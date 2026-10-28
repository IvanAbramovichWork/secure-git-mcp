"""Require repository pinning and trusted policy before opening MCP stdio."""

import asyncio
import logging
import sys
from pathlib import Path

import click

from .policy import PolicyError, load_policy
from .repository import SecureRepository
from .secure_server import serve


@click.command()
@click.option(
    "--repository",
    "-r",
    type=click.Path(path_type=Path),
    required=True,
    help="Git working tree to serve exclusively.",
)
@click.option(
    "--mcp-config",
    type=click.Path(path_type=Path),
    help="Trusted .mcp.json to read instead of environment policy.",
)
@click.option("--server-name", default="secure-git", show_default=True)
@click.option("-v", "--verbose", count=True)
def main(
    repository: Path, mcp_config: Path | None, server_name: str, verbose: int
) -> None:
    """Secure Git MCP server: Git operations restricted by project policy."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, stream=sys.stderr
    )
    try:
        policy = load_policy(mcp_config, server_name)
        backend = SecureRepository(repository, policy)
        root = backend.root
        backend.repo.close()
        asyncio.run(serve(root, policy))
    except PolicyError as exc:
        raise click.ClickException(str(exc)) from None
