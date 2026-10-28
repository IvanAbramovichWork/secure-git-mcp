"""Functional stdio check against native or Docker entry points using disposable Git history."""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


async def smoke(image: str | None, parent: str | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="secure-git-smoke-", dir=parent) as temp:
        root = Path(temp)
        git(root, "init", "-b", "main")
        git(root, "config", "user.name", "Smoke Test")
        git(root, "config", "user.email", "smoke@example.com")
        (root / "public").mkdir()
        (root / "private").mkdir()
        (root / "public/readme").write_text("VISIBLE_SENTINEL\n")
        (root / "private/key").write_text("HIDDEN_SENTINEL\n")
        git(root, "add", ".")
        git(root, "commit", "-m", "fixture")
        (root / ".claude").mkdir()
        (root / ".claude/settings.json").write_text(
            '{"sandbox":{"filesystem":{"denyRead":["private"]}}}'
        )
        (root / "mcp-only").mkdir()
        (root / "mcp-only/key").write_text("MCP_HIDDEN_SENTINEL")
        policy = '{"deny":["^mcp-only$"]}'
        if image:
            command = "docker"
            args = [
                "run",
                "--pull=never",
                "--rm",
                "-i",
                "--network=none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--env",
                "SECURE_GIT_POLICY",
                "--mount",
                f"type=bind,src={root},dst=/repo",
                image,
            ]
            served_path = "/repo"
        else:
            command = sys.executable
            args = ["-m", "mcp_server_git"]
            served_path = str(root)
        parameters = StdioServerParameters(
            command=command,
            args=args,
            cwd=str(root),
            env={**os.environ, "SECURE_GIT_POLICY": policy},
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                explicit = await session.call_tool(
                    "git_status", {"repo_path": served_path}
                )
                assert not explicit.is_error

                async def call(name: str, **kwargs):
                    result = await session.call_tool(name, kwargs)
                    text = "\n".join(c.text for c in result.content if c.type == "text")
                    assert (
                        "HIDDEN_SENTINEL" not in text
                    ), f"protected content leaked by {name}"
                    return result, text

                result, text = await call("git_show", revision="HEAD:public/readme")
                assert not result.is_error and "VISIBLE_SENTINEL" in text
                result, text = await call("git_add", files=["mcp-only/key"])
                assert result.is_error and "according to project policy" in text
                result, text = await call("git_show", revision="HEAD:private/key")
                assert result.is_error and "according to project policy" in text
                result, text = await call("git_show", revision="HEAD")
                assert (
                    not result.is_error
                    and "VISIBLE_SENTINEL" in text
                    and "Some content is unavailable" in text
                )
                (root / "public/readme").write_text("VISIBLE_NEW\n")
                (root / "private/key").write_text("HIDDEN_NEW\n")
                index = root / ".git/index"
                before = index.read_bytes()
                result, text = await call("git_add", files=["."])
                assert result.is_error and index.read_bytes() == before
                result, text = await call("git_add", files=["public/readme"])
                assert not result.is_error, text
                assert (
                    git(root, "diff", "--cached", "--name-only").strip()
                    == "public/readme"
                )
                previous = git(root, "rev-parse", "HEAD")
                result, text = await call("git_commit", message="permitted change")
                assert not result.is_error, text
                assert git(root, "rev-parse", "HEAD") != previous
                assert (root / "private/key").read_text() == "HIDDEN_NEW\n"
                result, text = await call("git_diff_unstaged")
                assert (
                    not result.is_error
                    and "HIDDEN_NEW" not in text
                    and "Some content is unavailable" in text
                )
        print(
            ("Docker" if image else "Native")
            + " MCP smoke passed: permitted reads/writes, filtered diffs, and atomic policy denials."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", metavar="IMAGE")
    parser.add_argument(
        "--parent",
        help="Shared host directory for disposable Docker bind-mount fixture.",
    )
    options = parser.parse_args()
    asyncio.run(smoke(options.docker, options.parent))
