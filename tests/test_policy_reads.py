"""Exercise actual Git objects and patches rather than matching text fragments."""

from pathlib import Path

import pytest

from mcp_server_git.policy import PARTIAL, PathPolicy, PolicyError
from mcp_server_git.read_tools import ReadTools
from mcp_server_git.repository import SecureRepository


@pytest.fixture
def guarded(repo):
    root = Path(repo.working_dir)
    for name, text in [
        ("public/a.txt", "VISIBLE_OLD\n"),
        ("private/key.txt", "HIDDEN_OLD\n"),
    ]:
        p = root / name
        p.parent.mkdir(exist_ok=True)
        p.write_text(text)
    repo.git.add(".")
    repo.index.commit("MIXED_SECRET_MESSAGE")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, ReadTools(backend)
    backend.repo.close()


def assert_filtered(text):
    assert "private" not in text
    assert "HIDDEN" not in text
    assert PARTIAL in text


def test_status_and_diffs(guarded):
    repo, root, reads = guarded
    (root / "public/a.txt").write_text("VISIBLE_NEW\n")
    (root / "private/key.txt").write_text("HIDDEN_NEW\n")
    (root / "private/untracked.txt").write_text("HIDDEN")
    assert_filtered(reads.status())
    text = reads.diff_unstaged()
    assert_filtered(text)
    assert "VISIBLE_NEW" in text
    repo.git.add(".")
    assert_filtered(reads.diff_staged())
    assert "VISIBLE_NEW" in reads.diff_staged()
    assert_filtered(reads.diff("HEAD"))


def test_show_and_history(guarded):
    repo, root, reads = guarded
    assert reads.show("HEAD:public/a.txt") == "VISIBLE_OLD\n"
    assert_filtered(reads.show("HEAD:"))
    assert "public/" in reads.show("HEAD:")
    text = reads.show("HEAD")
    assert_filtered(text)
    assert "MIXED_SECRET_MESSAGE" not in text
    assert "Test User" not in text
    for spec in [
        "HEAD:private/key.txt",
        "HEAD:private",
        ":0:private/key.txt",
        "HEAD:public/../private/key.txt",
        "HEAD^{tree}",
        repo.git.rev_parse("HEAD:private/key.txt"),
        "--help",
    ]:
        with pytest.raises(PolicyError):
            reads.show(spec)
    (root / "private/key.txt").write_text("HIDDEN_SECOND\n")
    repo.git.add(".")
    hidden = repo.index.commit("HIDDEN_ONLY_MESSAGE")
    text = reads.log()
    assert_filtered(text)
    assert hidden.hexsha not in text
    assert "MIXED_SECRET_MESSAGE" not in text
    assert "HIDDEN_ONLY_MESSAGE" not in text


def test_rename_out_of_denied_directory(guarded):
    repo, root, reads = guarded
    repo.git.mv("private/key.txt", "public/moved.txt")
    text = reads.diff_staged()
    assert_filtered(text)
    assert "moved.txt" not in text
    assert_filtered(reads.status())
    assert "moved.txt" not in reads.status()


@pytest.mark.parametrize(
    "filename", ['quote"name.txt', "new\nline.txt", "üñî.txt", "literal[1]*.txt"]
)
def test_unusual_filenames_are_literal_and_safe(guarded, filename):
    repo, root, reads = guarded
    (root / "public" / filename).write_text("VISIBLE_UNUSUAL\n")
    (root / "private" / filename).write_text("HIDDEN_UNUSUAL\n")
    repo.git.add(".")
    text = reads.diff_staged()
    assert "VISIBLE_UNUSUAL" in text
    assert_filtered(text)


def test_deleted_and_binary_files(guarded):
    repo, root, reads = guarded
    (root / "public/a.txt").unlink()
    (root / "private/key.txt").unlink()
    (root / "public/binary").write_bytes(b"\x00visible")
    (root / "private/binary").write_bytes(b"\x00HIDDEN")
    repo.git.add(".")
    text = reads.diff_staged()
    assert_filtered(text)
    assert "VISIBLE_OLD" in text
    assert "Binary files" in text


def test_symlink_and_gitlink_contents_not_served(guarded):
    repo, root, reads = guarded
    (root / "public/link").symlink_to("../private/key.txt")
    repo.git.add(".")
    repo.index.commit("links")
    with pytest.raises(PolicyError):
        reads.show("HEAD:public/link")
    repo.git.update_index(
        "--add", "--cacheinfo", f"160000,{repo.head.commit.hexsha},public/submodule"
    )
    repo.index.commit("gitlink")
    with pytest.raises(PolicyError):
        reads.show("HEAD:public/submodule")
    assert_filtered(reads.show("HEAD"))


def test_root_commit_allowed_history_and_ref_validation(repo):
    reads = ReadTools(
        SecureRepository(Path(repo.working_dir), PathPolicy.from_json('{"deny":[]}'))
    )
    assert "line 1" in reads.show("HEAD")
    assert "initial commit" in reads.log()
    assert "main" in reads.branch("local")
    for n in [-1, 10001]:
        with pytest.raises(PolicyError):
            reads.log(n)
    with pytest.raises(PolicyError):
        reads.diff("--output=/tmp/forbidden")
    with pytest.raises(PolicyError):
        reads.diff_unstaged(-1)
    with pytest.raises(PolicyError):
        reads.log(start_timestamp="--help")


def test_unborn_read_operations(tmp_path):
    import git

    repo = git.Repo.init(tmp_path / "unborn")
    reads = ReadTools(
        SecureRepository(Path(repo.working_dir), PathPolicy.from_json('{"deny":[]}'))
    )
    assert reads.diff_unstaged() == ""
    assert reads.diff_staged() == ""
    assert reads.status() == ""
    repo.close()
