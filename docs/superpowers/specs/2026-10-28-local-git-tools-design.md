# Protected local Git tooling

This design extends the existing pinned-repository MCP server with the remaining local development tools. It follows the user's requested TDD workflow and one feature per commit, with tests and documentation included in each feature commit. Repository defaults are already implemented in `6929a27` and must not be reimplemented.

## Scope and approach

Continue the typed-tool architecture in Python using the existing Git runner, immutable policy, shared repository lock, and output-filter pipeline. Each operation validates structured paths before returning content or changing state. An unrestricted Git executor is excluded because arbitrary commands/flags can read pathless objects or affect unvalidated state. No new runtime dependencies, automatic policy weakening, remote publication, or changes to unrelated reference helpers are needed.

The local batch contains eight independently testable features/commits. History integration and remote operations require separate decisions about command scope, conflict handling, credentials, and network permissions; the current Docker runtime stays offline. Their scope remains pending the user's answer rather than being silently treated as implemented.

## Feature 1: actionable safe errors

Differentiate policy denial, repository-path mismatch, invalid tool arguments, Git ownership rejection, Git timeout/unavailability, and other refused operations. Messages and codes are fixed server-owned strings; never forward Git stderr, exception text, or echoed caller arguments. All error responses still pass through custom filters. Preserve `isError: true`. Root discovery and optional repo_path remain unchanged.

Only trusted handler-generated codes can select a specific safe message. SDK validation errors cannot spoof codes or inject response content. A protected path still produces the established policy-denial notice. Git ownership checks remain enabled; diagnostics suggest matching UID/GID without globally trusting repositories.

## Feature 2: git_ls_files

List policy-permitted repository-relative tracked files, optionally including untracked/nonignored files or selecting a commit revision. Optional literal path selectors restrict the result; `.` means repository root. Emit escaped filenames so whitespace/newlines cannot fake additional records. Omit protected paths with a partial-policy notice. Do not follow symlinks, enter submodules, expose metadata, or interpret selectors as Git glob/pathspec syntax.

Shared path-selection helpers introduced here must support logical old/new paths, directory ancestry and NUL-delimited records. Later features reuse them rather than each inventing a parser.

## Feature 3: git_grep

Search tracked working-tree files or files at an explicit commit. Accept a search string, optional literal path selectors, optional extended-regex mode (literal search by default), and bounded result count. Preselect permitted regular files before Git searches. Exclude binaries, symlinks, submodules, textconv and external tools; do not follow working-tree symlink ancestors. Pass search patterns as values, not flags. Treat a no-match exit as a successful empty result.

Render permitted matches with escaped filename, line number, and matching text. Use unambiguous machine records and exact-file selection, including file/directory transitions. Partial/truncated output is explicitly marked; protected match counts are never exposed. Existing filters apply to match text as usual.

## Feature 4: git_blame

Blame one permitted file at an explicit revision (default HEAD), with optional line range and a bounded response. Reject nonregular/pathless objects. Parse porcelain records and provenance filenames before rendering. Do not enable cross-file copy/move inference; suppress provenance from denied historical paths, including automatic whole-file rename history. Withhold author/message metadata when the originating commit touches denied paths, consistent with existing log/show behavior. Handle C-quoted filenames, Unicode, newlines, and root commits safely.

## Feature 5: path-filtered git_log

Add optional literal file/directory selectors to the current log tool. Preserve existing default traversal and date/count behavior. Select commits from structured changes, applying full policy checks before showing metadata; commits with no permitted selected changes are omitted. Mixed commits retain metadata redaction. Historical additions, deletions, renames and file/directory replacements must work. Automatic follow-across-renames is not part of this feature.

## Feature 6: git_diff_revisions

Add an explicit two-commit comparison tool with base, target, context lines, and optional literal path selectors. Both revisions must resolve to commits. Reuse structured change filtering and exact-file rendering, checking both sides of every rename. Keep the existing target-to-working-tree git_diff behavior unchanged. Support root commits, binary files, unusual filenames, deletions and permitted file/directory transitions without recursive pathspec leakage.

## Feature 7: git_unstage

Unstage a nonempty literal selection of files/directories, leaving working files and unrelated index entries intact. Expand the complete affected index/HEAD selection before mutation; reject the whole request if any selected path is denied, a symlink/gitlink, or unmerged. Include intent-to-add entries and unborn repositories. Allow unstaging permitted files even when unrelated denied entries are staged. Disable broad index refresh/side effects, hooks and pathspec interpretation.

## Feature 8: git_restore

Restore explicitly named regular files from index or an explicit commit, to working tree and/or index with standard documented defaults. This is a destructive tool and its annotation must say so. Start with explicit file paths; directory selectors, `.` and ambiguous file/directory transitions are refused. Check policy, source/index modes, actual working-tree objects, and every ancestor before execution. Denied selections must preserve index, HEAD, refs, and all working files. Never overwrite a protected descendant, traverse a symlink, update a submodule or perform conflict resolution. Index-only restoration can reuse unstaging helpers but must preserve untouched entries.

## Validation and delivery

For each feature: add meaningful failing backend/protocol tests; watch them fail; implement; run targeted and full suites; run appropriate lint/type checks; commit once with tests and docs. No separate scaffolding/refactor commit. Reuse the existing cloud checkout. Include tests for filename controls, option-looking inputs, traversal, denied old/new paths, raw object attempts, mixed metadata, conflict state, and failed writes preserving state. The final native/Docker smoke checks must exercise representative new read/write tools, and an independent review checks the complete delta before completion.

Each final commit should be independently buildable and testable. Findings in later review should be folded into their feature commit where possible, preserving the user's one-feature/one-commit requirement. Do not amend pre-existing user commits or force-push shared history. No GitHub push or registry publication is included in this batch without explicit authorization.
