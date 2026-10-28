"""Restore only explicit permitted regular files after validating all destination state."""

from pathlib import Path
import git
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.write_tools import WriteTools
from test_secure_protocol import secure_connect, text


@pytest.fixture
def restoring(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/key").write_text("HIDDEN_OLD")
    repo.git.add(".")
    repo.index.commit("seed")
    (root / "test.txt").write_text("STAGED")
    (root / "private/key").write_text("HIDDEN_NEW")
    repo.git.add(".")
    (root / "test.txt").write_text("WORKING")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, WriteTools(backend)
    backend.repo.close()


def test_worktree_restore_defaults_to_index_and_explicit_source_uses_commit(restoring):
    repo, root, writer = restoring
    hidden = repo.git.ls_files("--stage", "--", "private/key")
    writer.restore(["test.txt"])
    assert (root / "test.txt").read_text() == "STAGED"
    writer.restore(["test.txt"], source="HEAD")
    assert (root / "test.txt").read_text().startswith("line 1")
    assert repo.git.show(":test.txt") == "STAGED"
    assert repo.git.ls_files("--stage", "--", "private/key") == hidden
    assert (root / "private/key").read_text() == "HIDDEN_NEW"


def test_index_only_and_both_destinations(restoring):
    repo, root, writer = restoring
    writer.restore(["test.txt"], staged=True)
    assert (root / "test.txt").read_text() == "WORKING"
    assert repo.git.diff("--cached", "--name-only").strip() == "private/key"
    (root / "test.txt").write_text("NEW")
    repo.git.add("test.txt")
    writer.restore(["test.txt"], staged=True, worktree=True)
    assert (root / "test.txt").read_text().startswith("line 1")
    assert repo.git.diff("--cached", "--name-only").strip() == "private/key"


def test_policy_denial_and_invalid_selection_preserve_state(restoring):
    repo, root, writer = restoring
    before = (
        Path(repo.git_dir, "index").read_bytes(),
        (root / "test.txt").read_bytes(),
        (root / "private/key").read_bytes(),
        repo.head.commit.hexsha,
    )
    for files in [["test.txt", "private/key"], ["."], ["private"], [], ["../outside"]]:
        with pytest.raises(PolicyError):
            writer.restore(files, source="HEAD", staged=True, worktree=True)
        after = (
            Path(repo.git_dir, "index").read_bytes(),
            (root / "test.txt").read_bytes(),
            (root / "private/key").read_bytes(),
            repo.head.commit.hexsha,
        )
        assert after == before
    with pytest.raises(PolicyError):
        writer.restore(["test.txt"], staged=False, worktree=False)


def test_ignored_directory_transition_and_symlink_ancestors_are_refused(repo):
    root = Path(repo.working_dir)
    (root / "slot").write_text("old")
    repo.git.add(".")
    repo.index.commit("slot")
    (root / "slot").unlink()
    (root / "slot").mkdir()
    (root / "slot/key").write_text("HIDDEN")
    writer = WriteTools(
        SecureRepository(root, PathPolicy.from_json('{"deny":["^slot/key$"]}'))
    )
    with pytest.raises(PolicyError):
        writer.restore(["slot"], source="HEAD")
    assert (root / "slot/key").read_text() == "HIDDEN"
    (root / "alias").symlink_to(root / "slot", target_is_directory=True)
    with pytest.raises(PolicyError):
        writer.restore(["alias/key"], source="HEAD")
    assert (root / "slot/key").read_text() == "HIDDEN"


def test_source_symlinks_gitlinks_and_directory_sources_are_refused(restoring):
    repo, root, writer = restoring
    (root / "link").symlink_to("test.txt")
    repo.git.add("link")
    with pytest.raises(PolicyError):
        writer.restore(["link"])
    repo.git.update_index(
        "--add", "--cacheinfo", f"160000,{repo.head.commit.hexsha},module"
    )
    with pytest.raises(PolicyError):
        writer.restore(["module"], staged=True)
    with pytest.raises(PolicyError):
        writer.restore(["test.txt"], source="HEAD:test.txt")


def test_unborn_repository_restore_and_literal_filenames(tmp_path):
    repo = git.Repo.init(tmp_path / "repo")
    root = Path(repo.working_dir)
    path = root / "-literal*.txt"
    path.write_text("STAGED")
    repo.git.add("--", path.name)
    path.write_text("WORKING")
    writer = WriteTools(SecureRepository(root, PathPolicy.from_json('{"deny":[]}')))
    writer.restore([path.name])
    assert path.read_text() == "STAGED"
    writer.restore([path.name], staged=True)
    assert repo.git.ls_files() == "" and path.read_text() == "STAGED"
    repo.close()


async def test_restore_registered_as_destructive_and_uses_default_repo(restoring):
    repo, root, _ = restoring
    async with secure_connect(root) as session:
        tools = await session.list_tools()
        tool = next(t for t in tools.tools if t.name == "git_restore")
        assert tool.annotations is not None
        assert tool.annotations.destructive_hint is True
        result = await session.call_tool("git_restore", {"files": ["test.txt"]})
        assert not result.is_error, text(result)
    assert (root / "test.txt").read_text() == "STAGED"


@pytest.mark.parametrize("mode", ["100644", "120000", "160000"])
@pytest.mark.parametrize("worktree", [False, True])
def test_restore_does_not_remove_unselected_index_ancestor(repo, worktree, mode):
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
        writer.restore(["slot/file"], source="HEAD", staged=True, worktree=worktree)
    assert (Path(repo.git_dir, "index").read_bytes(), repo.head.commit.hexsha) == before
    assert not (root / "slot").exists()
