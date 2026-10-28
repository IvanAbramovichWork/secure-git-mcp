# Secure Git MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a standalone Git MCP derivative enforcing regex path restrictions on all reads and writes, with Docker deployment and extensible output filtering.

**Architecture:** Extract the pinned upstream Git package into this checkout. Route every tool through a repository-bound policy service that examines structured paths before rendering reads or executing writes; apply an ordered output-filter pipeline at the MCP boundary. Keep upstream helpers and regression tests where their semantics remain applicable, adapting entrypoint/protocol expectations to intentional security changes.

**Tech Stack:** Python >=3.10, GitPython, MCP Python SDK >=2.3,<3, Click, uv, pytest, Docker.

**Spec:** `docs/superpowers/specs/2026-10-28-secure-git-design.md`

## Global Constraints

- Upstream source: `modelcontextprotocol/servers`, commit `ef9808f71090d0db3eda99e2af2ba78f1086f4fb`, subtree `src/git`; preserve source/license notices and record modifications.
- Keep all 12 upstream tools, including writes. No pushes, image publication, or GitHub-side changes.
- Use the current isolated cloud checkout; do not create another worktree.
- Require a fixed `--repository` and explicit policy, including an explicit empty deny array for unrestricted ordinary paths.
- Read `SECURE_GIT_POLICY` from process env or `mcpServers.<server-name>.env.SECURE_GIT_POLICY` in an explicitly selected `.mcp.json`. `--server-name` defaults to `secure-git`; explicit `--mcp-config` selects that file rather than silently falling back to env on failure.
- Case-sensitive regex search matches normalized repository-relative POSIX paths and ancestors; Git metadata and invalid paths remain denied even with no deny patterns.
- Whole-request write denials must leave index, HEAD, refs, and working files unchanged. Never expose raw Git stderr or unrestricted data when filtering fails.
- Partial notice: `Some content is unavailable according to project policy.` Denial: `Access denied according to project policy.`
- Tool access is not an OS sandbox. Trusted policy/configuration, trusted Git configuration, and exclusive external write access are deployment requirements.

## Review Focus

- Renames across the policy boundary: suppress the complete rename and deny writes affecting either side; test in Tasks 2 and 3.
- Pathless objects and nested repositories: forbid blob/tree IDs, index-stage expressions, nested repo roots, symlink escapes, and traversal; test in Tasks 1 and 2.
- Worktree/index edge states: unborn HEAD, unmerged index, merge/autostash/rebase/cherry-pick state must not circumvent validation; test in Task 3.
- Output-channel failures: hidden-only/mixed commits, quoted/newline/non-ASCII filenames, binary changes, and raised Git/plugin exceptions must not leak hidden content; test in Tasks 2 and 4.
- Container policy and filesystem permissions: injected env vs explicit config, UID/GID ownership, read-write mounts, and disabled runtime networking must work over actual MCP stdio; test in Task 5.

---

### Task 1: Upstream extraction, policy configuration, and repository boundary

**Files:** Import upstream package/source/tests/lock/license at repository root; add `UPSTREAM.md`, `src/mcp_server_git/policy.py`, `src/mcp_server_git/repository.py`, and `tests/test_policy.py`. Modify `pyproject.toml` and `src/mcp_server_git/__init__.py` as integration proceeds; retain the upstream distribution metadata name initially so its version lookup and regression tests remain valid.

**Interfaces:** `PolicyError(Exception)`; immutable `PathPolicy.from_json(value: str) -> PathPolicy`; `PathPolicy.allows(path: str) -> bool`; `load_policy(config_path: Path | None, server_name: str = 'secure-git') -> PathPolicy`; `SecureRepository(root: Path, policy: PathPolicy)` exposing `.repo: git.Repo`, `.policy`, `validate_repo_path(path: str) -> None`, and a per-instance lock. Repository validation compares actual Git roots, not only lexical containment.

- [x] Import the exact upstream tree, preserve both applicable root and package license notices, record source SHA and local modification scope, and establish a clean regression baseline with `uv sync --frozen --dev` and `uv run --frozen pytest`. Diagnose any inherited failures before changing behavior.
- [x] Write policy/config tests: missing policy fails; `{"deny":[]}` permits normal paths; `^private$` denies descendants; `^modules/payments(/|$)` allows sibling modules; malformed JSON, unknown keys, non-string/invalid regexes, absolute/traversal/NUL paths, and `.git` components fail closed. Explicit config has deterministic precedence; an invalid selected config never falls back to env.
- [x] Run `uv run --frozen pytest tests/test_policy.py -q`; confirm failures reflect missing policy implementation.
- [x] Implement policy parsing and normalized ancestor checks; bind one real repository root, reject bare/other/nested repositories and symlink escapes, and capture immutable policy at startup.
- [x] Extend repository tests to cover legitimate root aliases, missing paths, linked worktree metadata, and nested Git roots; run policy tests and retained baseline tests. Commit the extraction and policy boundary.

### Task 2: Structured, policy-aware reads

**Files:** Add `src/mcp_server_git/read_tools.py`, `tests/test_policy_reads.py`; extend `repository.py` with validated Git execution and structured-change helpers.

**Interfaces:** `ReadTools(repository: SecureRepository)` provides `status() -> str`, `diff_unstaged(context_lines: int = 3) -> str`, `diff_staged(context_lines: int = 3) -> str`, `diff(target: str, context_lines: int = 3) -> str`, `show(revision: str) -> str`, `log(max_count: int = 10, start_timestamp: str | None = None, end_timestamp: str | None = None) -> str`, and `branch(branch_type: str, contains: str | None = None, not_contains: str | None = None) -> str`. A structured change stores old/new paths and change kind, independent of patch text; shared helpers validate refs to commits and obtain complete path sets for Task 3.

- [x] Write real-repository tests containing allowed and denied sentinel content across modified, added, deleted, staged, untracked, binary, renamed, and submodule paths. Assert allowed output survives, denied paths/content do not, and partial notices appear. Test quoted, newline, and Unicode filenames using NUL-delimited records.
- [x] Write `show`/history tests for allowed blobs and directory listings, denied files, raw blob/tree IDs, `:stage:path`, invalid ref options, `..` paths, symlinks/gitlinks, root commits, and hidden-only/mixed commits. Assert hidden-only entries disappear and mixed commit messages/authors are withheld; permitted metadata remains usable. Invalid arguments must not surface Git stderr.
- [x] Run `uv run --frozen pytest tests/test_policy_reads.py -q` and confirm meaningful failures.
- [x] Implement reads using structured status/diff records and path-bearing commit traversal. Filter whole changes before producing patches; explicitly disable external diff, textconv, copy inference and submodule expansion. Detect boundary-crossing renames before rendering; use literal path handling. Commit-plus-path reads resolve a commit first; pathless blob/tree reads are rejected.
- [x] Validate integer bounds and ref/date arguments; preserve legitimate branch/commit reads and an unborn repository's status/diffs. Run read and policy tests plus relevant upstream regression tests; commit policy-aware reads.

### Task 3: Atomic policy checks for all five writes

**Files:** Add `src/mcp_server_git/write_tools.py`, `tests/test_policy_writes.py`; extend shared repository helpers as required.

**Interfaces:** `WriteTools(repository: SecureRepository)` provides `add(files: list[str]) -> str`, `commit(message: str) -> str`, `reset() -> str`, `create_branch(branch_name: str, base_branch: str | None = None) -> str`, and `checkout(branch_name: str) -> str`. Reuse the complete old/new path sets from Task 2. Validation and execution hold the same repository lock; Task 4 must not double-acquire a non-reentrant lock.

- [x] Write tests taking before/after snapshots of index bytes/entries, HEAD, refs, and file contents. Denied direct adds, directory adds, `.`, staged mixed commits, resets, and boundary-crossing checkout must leave snapshots identical. Allowed writes must actually stage, commit, unstage, create, and switch branches.
- [x] Test pathspec magic and wildcard-looking literal names, option-looking input, renamed/deleted files, symlink targets into denied directories or outside root, unmerged stages, unborn commits/resets/checkouts, and merge/rebase/cherry-pick/autostash state. Verify disabled hooks cannot write a marker file and submodule changes cannot trigger traversal or updates.
- [x] Run `uv run --frozen pytest tests/test_policy_writes.py -q` and confirm meaningful failures.
- [x] Implement full affected-path checks before each operation. Expand literal add selections to actual files and reject the entire selection if any are denied; invoke Git with literal pathspecs and `--`. Commit only a validated nonempty staged set (or valid initial commit), skip hooks, and refuse in-progress operations. Reset only after checking the full staged set. Checkout only validated commit-tree changes with hooks/submodule recursion disabled. Branch creation validates the ref and base without checking out files.
- [x] Run write/read/policy tests and relevant upstream regression tests; verify allowed-file workflows still operate when untouched denied files exist. Commit guarded writes.

### Task 4: MCP integration, fail-closed output pipeline, and secure CLI

**Files:** Modify `src/mcp_server_git/server.py`, `__init__.py`, `__main__.py`, and `pyproject.toml`; add `src/mcp_server_git/filters.py`, `examples/redactor.py`, `tests/test_filters.py`, `tests/test_secure_protocol.py`, and `tests/test_secure_cli.py`. Adapt retained upstream fixture/entrypoint/protocol expectations for deliberate restrictions.

**Interfaces:** `FilterContext(tool_name: str, is_error: bool)`; `OutputFilter` protocol with `filter(text: str, context: FilterContext) -> str`; `FilterPipeline(filters: Sequence[OutputFilter])` with `apply(text: str, context: FilterContext) -> str`; `create_server(repository: Path, policy: PathPolicy, filters: Sequence[OutputFilter] = ()) -> MCPServer`; `serve(repository: Path, policy: PathPolicy) -> None`. All advertised tools retain upstream names and argument signatures. CLI `secure-git-mcp --repository PATH [--mcp-config PATH] [--server-name NAME] [-v]` is the documented entry point; remove any installed unrestricted CLI route, or route compatibility aliases through the secure CLI.

- [x] Write pipeline tests proving ordered custom redaction applies to success and error responses across multiple tools; a plugin exception must yield a fixed safe failure without original text. No dynamically imported plugin configuration is accepted.
- [x] Write MCP tests exercising initialization, all 12 tools, filtered descriptions, generic sanitized Git failures, invalid argument echoes, policy denials, partial notices, unknown tools, and serialization of overlapping write calls. Write subprocess CLI tests for missing/invalid policy, config selection, repository pinning, clean stderr/stdout separation, and module/console entry-point equivalence.
- [x] Run `uv run --frozen pytest tests/test_filters.py tests/test_secure_protocol.py tests/test_secure_cli.py -q` and verify failures.
- [x] Wire handlers to `ReadTools`/`WriteTools`, sanitize anticipated errors before filtering, and contain unexpected filter failures without unfiltered fallback. Validate startup before opening stdio, maintain MCP protocol compatibility, and route every installed entry point through required policy and repository checks.
- [x] Adapt upstream assertions only where secure behavior intentionally differs; retain backend regression coverage and document each change. Run the complete suite, Ruff, Pyright, and `uv build`; resolve application failures rather than weakening security assertions. Commit protocol/CLI integration.

### Task 5: Docker deployment, documentation, and end-to-end validation

**Files:** Modify `Dockerfile` and `README.md`; add `.dockerignore`, `examples/native.mcp.json`, `examples/docker.mcp.json`, and `scripts/smoke.py`; maintain `uv.lock` whenever dependency metadata changes.

**Interfaces:** Docker entry point is `secure-git-mcp`; container repository is `/repo`; trusted configuration can mount at `/config/.mcp.json:ro`. `scripts/smoke.py` drives MCP over stdio against either the installed native executable or a supplied Docker image, using real fixture history and checking allowed reads, denied reads, and allowed/denied writes.

- [x] Write the smoke check first; require allowed sentinel visibility, hidden sentinel absence, explicit policy notices, and unchanged repository state after denied writes. A nonzero child exit or failed assertion must fail the script.
- [x] Provide a locked multi-stage Docker build and non-root runtime with Git, minimal package/source copies, and no credential/build-cache inclusion. Examples use `docker run --rm -i --network=none`, a configurable numeric UID:GID, and explicit repository/config binds; never mount the whole host workspace unnecessarily.
- [x] Document native install/run, regex semantics and JSON escaping, all tool restrictions, metadata treatment, write denials, ownership handling, Git trust/exclusive access, extension API/example, and the distinction between MCP policy and other agent access. State this image is built locally, not published.
- [x] Run the documented frozen install and native smoke commands; then `docker build -t secure-git-mcp:local .` and the Docker MCP smoke check. Verify a real stdio functional result and allowed writes as the chosen non-root UID; diagnose any build or mount restriction without bypassing artifact/TLS verification.
- [x] Run final full pytest, Ruff lint/format checks, Pyright, and package build; inspect Git diff/status and ensure generated outputs/secrets are excluded. Obtain independent whole-branch review for policy bypasses and unexpected mutation, resolve findings, and rerun affected checks.
- [x] Save tested reusable cloud `install_script` and `start_skill` if needed; include working directories, startup policy requirements and readiness checks, and the instruction to use the existing isolated checkout. Report current-instance validation separately from saved drafts/publication. Commit deployment/docs and report test evidence and remaining limits.
