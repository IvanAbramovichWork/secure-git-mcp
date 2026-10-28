"""Retained upstream CLI fixture, never installed as a runnable server."""

import click
from pathlib import Path
import logging
import sys
from mcp_server_git.server import serve


@click.command()
@click.option("--repository", "-r", type=Path, help="Git repository path")
@click.option("-v", "--verbose", count=True)
def main(repository: Path | None, verbose: bool) -> None:
    """MCP Git Server - Git functionality for MCP"""
    import asyncio

    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    logging.basicConfig(level=logging_level, stream=sys.stderr)
    asyncio.run(serve(repository))
