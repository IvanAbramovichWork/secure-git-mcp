# Secure Git MCP

A standalone derivative of the [MCP Git reference server](https://github.com/modelcontextprotocol/servers/tree/main/src/git) that enforces project path policy before returning Git output or changing repository state. All 12 upstream tools remain available. Restricted content produces an explicit policy notice; denied writes are rejected as a whole.

See [upstream provenance](UPSTREAM.md), [the approved design](docs/superpowers/specs/2026-10-28-secure-git-design.md), and the preserved [package license](LICENSE) and [upstream licensing notice](UPSTREAM-LICENSE).

## Quick start with Docker

Build locally (the image has not been published):

```sh
docker build -t secure-git-mcp:local .
```

Copy [examples/docker.mcp.json](examples/docker.mcp.json) to your MCP client's `.mcp.json`, changing the absolute project path and numeric UID:GID. A complete configuration is:

```json
{
  "mcpServers": {
    "secure-git": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i", "--network=none",
        "--user", "1000:1000",
        "--env", "SECURE_GIT_POLICY",
        "--mount", "type=bind,src=/absolute/path/to/project,dst=/repo",
        "secure-git-mcp:local"
      ],
      "env": {
        "SECURE_GIT_POLICY": "{\"deny\":[\"^private(/|$)\",\"^modules/payments(/|$)\"]}"
      }
    }
  }
}
```

The client's `env` entry sets the Docker process environment; `--env SECURE_GIT_POLICY` forwards it into the container. The image starts in `/repo` and discovers the mounted repository automatically; no `--repository` argument is needed. Docker still requires `dst=/repo`, but only `src` needs customizing. Tool calls can omit `repo_path` entirely; `git_status({})` uses the pinned repository. Use `-i` without `-t`: MCP runs over stdio, not an HTTP port. Runtime networking is disabled; local Git operations do not need it. Network access is needed while building dependencies.

The image defaults to UID:GID `1000:1000`. Set `--user` to the repository owner's output from `id -u` and `id -g` on Linux, and ensure the working tree and Git directory are readable/writable by that identity. Git commits require a locally configured author name and email. Do not bypass Git ownership checks with `safe.directory=*`; fix ownership or explicitly trust the one intended repository in operator-controlled Git configuration. Linked worktrees also need their external Git metadata mounted at the correct location; a normal standalone clone is easiest to deploy. A read-only bind mount permits reads but cannot support write tools.

To keep policy in an external trusted `.mcp.json` instead of forwarding env, mount it read-only at `/config/.mcp.json` and append `--mcp-config /config/.mcp.json` after the image name. This file has the same `mcpServers.secure-git.env.SECURE_GIT_POLICY` structure shown above. Mount only the intended working tree and required trusted configuration.

## Native development and use

Requires Git, [uv](https://docs.astral.sh/uv/), and Python >=3.10. `.python-version` pins the upstream development interpreter to Python 3.10; Docker uses Python 3.12.

```sh
uv sync --frozen --dev
SECURE_GIT_POLICY='{"deny":["^private(/|$)","^modules/payments(/|$)"]}' \
  uv run --frozen secure-git-mcp --repository /absolute/path/to/project
```

For an MCP client, use [examples/native.mcp.json](examples/native.mcp.json). It runs this checkout with `uv --directory /absolute/path/to/secure-git-mcp run --frozen secure-git-mcp ...`. Installing the upstream package from PyPI is not an installation of this derivative: use this checkout or its built wheel.

The compatibility command `mcp-server-git` and `python -m mcp_server_git` both invoke the same secured CLI. `--repository` is optional and defaults to the startup working directory; an explicit value overrides it. The image working directory defaults to `/repo`. When the startup path points inside a repository, it resolves to its working-tree root and pins that repository. If the default directory is not in a Git working tree, startup fails rather than searching other mounts. Every tool accepts omitted or null `repo_path` to use that fixed root; explicit nonempty paths must identify the same root (a real filesystem alias is accepted). Other and nested repositories remain inaccessible. Existing clients sending the correct explicit path continue to work.

The native `uv --directory` option changes the working directory: keep an explicit `--repository` when launching from the server checkout to serve a different project. To run directly from the target repository using an installed executable, use `SECURE_GIT_POLICY='{"deny":[]}' secure-git-mcp`.

Optional arguments:

```sh
uv run --frozen secure-git-mcp \
  --repository /absolute/path/to/project \
  --mcp-config /trusted/location/.mcp.json \
  --server-name secure-git
```

`--mcp-config` explicitly selects policy from that file instead of process env; an invalid selected file never falls back to another source. `--server-name` defaults to `secure-git`. Missing/malformed policy prevents startup. An explicit `{"deny":[]}` allows ordinary paths, while metadata/path-safety restrictions remain enforced. Policy is loaded once per server startup.

## Regex policy

`SECURE_GIT_POLICY` is a JSON object with exactly one key, `deny`, containing regex strings. Within `.mcp.json` this JSON object is encoded as an environment-variable string.

| Regex | Meaning |
| --- | --- |
| `^private(/|$)` | Deny the root `private` directory and descendants |
| `^modules/(payments|billing)(/|$)` | Deny two modules by their directory paths |
| `(^|/)secrets(/|$)` | Deny any directory named `secrets` |
| `\\.pem$` in JSON source | Deny filenames ending in `.pem` (regex `\.pem$`) |

Matching uses case-sensitive regex **search**, applied to each normalized POSIX repository-relative path and all its ancestor directories. Thus `^private$` also denies `private/a/b.txt`; `private` without anchors may also match `notprivate`. Modules are paths, not language-aware import dependencies. Use `/` on every platform. Invalid regexes, unknown policy keys, traversal, absolute paths, NULs, and `.git` components fail closed. Git pathspec magic is never accepted as pattern syntax for `git_add`: filenames are literal.

Keep policy and launcher configuration under operator control, outside the agent's writable scope. Configure any other shell, filesystem, or MCP tools separately: this server cannot restrict those tools or stop them modifying a policy file and restarting a process.

## Inherit Claude project deny paths

The server automatically reads `<repository>/.claude/settings.json` at startup and imports `sandbox.filesystem.denyRead`. Additional MCP `deny` regexes still apply: a match in either source denies access, including writes and historical Git reads.

```json
{
  "sandbox": {
    "filesystem": {
      "denyRead": ["private", "modules/payments/**", "**/.env", "**/*.pem"]
    }
  }
}
```

If the Claude file is absent, the server falls back to MCP rules. Missing `sandbox`, `filesystem`, or `denyRead` sections mean no inherited denies. An existing malformed/unreadable file or dangling settings symlink prevents startup. Explicit MCP policy remains required; use `{"deny":[]}` to add no extra regex restrictions. Policy is frozen until restart, including the missing-file fallback.

Inherited entries are paths/globs, never regexes: `*` stays within one path component, `**` crosses directories, `?` matches one non-separator, and bracket sets are supported. A directory match denies its descendants. Matching includes dotfiles and historical/absent paths; it does not expand only files currently on disk. Literal spellings are also denied, and existing literal symlink prefixes are resolved to cover target aliases. Unsupported backslash patterns, unresolved home names, globs ending in `/`, and `..` after wildcard components fail closed. Relative paths resolve from the served repository root; absolute paths and `~` use the server's filesystem/account. Prefer relative paths in Docker: host absolute paths do not automatically map to `/repo`.

This imports only the project's deny list, not the complete platform sandbox policy: `allowRead` exceptions cannot remove these denials, and user-level settings and `settings.local.json` are not imported. Keep the project settings file operator-controlled. With Docker, include `.claude/settings.json` in the repository mount; an omitted file intentionally activates MCP-only fallback.

## Tool behavior

| Tool | Enforcement |
| --- | --- |
| `git_status` | Structured per-file records; omit protected paths and either side of protected renames. No raw branch/upstream summaries. |
| `git_diff_unstaged`, `git_diff_staged`, `git_diff` | Check old and new paths, then render complete permitted file changes. External diff, textconv, and submodule expansion are disabled. |
| `git_show` | Accept commits and `commit:path`. Filter commit patches and directory listings. Deny protected paths, raw blob/tree IDs, index-stage expressions, and symlink/gitlink content. |
| `git_log` | Omit hidden-only commits; withhold messages/authors for mixed commits. Permitted commit metadata remains visible. |
| `git_branch` | List local/remote/all branch names, with optional commit filters. Names are not secret-scanned. |
| `git_add` | Expand literal files/directories or `.`; refuse the whole selection if any affected path or resolved symlink target is restricted. Embedded repositories and gitlinks are refused before staging. |
| `git_commit` | Refuse a staged set containing any restricted path, symlink, gitlink, or conflict. Initial commits are supported. |
| `git_reset` | Refuse when restricted entries are staged, including intent-to-add entries. Otherwise unstage everything, preserving working files. |
| `git_create_branch` | Validate branch/base and create the ref without changing files. |
| `git_checkout` | Refuse changes involving restricted tree paths, symlinks, gitlinks, or restricted staged changes. No forced checkout, overwriting ignored files, hooks, or recursive submodule updates. |

Write operations refuse in-progress merge, rebase, cherry-pick, revert, autostash, or sequencer state. All calls are serialized within the process; this cannot lock out external Git processes. Hooks are disabled for server-initiated commands. Git command failures and argument-validation failures return safe generic errors rather than stderr or echoed argument values.

Partial results include **“Some content is unavailable according to project policy.”** Denied calls return `isError: true` with **“Access denied according to project policy.”** Errors now carry safe codes: `POLICY_DENIED`, `REPOSITORY_MISMATCH`, `INVALID_ARGUMENTS`, `GIT_OWNERSHIP`, `GIT_TIMEOUT`, `GIT_UNAVAILABLE`, `GIT_FAILED`, `OPERATION_REFUSED`, or `INTERNAL_ERROR`. A repository mismatch tells the client to omit `repo_path`; ownership errors suggest matching the repository owner UID/GID. Raw arguments, paths and Git stderr remain withheld. Custom output filters also process these error messages.

Path policy does not classify arbitrary copied text. Copies of protected content in permitted files, author names/messages on entirely permitted commits, and branch names are not automatically redacted. Similarity-based rename detection checks both paths; a copy or heavily rewritten file at an allowed path is governed by that allowed path. Do not treat directory rules as automatic secret detection or an OS sandbox. Use a trusted repository/Git configuration and exclusive access for writes. Git attribute filters and signing/credential helpers must be controlled by the operator; runtime networking is disabled in the Docker example.

## Additional local tools

`git_ls_files(paths=None, revision=None, include_untracked=False)` lists permitted regular files, using the index by default. A commit revision selects historical files; optional untracked files exclude ignored entries and cannot be combined with a revision. Selectors are literal file/directory paths (`.` selects the root), not globs. Names are JSON-escaped, and protected/symlink/submodule entries are omitted with a policy notice.

`git_grep(pattern, paths=None, revision=None, regex=False, max_count=1000)` searches permitted tracked working files or a commit. Patterns are literal by default; `regex=true` selects extended POSIX regex. Results include JSON-escaped filename, line number and match text. No matches is successful; binaries, symlinks, submodules, ignored/untracked files and external transforms are excluded. Results are bounded and truncation is marked.

`git_blame(path, revision="HEAD", start_line=1, end_line=None, max_count=500)` returns JSON line records with commit, line number and source text. Protected historical filenames (including rename origins) suppress the affected lines. Author/summary fields are withheld for originating commits that touch denied paths. Raw blobs, symlinks, submodules, and cross-file copy/move detection are not supported. A policy/truncation notice may follow the JSON records.

`git_log(..., paths=None)` accepts literal file/directory selectors, including deleted historical paths. It selects commits by permitted old/new changes, applies the visible result count after selection, and retains mixed-commit metadata redaction. It does not automatically follow a file through all previous rename names; a rename is selected by either permitted side.

`git_diff_revisions(base, target, paths=None, context_lines=3)` compares two committed trees independently of working-tree changes. Literal paths filter complete old/new changes, with both rename sides checked. The existing `git_diff(target)` continues comparing a target commit with the working tree.

`git_unstage(files)` resets only a nonempty literal file/directory selection in the index. Working files and unrelated staging—including protected entries—are preserved. Selected denied paths, symlinks, gitlinks and conflicts are refused before mutation. A nested selection that would replace an unselected ancestor index entry is also refused. Unborn repositories and intent-to-add entries are supported. The older `git_reset` remains an all-index operation.

`git_restore(files, source=None, staged=False, worktree=None)` destructively restores explicitly named regular files. By default it restores the working file from the index. `staged=true` defaults to restoring only the index from HEAD; set `worktree=true` as well to restore both. An explicit `source` selects a commit. Directory selectors (including `.`), ambiguous type transitions, symlinks, submodules, conflicts and protected selections are refused. Source/index/actual filesystem paths are checked before mutation; unrelated files and staging are preserved. Unborn repositories support index-to-worktree restore and index-only unstaging.

## Extending output filtering

Path policy operates on structured Git data first. Trusted server integrators can then compose output filters, including future token/key redactors:

```python
from pathlib import Path
from mcp_server_git.filters import FilterContext
from mcp_server_git.policy import PathPolicy
from mcp_server_git.secure_server import create_server

class Redactor:
    def filter(self, text: str, context: FilterContext) -> str:
        return text.replace("EXAMPLE_TOKEN", "[REDACTED]")

server = create_server(
    Path("/repo"),
    PathPolicy.from_json('{"deny":["^private$"]}'),
    filters=[Redactor()],
)
# Run with: await server.run_stdio_async()
```

See [examples/redactor.py](examples/redactor.py) for a regex example. Filters run in order on successful tool results and anticipated tool errors, with `tool_name` and `is_error` context. If any filter fails or returns a non-string, the entire output is withheld. The default CLI performs path filtering only; it does not automatically load custom code from `.mcp.json`. Automatic token/key detection is a future extension, not a feature claimed by this version.

## Verification

```sh
uv sync --frozen --dev
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pyright --pythonpath .venv/bin/python
uv build
uv run --frozen python scripts/smoke.py
docker build -t secure-git-mcp:local .
uv run --frozen python scripts/smoke.py --docker secure-git-mcp:local
```

The smoke check creates a disposable repository, starts without `--repository`, makes real MCP requests over stdio with omitted `repo_path` (and checks explicit-path compatibility), verifies permitted/denied reads, stages and commits permitted files, and checks that denied staging leaves the index unchanged. Docker verification uses a non-root UID/GID and `--network=none`. If your Docker daemon can only bind directories under a shared path, use `scripts/smoke.py --docker secure-git-mcp:local --parent /path/shared/with/daemon`.

If your home is read-only (as in this cloud environment), set writable tool caches first:

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
export UV_PYTHON_INSTALL_DIR=/workspace/.cache/uv-python
export DOCKER_CONFIG=/workspace/.cache/docker
```

In this cloud, Docker builds also need the proxy hostname mapping and public CA bundle. Use `uv run --frozen python scripts/build_cloud_image.py` to pass them through supported Docker build arguments and a temporary BuildKit CA mount. No proxy credential or cloud CA is baked into the image; TLS and package-signature checks remain enabled. This build helper uses host networking for installation only; runtime smoke checks still disable networking.

Upstream reference helpers remain in `server.py` to preserve backend/protocol regression coverage. The public CLI and `secure_server.py` are the enforced interface; integrators should use `secure_server.create_server`, not instantiate the historical upstream reference server. No image/repository is pushed automatically.
