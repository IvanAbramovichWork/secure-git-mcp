# Repository Guidelines

## Project Structure & Module Organization

Python source lives in `src/mcp_server_git/`. The enforced interface is `secure_cli.py` and `secure_server.py`; policy, repository execution, path helpers, and read/write tools have separate modules. `server.py` preserves upstream reference behavior for regression coverage; use `secure_server.create_server` for secured integrations.

Tests live in `tests/`, with shared fixtures in `conftest.py` and protocol snapshots in `tests/snapshots/`. `examples/` contains client configurations and a redactor. `scripts/` contains smoke and cloud-image helpers; `docs/` holds security reviews, designs, and plans. Preserve provenance in `UPSTREAM.md`, `LICENSE`, and `UPSTREAM-LICENSE`.

## Build, Test, and Development Commands

- `uv sync --frozen --dev`: install locked runtime and development dependencies.
- `uv run --frozen pytest -q`: run the test suite.
- `uv run --frozen ruff check .`: check lint.
- `uv run --frozen ruff format --check .`: check formatting.
- `uv run --frozen pyright --pythonpath .venv/bin/python`: check types.
- `uv build`: build distribution artifacts with Hatchling.
- `uv run --frozen python scripts/smoke.py`: exercise real MCP requests against a disposable repository.
- `docker build -t secure-git-mcp:local .`: build the container image.

Run locally with `SECURE_GIT_POLICY='{"deny":[]}' uv run --frozen secure-git-mcp --repository /absolute/project/path`.

## Coding Style & Naming Conventions

Target Python 3.10+ compatibility. Use four-space indentation, Ruff formatting, type annotations, `snake_case` functions/modules, `PascalCase` classes, and uppercase constants. Keep policy checks in shared helpers so tools apply consistent restrictions.

## Testing Guidelines

Use pytest and pytest-asyncio; async tests run in automatic mode. Name files `test_*.py` and functions `test_*`. Use temporary Git repositories and shared fixtures rather than modifying the checkout. Cover permitted and denied operations, protocol responses, and unchanged state after refused writes. Run focused tests with `uv run --frozen pytest tests/test_policy.py -q`. Coverage tracks branches in `src/mcp_server_git`; no minimum percentage is configured.

## Commit & Pull Request Guidelines

Recent feature commits use `feat: <imperative description>`; follow that pattern for features and keep other subjects concise. Pull requests should describe behavior changes, link relevant issues, report validation commands/results, and explain security implications. Update documentation and regression tests when behavior changes.

## Security & Configuration Tips

Fail closed on invalid policy and unsafe paths. Keep policy configuration operator-controlled. Preserve generic errors that withhold raw arguments and Git stderr. Never bypass ownership checks with `safe.directory=*`.
