# Secure Git MCP design

This document defines a standalone derivative of the upstream Git MCP package, with project policy enforced inside its tools. It implements the user's directory/module restrictions, regex configuration, Docker deployment, and an extension point for future output filters.

## Goals and decisions

- Extract `src/git` from `modelcontextprotocol/servers` at commit `ef9808f71090d0db3eda99e2af2ba78f1086f4fb` into the existing empty `secure-git-mcp` checkout. Preserve upstream notices, license, attribution, and provenance. This is a standalone source derivative, not a GitHub fork of the full monorepo.
- Keep all 12 upstream tools, including writes, as requested by the user.
- Enforce policy inside the tools against the actual repository, as selected by the user.
- Retain Python, GitPython, MCP stdio, and uv from upstream. Provide a distinctly named `secure-git-mcp` entry point.
- No pushes, registry publications, or GitHub-side repository changes are part of this implementation.

## Approaches

The user chose enforcement inside the original repository's tools: structured path checks preserve real commits and normal Git operations. A separate sanitized repository would provide stronger isolation but rewrite history and require write synchronization. Plain line-based output filtering was rejected because it cannot safely isolate patches, renames, or raw Git objects.

## Configuration

Use the standard `.mcp.json` `mcpServers.secure-git.env` configuration to pass `SECURE_GIT_POLICY` as a JSON string containing `deny` (an array of regex strings). The server accepts the server name `secure-git` by default. An optional `--mcp-config /path/.mcp.json --server-name secure-git` reads that same env entry directly, for clients that do not inject env. `--repository` is required and fixes the single allowed repository; tool calls cannot select other or nested repositories.

Example policy value: `{"deny":["^private(/|$)","^modules/payments(/|$)"]}`. A module means a repository-relative directory or file, not a language import graph. Match regexes using search against case-sensitive normalized POSIX paths and each ancestor directory, so denying a directory denies all descendants. Reject traversal and invalid paths. Git metadata is never readable as file content through the server. Invalid or missing policy configuration must prevent startup; an explicit `{"deny":[]}` allows all ordinary paths. Load policy once at startup, independently of tool arguments. Operators must keep policy and launcher configuration outside the agent's writable scope if enforcement is to be trusted.

## Read enforcement

- Status uses structured/NUL-delimited Git records. Hidden paths are omitted, including untracked directories and both sides of renames. Avoid raw branch/tracking summaries from Git status.
- Diffs are filtered by complete file changes, checking old and new paths. A denied side suppresses the entire change, never individual lines. Disable external diff/textconv, rename copy inference across protected paths, and submodule expansion. Preserve allowed changes, including additions and deletions.
- `git_show` resolves a commit or a commit-plus-path. Denied files return a policy error. Trees return only permitted entries; commit patches use the same filtered diff machinery. Raw tree/blob IDs and unprovable object expressions are denied, avoiding pathless access. Symlinks and gitlinks are not followed to content.
- History omits commits touching only denied paths. Mixed commits may show permitted diffs, but their free-form commit messages are withheld. Do not expose raw Git error output. Commit messages and authors for entirely permitted commits, branch names, and allowed-file contents are not automatically secret-scanned in this version.
- Errors carry a generic safe explanation and an explicit project-policy notice without leaking Git stderr, hidden file names, or hidden content. Partial results include `Some content is unavailable according to project policy.` Denials say `Access denied according to project policy.` The tool descriptions also explain that results are policy-filtered.

## Write enforcement

Validate every affected path before modifying persistent state; reject the entire request if any affected path is denied, with no partial writes.

- Add: accept literal repository-relative files/directories and `.`; expand them to actual tracked/untracked paths, validate all affected paths, and disable Git pathspec magic. Check symlink destinations and forbid escaping or denied targets. Do not silently stage only part of a requested directory.
- Commit: inspect the complete staged change set against HEAD (or the empty tree for an unborn branch), including both sides of renames. Reject any denied paths or unmerged entries. Reject an in-progress merge/rebase/cherry-pick and autostash restoration where effects exceed the validated staged set.
- Reset: inspect all staged changes before unstaging; refuse when restricted paths are affected. Preserve working-tree contents.
- Checkout: inspect the complete difference between current and target trees; refuse changes to denied paths, including renames. Reject in-progress operations and submodule updates; avoid hooks and external commands. Reject checkout on an unborn branch if the effects cannot be established.
- Create branch: validate names and resolve base to a commit, without changing working files. Do not interpolate user text into shell commands.
- Serialize requests within the process to avoid overlapping validation/writes. External simultaneous repository writers and malicious local Git configuration are outside the isolation guarantee; document that the server must use a trusted repository and exclusive write access. Disable hooks for operations initiated here.

## Filter extension

Use a typed Python output-filter protocol with ordered composition. The path policy works on structured Git data first; filters then receive the safe rendered response and tool context. Apply this pipeline to every successful result and anticipated error. A filter failure yields a fixed safe error, never unfiltered output. Provide a small example custom redactor and a test demonstrating that it runs across multiple tools. Dynamic plugin loading from agent-controlled config is excluded; future token/key filters can be added by server integrators through this interface.

## Docker and development

Provide a root Dockerfile, `.dockerignore`, and complete `.mcp.json` examples for native and Docker stdio invocation. Docker uses `-i`, no TTY, binds only the intended repository and trusted policy, disables networking at runtime, and runs under a configurable non-root UID/GID. Document the distinction between Docker mount paths and host paths, Git ownership checks, and read-write mounts needed for write tools. No secrets belong in the build context or image.

Retain upstream regression tests and add policy tests over real temporary repositories, protocol-level tests, CLI/config validation, and Docker functional smoke coverage. Verify native installation, tests, lint/type checks where supported, package build, and Docker build plus a real MCP request. Diagnose upstream/environment failures separately. Save reusable environment installation/start instructions after implementation if needed.

## Limits and non-goals

This is an MCP-tool access boundary, not a sandbox for other agent tools. Copies of denied content already present in allowed files are outside directory policy. Automatic token/key detection, language-aware module resolution, remote HTTP hosting, dynamic config reloading, GitHub-side fork creation, and image publication are out of scope. Commit metadata from mixed or hidden commits is withheld rather than claiming arbitrary free-text information can be classified by path. New-task restoration and deployment outside this cloud machine require separate verification.

## User clarification: Claude deny-list inheritance

The project-level `<repository>/.claude/settings.json` supplies `sandbox.filesystem.denyRead` paths/globs. The user selected fallback to the existing MCP regex policy when the Claude file is absent. An existing malformed/unreadable settings file remains a startup error; a missing denyRead section is an empty inherited list. Combine denies additively; neither source re-allows the other. Keep existing explicit MCP-policy requirements and env/config precedence. Read the project file at startup against the actual repository root, not process cwd, and freeze the result for both CLI and Python integrations. Do not import user/global/local Claude settings or allowRead exceptions.

Inherited literal paths and `*`, `**`, `?`, and bracket globs use path semantics rather than regex semantics. Relative paths are rooted at the served repository, `~` expands in the server's account, and absolute paths retain their meaning in the server/container filesystem. Match logical Git paths, including history and absent files, rather than expanding only existing files. Directory matches deny descendants. Preserve literal spellings as well as glob interpretation, and resolve literal path prefixes to cover existing symlink aliases. Reject unsupported/ambiguous patterns rather than silently ignoring restrictions. Docker users should prefer repository-relative rules; host absolute paths need matching container mounts. Configuration must remain operator-controlled.

This remains a deny-list import, not a reproduction of every platform-specific Claude sandbox behavior. Missing-file fallback is intentional; operators who rely on inheritance must ensure the settings file is included in the repository mount. Existing Git enforcement, policy notices, fixed-repository boundary, and future output-filter interface are unchanged.
