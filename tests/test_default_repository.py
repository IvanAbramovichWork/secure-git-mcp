"""Repository defaults remove Docker path guessing without broadening repository access."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mcp_server_git.secure_cli import main
from test_secure_protocol import secure_connect, text


@pytest.mark.parametrize("subdirectory", [False, True])
def test_cli_defaults_to_current_repository(repo, monkeypatch, subdirectory):
    root = Path(repo.working_dir)
    cwd = root / "sub" if subdirectory else root
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SECURE_GIT_POLICY", '{"deny":[]}')
    seen = []

    async def serve(root, policy):
        seen.append(root)

    with patch("mcp_server_git.secure_cli.serve", serve):
        result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert seen == [root]


def test_default_outside_repository_fails_before_stdio(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECURE_GIT_POLICY", '{"deny":[]}')
    with patch("mcp_server_git.secure_cli.serve") as serve:
        result = CliRunner().invoke(main, [])
    assert result.exit_code != 0
    serve.assert_not_called()


async def test_every_tool_schema_defaults_repository_but_keeps_required_inputs(repo):
    required = {
        "git_diff": "target",
        "git_show": "revision",
        "git_branch": "branch_type",
        "git_add": "files",
        "git_commit": "message",
        "git_create_branch": "branch_name",
        "git_checkout": "branch_name",
    }
    async with secure_connect(Path(repo.working_dir)) as session:
        response = await session.list_tools()
        for tool in response.tools:
            schema = tool.input_schema
            assert "repo_path" not in schema.get("required", [])
            assert schema["properties"]["repo_path"].get("default", "MISSING") is None
            if tool.name in required:
                assert required[tool.name] in schema.get("required", [])


async def test_all_twelve_tools_work_without_repository_arguments(repo):
    root = Path(repo.working_dir)
    async with secure_connect(root) as session:
        for name, args in [
            ("git_status", {}),
            ("git_diff_unstaged", {}),
            ("git_diff_staged", {}),
            ("git_diff", {"target": "HEAD"}),
            ("git_show", {"revision": "HEAD:test.txt"}),
            ("git_log", {}),
            ("git_branch", {"branch_type": "local"}),
        ]:
            result = await session.call_tool(name, args)
            assert not result.is_error, text(result)
        (root / "new.txt").write_text("permitted\n")
        for name, args in [
            ("git_add", {"files": ["new.txt"]}),
            ("git_reset", {}),
            ("git_add", {"files": ["new.txt"]}),
            ("git_commit", {"message": "permitted"}),
            ("git_create_branch", {"branch_name": "optional-path"}),
            ("git_checkout", {"branch_name": "optional-path"}),
        ]:
            result = await session.call_tool(name, args)
            assert not result.is_error, text(result)
        assert repo.active_branch.name == "optional-path"


async def test_null_defaults_and_explicit_wrong_paths_do_not_override_repository(
    repo, tmp_path
):
    root = Path(repo.working_dir)
    (root / "new.txt").write_text("permitted\n")
    before = Path(repo.git_dir, "index").read_bytes()
    async with secure_connect(root) as session:
        assert not (await session.call_tool("git_status", {"repo_path": None})).is_error
        for path in [str(tmp_path), "", "/home/ivan.abramovich/ecss10"]:
            result = await session.call_tool(
                "git_add", {"repo_path": path, "files": ["new.txt"]}
            )
            assert result.is_error
            assert Path(repo.git_dir, "index").read_bytes() == before
        result = await session.call_tool("git_show", {"revision": "HEAD:private/key"})
        assert result.is_error
