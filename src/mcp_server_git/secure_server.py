"""Secured MCP entry point; preserve upstream reference helpers separately for regression tests."""

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, Sequence

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent

from .filters import FilterContext, FilterFailure, FilterPipeline, OutputFilter
from .policy import DENIED, PathPolicy, PolicyError
from .read_tools import ReadTools
from .repository import SecureRepository
from .server import READ_ONLY, SERVER_VERSION, writes
from .write_tools import WriteTools

_P = ParamSpec("_P")


def create_server(
    repository: Path, policy: PathPolicy, filters: Sequence[OutputFilter] = ()
) -> MCPServer:
    backend = SecureRepository(repository, policy)
    reader, writer = ReadTools(backend), WriteTools(backend)
    pipeline = FilterPipeline(filters)
    names = {
        "git_status",
        "git_diff_unstaged",
        "git_diff_staged",
        "git_diff",
        "git_show",
        "git_log",
        "git_branch",
        "git_add",
        "git_commit",
        "git_reset",
        "git_create_branch",
        "git_checkout",
    }

    async def filter_responses(
        ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        name = (ctx.params or {}).get("name")
        if name not in names:
            raise MCPError(code=INVALID_PARAMS, message="Unknown tool.")
        result = await call_next(ctx)
        if not isinstance(result, CallToolResult):
            result = CallToolResult.model_validate(result)
        # SDK validation errors may quote input values; never return those diagnostics.
        text = (
            DENIED + " Invalid arguments or Git operation unavailable."
            if result.is_error
            else "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        )
        try:
            text = pipeline.apply(text, FilterContext(str(name), result.is_error))
        except FilterFailure as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=str(exc))], is_error=True
            )
        return result.model_copy(
            update={
                "content": [TextContent(type="text", text=text)],
                "structured_content": None,
            }
        )

    server = MCPServer(
        "secure-git-mcp",
        version=SERVER_VERSION,
        middleware=[filter_responses],
        subscriptions=False,
        instructions="All Git tools enforce project path policy. Partial results omit protected content; denied requests must not be bypassed.",
    )

    def guarded(fn: Callable[_P, str]) -> Callable[_P, str]:
        @wraps(fn)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> str:
            with backend.lock:
                try:
                    # repo_path is present in every signature and is never an access-policy input.
                    path = kwargs.get("repo_path", args[0] if args else None)
                    if not isinstance(path, str):
                        raise PolicyError(DENIED)
                    backend.validate_repo_path(path)
                    return fn(*args, **kwargs)
                except PolicyError as exc:
                    raise ToolError(str(exc)) from None
                except Exception:
                    raise ToolError(
                        "Git operation unavailable according to project policy."
                    ) from None

        return wrapped

    suffix = " Results and operations are restricted according to project policy."

    @server.tool(
        name="git_status",
        description="Show permitted working tree status." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def status(repo_path: str) -> str:
        return reader.status()

    @server.tool(
        name="git_diff_unstaged",
        description="Show permitted unstaged file patches." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff_unstaged(repo_path: str, context_lines: int = 3) -> str:
        return reader.diff_unstaged(context_lines)

    @server.tool(
        name="git_diff_staged",
        description="Show permitted staged file patches." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff_staged(repo_path: str, context_lines: int = 3) -> str:
        return reader.diff_staged(context_lines)

    @server.tool(
        name="git_diff",
        description="Compare worktree with a commit, omitting protected file patches."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff(repo_path: str, target: str, context_lines: int = 3) -> str:
        return reader.diff(target, context_lines)

    @server.tool(
        name="git_show",
        description="Show a commit or commit:path; raw blobs/trees are forbidden."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def show(repo_path: str, revision: str) -> str:
        return reader.show(revision)

    @server.tool(
        name="git_log",
        description="Show history visible under the path policy." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def log(
        repo_path: str,
        max_count: int = 10,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> str:
        return reader.log(max_count, start_timestamp, end_timestamp)

    @server.tool(
        name="git_branch",
        description="List branch names (not secret-scanned)." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def branch(
        repo_path: str,
        branch_type: str,
        contains: str | None = None,
        not_contains: str | None = None,
    ) -> str:
        return reader.branch(branch_type, contains, not_contains)

    @server.tool(
        name="git_add",
        description="Stage literal files/directories; reject the entire selection if restricted."
        + suffix,
        annotations=writes(destructive=False, idempotent=True),
        structured_output=False,
    )
    @guarded
    def add(repo_path: str, files: list[str]) -> str:
        return writer.add(files)

    @server.tool(
        name="git_commit",
        description="Commit only when the complete staged set is permitted." + suffix,
        annotations=writes(destructive=False, idempotent=False),
        structured_output=False,
    )
    @guarded
    def commit(repo_path: str, message: str) -> str:
        return writer.commit(message)

    @server.tool(
        name="git_reset",
        description="Unstage only when the complete staged set is permitted." + suffix,
        annotations=writes(destructive=True, idempotent=True),
        structured_output=False,
    )
    @guarded
    def reset(repo_path: str) -> str:
        return writer.reset()

    @server.tool(
        name="git_create_branch",
        description="Create a branch without changing working files." + suffix,
        annotations=writes(destructive=False, idempotent=False),
        structured_output=False,
    )
    @guarded
    def create_branch(
        repo_path: str, branch_name: str, base_branch: str | None = None
    ) -> str:
        return writer.create_branch(branch_name, base_branch)

    @server.tool(
        name="git_checkout",
        description="Checkout only if no protected paths or submodules change."
        + suffix,
        annotations=writes(destructive=False, idempotent=False),
        structured_output=False,
    )
    @guarded
    def checkout(repo_path: str, branch_name: str) -> str:
        return writer.checkout(branch_name)

    return server


async def serve(repository: Path, policy: PathPolicy) -> None:
    await create_server(repository, policy).run_stdio_async()
