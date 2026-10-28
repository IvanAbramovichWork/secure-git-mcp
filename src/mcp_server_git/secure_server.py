"""Secured MCP entry point; preserve upstream reference helpers separately for regression tests."""

from collections.abc import Callable
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import Annotated, Any, ParamSpec, Sequence

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent
from pydantic import Field

from .errors import MESSAGES, SafeGitError, response_message
from .filters import FilterContext, FilterFailure, FilterPipeline, OutputFilter
from .policy import DENIED, PathPolicy, PolicyError
from .read_tools import ReadTools
from .repository import SecureRepository
from .server import READ_ONLY, SERVER_VERSION, writes
from .write_tools import WriteTools

_P = ParamSpec("_P")
RepoPath = Annotated[
    str | None,
    Field(
        description="Optional repository root. Omit to use the server's fixed repository. An explicit path must identify that same repository."
    ),
]


def create_server(
    repository: Path, policy: PathPolicy, filters: Sequence[OutputFilter] = ()
) -> MCPServer:
    backend = SecureRepository(repository, policy)
    reader, writer = ReadTools(backend), WriteTools(backend)
    pipeline = FilterPipeline(filters)
    names = {
        "git_status",
        "git_ls_files",
        "git_grep",
        "git_blame",
        "git_diff_revisions",
        "git_unstage",
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
        raw_text = "\n".join(
            c.text for c in result.content if isinstance(c, TextContent)
        )
        if result.is_error:
            # Only an exact, server-owned marker can select a specific message.
            code = next(
                (
                    c
                    for c in MESSAGES
                    if raw_text == f"Error executing tool {name}: secure-git/{c}"
                ),
                "INVALID_ARGUMENTS",
            )
            text = response_message(code)
        else:
            text = raw_text
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
        instructions=f"Repository: {str(backend.root)!r}. Omit repo_path to use this fixed repository; do not guess a host path. All Git tools enforce project path policy. Partial results omit protected content; denied requests must not be bypassed.",
    )

    def guarded(fn: Callable[_P, str]) -> Callable[_P, str]:
        parameters = signature(fn)

        @wraps(fn)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> str:
            with backend.lock:
                try:
                    # Bind by name because operation arguments precede optional repo_path.
                    path = parameters.bind(*args, **kwargs).arguments.get("repo_path")
                    if path is None:
                        path = str(backend.root)
                    if not isinstance(path, str) or not path:
                        raise PolicyError(DENIED)
                    backend.validate_repo_path(path)
                    return fn(*args, **kwargs)
                except PolicyError as exc:
                    code = (
                        exc.code
                        if isinstance(exc, SafeGitError)
                        else "POLICY_DENIED"
                        if str(exc) == DENIED
                        else "OPERATION_REFUSED"
                    )
                    raise ToolError("secure-git/" + code) from None
                except Exception:
                    raise ToolError("secure-git/INTERNAL_ERROR") from None

        return wrapped

    suffix = " Omit repo_path to use the configured repository. Results and operations are restricted according to project policy."

    @server.tool(
        name="git_diff_revisions",
        description="Compare two committed trees, filtering complete changes by permitted literal paths."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff_revisions(
        base: str,
        target: str,
        paths: list[str] | None = None,
        context_lines: int = 3,
        repo_path: RepoPath = None,
    ) -> str:
        return reader.diff_revisions(base, target, paths, context_lines)

    @server.tool(
        name="git_blame",
        description="Blame permitted committed lines; protected historical provenance is omitted and mixed metadata withheld."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def blame(
        path: str,
        revision: str = "HEAD",
        start_line: int = 1,
        end_line: int | None = None,
        max_count: int = 500,
        repo_path: RepoPath = None,
    ) -> str:
        return reader.blame(path, revision, start_line, end_line, max_count)

    @server.tool(
        name="git_grep",
        description="Search permitted tracked working files or a commit; literal patterns by default."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def grep(
        pattern: str,
        paths: list[str] | None = None,
        revision: str | None = None,
        regex: bool = False,
        max_count: int = 1000,
        repo_path: RepoPath = None,
    ) -> str:
        return reader.grep(pattern, paths, revision, regex, max_count)

    @server.tool(
        name="git_ls_files",
        description="List selected permitted regular files in the index or a commit."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def ls_files(
        paths: list[str] | None = None,
        revision: str | None = None,
        include_untracked: bool = False,
        repo_path: RepoPath = None,
    ) -> str:
        return reader.ls_files(paths, revision, include_untracked)

    @server.tool(
        name="git_status",
        description="Show permitted working tree status." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def status(repo_path: RepoPath = None) -> str:
        return reader.status()

    @server.tool(
        name="git_diff_unstaged",
        description="Show permitted unstaged file patches." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff_unstaged(context_lines: int = 3, repo_path: RepoPath = None) -> str:
        return reader.diff_unstaged(context_lines)

    @server.tool(
        name="git_diff_staged",
        description="Show permitted staged file patches." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff_staged(context_lines: int = 3, repo_path: RepoPath = None) -> str:
        return reader.diff_staged(context_lines)

    @server.tool(
        name="git_diff",
        description="Compare worktree with a commit, omitting protected file patches."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def diff(target: str, context_lines: int = 3, repo_path: RepoPath = None) -> str:
        return reader.diff(target, context_lines)

    @server.tool(
        name="git_show",
        description="Show a commit or commit:path; raw blobs/trees are forbidden."
        + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def show(revision: str, repo_path: RepoPath = None) -> str:
        return reader.show(revision)

    @server.tool(
        name="git_log",
        description="Show history visible under the path policy." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def log(
        max_count: int = 10,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
        paths: list[str] | None = None,
        repo_path: RepoPath = None,
    ) -> str:
        return reader.log(max_count, start_timestamp, end_timestamp, paths)

    @server.tool(
        name="git_branch",
        description="List branch names (not secret-scanned)." + suffix,
        annotations=READ_ONLY,
        structured_output=False,
    )
    @guarded
    def branch(
        branch_type: str,
        contains: str | None = None,
        not_contains: str | None = None,
        repo_path: RepoPath = None,
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
    def add(files: list[str], repo_path: RepoPath = None) -> str:
        return writer.add(files)

    @server.tool(
        name="git_commit",
        description="Commit only when the complete staged set is permitted." + suffix,
        annotations=writes(destructive=False, idempotent=False),
        structured_output=False,
    )
    @guarded
    def commit(message: str, repo_path: RepoPath = None) -> str:
        return writer.commit(message)

    @server.tool(
        name="git_unstage",
        description="Unstage a permitted literal selection without changing working files or unrelated index entries."
        + suffix,
        annotations=writes(destructive=True, idempotent=True),
        structured_output=False,
    )
    @guarded
    def unstage(files: list[str], repo_path: RepoPath = None) -> str:
        return writer.unstage(files)

    @server.tool(
        name="git_reset",
        description="Unstage only when the complete staged set is permitted." + suffix,
        annotations=writes(destructive=True, idempotent=True),
        structured_output=False,
    )
    @guarded
    def reset(repo_path: RepoPath = None) -> str:
        return writer.reset()

    @server.tool(
        name="git_create_branch",
        description="Create a branch without changing working files." + suffix,
        annotations=writes(destructive=False, idempotent=False),
        structured_output=False,
    )
    @guarded
    def create_branch(
        branch_name: str, base_branch: str | None = None, repo_path: RepoPath = None
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
    def checkout(branch_name: str, repo_path: RepoPath = None) -> str:
        return writer.checkout(branch_name)

    return server


async def serve(repository: Path, policy: PathPolicy) -> None:
    await create_server(repository, policy).run_stdio_async()
