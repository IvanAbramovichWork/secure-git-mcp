"""Path-filtered history selects logical old/new paths without exposing mixed metadata."""

from pathlib import Path
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from test_secure_protocol import secure_connect, text


def test_log_selects_paths_and_retains_deleted_history(repo):
    root = Path(repo.working_dir)
    (root / "a").write_text("a")
    repo.git.add(".")
    repo.index.commit("A_ONLY")
    (root / "b").write_text("b")
    repo.git.add(".")
    repo.index.commit("B_ONLY")
    (root / "a").unlink()
    repo.git.add(".")
    repo.index.commit("A_DELETE")
    reader = ReadTools(SecureRepository(root, PathPolicy.from_json('{"deny":[]}')))
    result = reader.log(paths=["a"])
    assert "A_ONLY" in result and "A_DELETE" in result and "B_ONLY" not in result
    assert "A_DELETE" in reader.log(paths=["a"], max_count=1)
    assert "A_ONLY" not in reader.log(paths=["a"], max_count=1)
    assert reader.log(paths=["missing"]) == ""
    with pytest.raises(PolicyError):
        reader.log(paths=[])


def test_mixed_metadata_and_boundary_rename_stay_hidden(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/key").write_text("secret")
    (root / "allowed").write_text("public")
    repo.git.add(".")
    mixed = repo.index.commit("HIDDEN_MIXED_MESSAGE")
    repo.git.mv("private/key", "renamed")
    renamed = repo.index.commit("HIDDEN_RENAME_MESSAGE")
    reader = ReadTools(
        SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    )
    result = reader.log(paths=["allowed"])
    assert mixed.hexsha in result and "HIDDEN_MIXED_MESSAGE" not in result
    assert renamed.hexsha not in reader.log(paths=["renamed"])
    assert "HIDDEN_RENAME_MESSAGE" not in reader.log(paths=["renamed"])
    with pytest.raises(PolicyError):
        reader.log(paths=["private"])


async def test_log_path_filter_is_exposed_over_mcp(repo):
    root = Path(repo.working_dir)
    (root / "other").write_text("other")
    repo.git.add(".")
    repo.index.commit("OTHER_ONLY")
    async with secure_connect(root) as session:
        result = await session.call_tool("git_log", {"paths": ["test.txt"]})
        assert not result.is_error
        assert "initial commit" in text(result) and "OTHER_ONLY" not in text(result)
