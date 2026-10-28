"""Unstage a validated selection without changing working files or unrelated hidden index entries."""

from pathlib import Path
import git
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.write_tools import WriteTools
from test_secure_protocol import secure_connect, text


@pytest.fixture
def staged(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/key").write_text("hidden old")
    repo.git.add(".")
    repo.index.commit("seed")
    (root / "private/key").write_text("hidden new")
    (root / "test.txt").write_text("allowed new")
    repo.git.add(".")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, WriteTools(backend)
    backend.repo.close()


def test_unstage_allowed_preserves_hidden_staging_and_all_working_files(staged):
    repo, root, writer = staged
    hidden = repo.git.ls_files("--stage", "--", "private/key")
    head = repo.head.commit.hexsha
    writer.unstage(["test.txt"])
    assert repo.git.diff("--cached", "--name-only").strip() == "private/key"
    assert repo.git.ls_files("--stage", "--", "private/key") == hidden
    assert (root / "test.txt").read_text() == "allowed new"
    assert (root / "private/key").read_text() == "hidden new"
    assert repo.head.commit.hexsha == head


def test_denied_selection_is_atomic(staged):
    repo, root, writer = staged
    before = Path(repo.git_dir, "index").read_bytes()
    for paths in [["test.txt", "private/key"], ["."], [], ["../outside"]]:
        with pytest.raises(PolicyError):
            writer.unstage(paths)
        assert Path(repo.git_dir, "index").read_bytes() == before


def test_unborn_intent_to_add_and_literal_paths(tmp_path):
    repo = git.Repo.init(tmp_path / "repo")
    root = Path(repo.working_dir)
    (root / "-literal*.txt").write_text("working file")
    repo.git.add("-N", "--", "-literal*.txt")
    writer = WriteTools(SecureRepository(root, PathPolicy.from_json('{"deny":[]}')))
    writer.unstage(["-literal*.txt"])
    assert (
        repo.git.ls_files() == ""
        and (root / "-literal*.txt").read_text() == "working file"
    )
    repo.close()


def test_directory_selection_and_modes(staged):
    repo, root, writer = staged
    (root / "public").mkdir()
    (root / "public/a").write_text("new")
    repo.git.add("public")
    writer.unstage(["public"])
    assert "public/a" not in repo.git.ls_files()
    (root / "link").symlink_to("test.txt")
    repo.git.add("link")
    with pytest.raises(PolicyError):
        writer.unstage(["link"])
    repo.git.update_index(
        "--add", "--cacheinfo", f"160000,{repo.head.commit.hexsha},module"
    )
    with pytest.raises(PolicyError):
        writer.unstage(["module"])


async def test_unstage_registered_and_default_repository(staged):
    repo, root, _ = staged
    async with secure_connect(root) as session:
        result = await session.call_tool("git_unstage", {"files": ["test.txt"]})
        assert not result.is_error, text(result)
    assert repo.git.diff("--cached", "--name-only").strip() == "private/key"


@pytest.mark.parametrize("mode", ["100644", "120000", "160000"])
def test_unstage_does_not_remove_unselected_index_ancestor(repo, mode):
    import shutil

    root = Path(repo.working_dir)
    (root / "slot").mkdir()
    (root / "slot/file").write_text("original")
    repo.git.add(".")
    repo.index.commit("directory")
    shutil.rmtree(root / "slot")
    repo.git.rm("-r", "--cached", "--", "slot")
    oid = (
        repo.head.commit.hexsha
        if mode == "160000"
        else repo.git.rev_parse("HEAD:test.txt")
    )
    repo.git.update_index("--add", "--cacheinfo", f"{mode},{oid},slot")
    before = Path(repo.git_dir, "index").read_bytes(), repo.head.commit.hexsha
    writer = WriteTools(SecureRepository(root, PathPolicy.from_json('{"deny":[]}')))
    with pytest.raises(PolicyError):
        writer.unstage(["slot/file"])
    assert (Path(repo.git_dir, "index").read_bytes(), repo.head.commit.hexsha) == before
