# Security review and validation

This record describes the independent review of the secured Git MCP implementation and the regression tests used to verify its fixes. It distinguishes reviewed code from later verified fixes and states the access boundary that was retained.

## Review findings resolved

The independent reviewer inspected `8cda094..449eab9` read-only and reproduced four issues using disposable repositories. That revision was not approved for merge. All four findings were then addressed in one regression/fix pass; there was no second independent review.

| Finding | Correction | Regression evidence |
| --- | --- | --- |
| A literal diff pathspec for an allowed file recursively included a denied child during a file/directory transition. | Render each validated file side using an internally constructed literal include and literal descendant exclusion. Clear inherited glob/case flags for these commands. Render rename sides separately so excluding an old parent's descendants does not hide a permitted new child. | Six cases covering staged diffs, comparison with HEAD, and commit show, in both transition directions; an additional allowed-rename case. |
| Checkout could delete ignored denied working files while replacing their directory with an allowed file. | Disable overwriting ignored files with `--no-overwrite-ignore`; Git refuses the checkout and leaves HEAD/index/files intact. | Reproduce the ignored denied descendant and compare HEAD, exact index bytes, and file content after rejection. |
| Reset missed denied intent-to-add index entries. | Include `--ita-visible-in-index` in structured change validation and staged rendering. | Reproduce `git add -N` on a denied path, require rejection and identical index bytes, and verify a partial-policy notice. |
| Add accepted gitlinks/embedded repositories that subsequent commit/reset tools refused. | Reject selected cached gitlink modes and embedded repository directories before staging. | Test both new embedded repositories and changed tracked gitlinks, requiring rejection with unchanged index. |

All reproductions failed before their corresponding fixes, then passed. The final suite includes 11 review regressions. The initial upstream baseline ran 186 tests; final validation ran 261 tests successfully, including native protocol, policy, write safety, and upstream regressions. Ruff lint/format, Pyright with the project interpreter, and wheel/source-package builds passed. Native and Docker MCP stdio smoke checks exercised permitted reads/writes, filtered diffs, and unchanged index after denied staging. Docker ran as UID:GID `1000:1000` with `--network=none`. Cloud builds retained TLS, lockfile artifact-hash, and Debian signature verification.

## Decisions retained after review

- The secured registration is in `secure_server.py`; historical upstream helpers and registration remain in `server.py` for reference regression coverage. All installed console/module entry points invoke the secured CLI. Integrators must use `secure_server.create_server`; importing the historical factory would bypass policy.
- Copies or heavily rewritten content at an allowed path, branch names, and metadata on wholly allowed commits are governed by their allowed location, not automatic secret scanning. Directory policy alone cannot detect copied keys; the trusted output-filter interface is the extension point.
- Git configuration/attribute helpers and launcher policy must be trusted, and external writers must be excluded while tools operate. Process serialization does not prevent a separate shell or process racing the checks or changing policy before restart.
- The pinned MCP stdio transport redirects inherited stdin to EOF; root-commit rendering was exercised by the smoke check. Review did not identify a public stdin defect. SDK upgrades must retain that behavior or adapt the root-rendering implementation.
- Upstream distribution identity/version remain for version-lookup compatibility. The documented command/image identify this derivative. Installing upstream from PyPI does not install these changes; publication under an upstream-owned package name is not part of this work.
- Docker evidence applies to this cloud machine, with its proxy/CA build helper and non-root bind mount. Different hosts still need correct repository mounts, ownership, and Git identity; current-machine success is not proof of deployment elsewhere.

No minor review findings were deferred. No remote branch, image registry, or environment version was published. Cloud installation/startup instructions were saved as a draft; review/save and publication remain product actions for the user.

## Claude deny-list inheritance follow-up

A separate read-only review of Task 6 found three important matcher issues: globstar did not span newline characters, literal repository/canonical prefixes could be interpreted as glob syntax, and lexical normalization could discard symlink traversal semantics before resolution. Each was reproduced with a failing test and fixed: DOTALL glob matching, escaped physical prefixes with only supplied glob components interpreted, and resolution of the uncollapsed literal prefix. Additional regressions cover canonical target names containing brackets and both read/write inheritance of historical paths.

The completed follow-up passes 297 tests, Ruff lint/format, Pyright, package build, and native plus non-root/offline Docker MCP smoke checks. Thirty-six new inheritance tests include absent-file MCP fallback, invalid/unreadable-file errors, additive rules, immutable snapshots, repository-root discovery, and supported glob forms. No minor review issues were deferred. This imports project denyRead only: exact platform sandbox behavior, allowRead exceptions, user/local/global settings, and mutation of operator-controlled configuration or aliases after startup remain outside this feature. The existing unrestricted legacy-helper boundary is unchanged.
