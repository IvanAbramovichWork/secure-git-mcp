"""Test the secured server through real MCP requests, including response middleware."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams

from mcp_server_git.policy import PathPolicy
from mcp_server_git.secure_server import create_server
from test_filters import Replace


@asynccontextmanager
async def secure_connect(root, filters=()):
    async with create_client_server_memory_streams() as (client, server):

        @asynccontextmanager
        async def fake_stdio():
            yield server

        with patch("mcp.server.mcpserver.server.stdio_server", fake_stdio):
            async with anyio.create_task_group() as tg:
                service = create_server(
                    root, PathPolicy.from_json('{"deny":["^private$"]}'), filters
                )
                tg.start_soon(service.run_stdio_async)
                async with ClientSession(*client) as session:
                    await session.initialize()
                    yield session
                tg.cancel_scope.cancel()


def text(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


async def test_all_twelve_tools_and_filtered_descriptions(repo):
    root = Path(repo.working_dir)
    async with secure_connect(root) as session:
        tools = await session.list_tools()
        assert len(tools.tools) == 12
        assert all(
            t.description is not None and "policy" in t.description.lower()
            for t in tools.tools
        )
        args = {"repo_path": str(root)}
        for name, extra in [
            ("git_status", {}),
            ("git_diff_unstaged", {}),
            ("git_diff_staged", {}),
            ("git_diff", {"target": "HEAD"}),
            ("git_show", {"revision": "HEAD:test.txt"}),
            ("git_log", {}),
            ("git_branch", {"branch_type": "local"}),
        ]:
            result = await session.call_tool(name, {**args, **extra})
            assert not result.is_error, text(result)
        (root / "new.txt").write_text("new\n")
        for name, extra in [
            ("git_add", {"files": ["new.txt"]}),
            ("git_reset", {}),
            ("git_add", {"files": ["new.txt"]}),
            ("git_commit", {"message": "new"}),
            ("git_create_branch", {"branch_name": "new"}),
            ("git_checkout", {"branch_name": "new"}),
        ]:
            result = await session.call_tool(name, {**args, **extra})
            assert not result.is_error, text(result)
        assert repo.active_branch.name == "new"


async def test_pipeline_applies_to_multiple_tools_and_errors(repo):
    root = Path(repo.working_dir)
    async with secure_connect(
        root, [Replace("line", "[REDACTED]"), Replace("policy", "POLICY")]
    ) as session:
        for revision in ["HEAD:test.txt", "HEAD"]:
            result = await session.call_tool(
                "git_show", {"repo_path": str(root), "revision": revision}
            )
            assert "[REDACTED]" in text(result)
            assert "line" not in text(result)
        result = await session.call_tool(
            "git_show", {"repo_path": str(root), "revision": "HEAD:private/key"}
        )
        assert result.is_error
        assert "POLICY" in text(result)


async def test_errors_and_sdk_validation_never_echo_input(repo):
    root = Path(repo.working_dir)
    async with secure_connect(root) as session:
        for name, args in [
            ("git_show", {"revision": "SECRET_INVALID_REF"}),
            ("git_diff_unstaged", {"context_lines": "SECRET_INVALID_TYPE"}),
            ("git_status", {"repo_path": "/SECRET_OTHER_REPO"}),
        ]:
            result = await session.call_tool(name, {"repo_path": str(root), **args})
            assert result.is_error
            assert "SECRET" not in text(result)
            assert "policy" in text(result)
        with pytest.raises(MCPError) as exc:
            await session.call_tool("SECRET_UNKNOWN_TOOL", {})
        assert "SECRET" not in str(exc.value)


async def test_plugin_exception_fails_closed(repo):
    class Broken:
        def filter(self, text, context):
            raise RuntimeError("SECRET_EXCEPTION")

    async with secure_connect(Path(repo.working_dir), [Broken()]) as session:
        result = await session.call_tool(
            "git_show",
            {"repo_path": str(repo.working_dir), "revision": "HEAD:test.txt"},
        )
        assert result.is_error
        assert "line 1" not in text(result)
        assert "SECRET" not in text(result)
        assert "filter" in text(result).lower()


async def test_overlapping_calls_share_the_repository_lock(repo):
    import asyncio
    import threading
    import time
    from mcp_server_git.read_tools import ReadTools

    active = 0
    maximum = 0
    counter_lock = threading.Lock()

    def observed_status(self):
        nonlocal active, maximum
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with counter_lock:
            active -= 1
        return "permitted"

    async with secure_connect(Path(repo.working_dir)) as session:
        with patch.object(ReadTools, "status", observed_status):
            results = await asyncio.gather(
                *[
                    session.call_tool(
                        "git_status", {"repo_path": str(repo.working_dir)}
                    )
                    for _ in range(2)
                ]
            )
    assert all(not r.is_error for r in results)
    assert maximum == 1
