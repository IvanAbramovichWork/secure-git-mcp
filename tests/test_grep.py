"""Search only permitted regular files and decode filenames independently of match text."""

from pathlib import Path
import shutil
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from test_secure_protocol import secure_connect, text


@pytest.fixture
def searching(repo):
    root = Path(repo.working_dir)
    for name, body in [
        ("public/a.txt", "needle first\nneedle second\n"),
        ("private/a.txt", "needle HIDDEN_SENTINEL\n"),
        ("public/weird\nname*.txt", "needle unusual\n"),
    ]:
        p = root / name
        p.parent.mkdir(exist_ok=True)
        p.write_text(body)
    (root / "public/binary").write_bytes(b"\0needle binary")
    repo.git.add(".")
    repo.index.commit("search fixture")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, ReadTools(backend)
    backend.repo.close()


def test_search_filters_denied_binary_and_untracked_files(searching):
    repo, root, reader = searching
    (root / "untracked").write_text("needle UNTRACKED")
    result = reader.grep("needle")
    assert "needle first" in result and "needle unusual" in result
    assert "public/weird\\nname*.txt" in result
    assert all(
        s not in result for s in ["HIDDEN_SENTINEL", "private", "binary", "UNTRACKED"]
    )
    assert "Some content is unavailable" in result


def test_no_match_literal_pattern_and_revision(searching):
    repo, root, reader = searching
    assert "needle" not in reader.grep("--open-files-in-pager")
    assert "needle" not in reader.grep("never.matches")
    assert "needle" not in reader.grep("needle.*")
    assert "needle first" in reader.grep("needle.*", regex=True)
    (root / "public/a.txt").write_text("changed\n")
    assert "needle first" not in reader.grep("needle", paths=["public/a.txt"])
    assert "needle first" in reader.grep(
        "needle", revision="HEAD", paths=["public/a.txt"]
    )


def test_literal_selection_and_bounded_results(searching):
    repo, root, reader = searching
    result = reader.grep("needle", paths=["public/weird\nname*.txt"])
    assert "unusual" in result and "first" not in result
    result = reader.grep("needle", paths=["public/a.txt"], max_count=1)
    assert (
        "first" in result and "second" not in result and "truncated" in result.lower()
    )
    for kwargs in [
        {"max_count": -1},
        {"pattern": "\x00"},
        {"pattern": "[", "regex": True},
        {"paths": ["private"]},
    ]:
        with pytest.raises(PolicyError):
            reader.grep(**({"pattern": "needle"} | kwargs))


@pytest.mark.parametrize("ancestor", [False, True])
def test_worktree_symlinks_do_not_expose_denied_targets(searching, ancestor):
    repo, root, reader = searching
    if ancestor:
        shutil.rmtree(root / "public")
        (root / "public").symlink_to(root / "private", target_is_directory=True)
    else:
        (root / "public/a.txt").unlink()
        (root / "public/a.txt").symlink_to(root / "private/a.txt")
    result = reader.grep("needle")
    assert "HIDDEN_SENTINEL" not in result
    assert "Some content is unavailable" in result


async def test_grep_uses_mcp_filters(searching):
    _, root, _ = searching
    from test_filters import Replace

    async with secure_connect(root, [Replace("needle", "MASKED")]) as session:
        result = await session.call_tool("git_grep", {"pattern": "needle"})
        assert not result.is_error
        assert "MASKED" in text(result) and "needle" not in text(result)
