# Upstream provenance

This standalone derivative imports `src/git` from https://github.com/modelcontextprotocol/servers at commit `ef9808f71090d0db3eda99e2af2ba78f1086f4fb` (2026-10-28). `LICENSE` preserves the package's MIT notice; `UPSTREAM-LICENSE` preserves the repository-wide licensing transition notice and licenses.

Local changes add path policy, guarded read/write tools, output-filter composition, a secure CLI, deployment documentation, Docker integration, and security tests. Upstream backend and protocol regression tests are retained; their fixtures use the upstream implementation directly, while new tests cover the secured public entry points.
