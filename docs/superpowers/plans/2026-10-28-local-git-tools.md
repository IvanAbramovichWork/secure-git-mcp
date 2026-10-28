# Local Git Tools Implementation Plan

> Execute natively with TDD. The user explicitly chose these eight local features, no additional design approval round, and one feature per commit. Include tests/docs/helpers with their owning feature; no scaffolding commit.

**Goal:** Make local Git investigation and targeted updates available through typed, policy-protected MCP tools.
**Architecture:** Extend pinned SecureRepository, typed MCP registration, structured path validation, and existing output filters. Reuse shared selection helpers; do not expose a raw Git executor.
**Tech stack:** Existing Python/Git/uv stack; no new runtime dependencies.
**Spec:** `docs/superpowers/specs/2026-10-28-local-git-tools-design.md`.

## Global constraints

Preserve optional repository defaults, additive immutable deny policy, full-file filtering, old/new rename checks, symlink/gitlink guards, no hooks/external diff/textconv, literal paths, process locking, and safe errors. Existing reference helpers remain unchanged. No history rewriting or network tools in this batch. Use the existing cloud checkout.

## Review focus

- Filenames containing tabs/newlines, Unicode, leading options, and pathspec syntax must stay literal.
- File/directory transitions and symlink ancestors must never introduce protected descendants.
- Historical filenames and blame metadata can refer to denied paths even when the current name is permitted.
- No-match grep is success; failed commands, invalid inputs, and output-filter failures must not leak raw diagnostics.
- Unstaging/restoring permitted selections must preserve unrelated protected index/worktree state, including intent-to-add and unborn HEAD.

## Per-feature sequence

For each: write behavioral backend/MCP tests, run RED, implement minimally, run focused and full suites, lint/type checks as appropriate, document, commit. Retain a ledger under `.superpowers/sdd/local-git-tools`. At the end run package/native/Docker checks and a fresh security review; fold necessary fixes into feature commits without rewriting pre-existing commits.

- [x] **1 Accurate errors.** Add fixed server-owned codes for policy, repository, argument, ownership, timeout and Git failures. Verify raw SDK arguments/stderr cannot spoof or leak, and custom filters still run.
- [x] **2 git_ls_files.** Implement shared literal path selectors and regular-file enumeration (index or commit; optional nonignored untracked). Return escaped names plus partial notice; test hidden paths, modes, malformed selectors and historical files. Expose typed `paths`, `revision`, `include_untracked`, `repo_path` inputs.
- [x] **3 git_grep.** Search permitted tracked working files or a revision using literal/extended-regex pattern, literal paths and bounded results. Decode NUL records, skip binaries and symlink ancestry, disable external transforms, and treat Git exit 1 as no match. Reuse enumeration/selection, never search unvalidated recursive prefixes.
- [x] **4 git_blame.** Read permitted committed file lines with bounded optional line range. Parse porcelain filenames/provenance, suppress denied historical sources and redact mixed-commit metadata. Test renames, root commits and quoted filenames; register typed inputs.
- [x] **5 Path-filtered git_log.** Add optional literal paths; filter structured commits by permitted selected changes. Preserve dates/count defaults and redact mixed metadata; test additions/deletions/renames.
- [x] **6 git_diff_revisions.** Accept base/target commits, optional literal paths and context; reuse exact-file patch rendering. Test two-revision semantics, protected rename sides, binary/unusual names and file/directory transitions.
- [x] **7 git_unstage.** Expand nonempty literal selections against index/HEAD and validate the complete selected set including ITA. Handle unborn HEAD, preserve working files and unrelated protected index entries, refuse conflicts/gitlinks/symlinks.
- [x] **8 git_restore.** Restore explicitly named regular files from index/commit to worktree/index under documented defaults. Refuse directory selectors and ambiguous type transitions; validate source, index, filesystem and ancestors before writes. Mark destructive; test denied requests leave all state unchanged.
