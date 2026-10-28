"""All user entry points require explicit policy before starting MCP stdio."""

import json
import os
import subprocess
import sys
from pathlib import Path
from click.testing import CliRunner
from mcp_server_git.secure_cli import main


def test_secure_cli_missing_and_invalid_policy(repo, monkeypatch):
    monkeypatch.delenv("SECURE_GIT_POLICY", raising=False)
    for value in [None, '{"deny":["SECRET_BAD_REGEX["]}']:
        if value is not None:
            monkeypatch.setenv("SECURE_GIT_POLICY", value)
        result = CliRunner().invoke(main, ["--repository", str(repo.working_dir)])
        assert result.exit_code != 0
        assert "SECRET" not in result.output
        assert "policy" in result.output


def test_secure_cli_config_and_help(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("SECURE_GIT_POLICY", "INVALID")
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps(
            {"mcpServers": {"custom": {"env": {"SECURE_GIT_POLICY": '{"deny":[]}'}}}}
        )
    )
    from unittest.mock import patch

    called = []

    async def fake_serve(root, policy):
        called.append((root, policy))

    with patch("mcp_server_git.secure_cli.serve", fake_serve):
        result = CliRunner().invoke(
            main,
            [
                "--repository",
                str(repo.working_dir),
                "--mcp-config",
                str(cfg),
                "--server-name",
                "custom",
            ],
        )
    assert result.exit_code == 0, result.output
    assert called[0][1].allows("normal")
    assert CliRunner().invoke(main, ["--help"]).exit_code == 0
    assert CliRunner().invoke(main, []).exit_code != 0


def test_console_and_module_entrypoints_require_policy(repo):
    env = {k: v for k, v in os.environ.items() if k != "SECURE_GIT_POLICY"}
    commands = [
        [sys.executable, "-m", "mcp_server_git"],
        [str(Path(sys.executable).parent / "secure-git-mcp")],
        [str(Path(sys.executable).parent / "mcp-server-git")],
    ]
    for cmd in commands:
        result = subprocess.run(
            [*cmd, "--repository", str(repo.working_dir)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert not result.stdout
        assert "policy" in result.stderr
