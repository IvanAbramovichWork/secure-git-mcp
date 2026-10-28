"""Blame validates historical provenance, not just the current allowed filename."""

import json
from pathlib import Path
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from test_secure_protocol import secure_connect, text


def reader(repo):
    return ReadTools(
        SecureRepository(
            Path(repo.working_dir), PathPolicy.from_json('{"deny":["^private$"]}')
        )
    )


def test_blame_regular_root_file_and_line_range(repo):
    result = reader(repo).blame("test.txt", start_line=2, end_line=3)
    rows = [json.loads(line) for line in result.splitlines()]
    assert [r["line"] for r in rows] == [2, 3]
    assert [r["text"] for r in rows] == ["line 2", "line 3"]
    assert rows[0]["author"] == "Test User"
    assert "truncated" in reader(repo).blame("test.txt", max_count=1).lower()


def test_mixed_commit_author_and_summary_are_withheld(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/key").write_text("HIDDEN_CONTENT")
    (root / "test.txt").write_text("PUBLIC_CHANGE\n")
    with repo.config_writer() as config:
        config.set_value("user", "name", "HIDDEN_AUTHOR")
    repo.git.add(".")
    repo.index.commit("HIDDEN_SUMMARY")
    result = reader(repo).blame("test.txt")
    assert "PUBLIC_CHANGE" in result and "withheld" in result
    assert all(
        s not in result for s in ["HIDDEN_AUTHOR", "HIDDEN_SUMMARY", "HIDDEN_CONTENT"]
    )


def test_renamed_denied_provenance_is_suppressed(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/old").write_text("HIDDEN_ORIGIN\n")
    repo.git.add(".")
    repo.index.commit("hidden original")
    repo.git.mv("private/old", "allowed.txt")
    repo.index.commit("rename")
    result = reader(repo).blame("allowed.txt")
    assert "HIDDEN_ORIGIN" not in result and "private" not in result
    assert "Some content is unavailable" in result


@pytest.mark.parametrize("name", ['quoted"name', "new\nline", "üñî.txt"])
def test_porcelain_quoted_paths(repo, name):
    root = Path(repo.working_dir)
    (root / name).write_text("visible\n")
    repo.git.add(".")
    repo.index.commit("unusual name")
    assert "visible" in reader(repo).blame(name)


def test_invalid_paths_objects_and_ranges_fail(repo):
    for path in ["private/key", "../file", ".git/config"]:
        with pytest.raises(PolicyError):
            reader(repo).blame(path)
    with pytest.raises(PolicyError):
        reader(repo).blame("test.txt", revision="HEAD:test.txt")
    with pytest.raises(PolicyError):
        reader(repo).blame("test.txt", start_line=0)
    with pytest.raises(PolicyError):
        reader(repo).blame("test.txt", start_line=3, end_line=2)
    root = Path(repo.working_dir)
    (root / "link").symlink_to("test.txt")
    repo.git.add("link")
    repo.index.commit("symlink")
    with pytest.raises(PolicyError):
        reader(repo).blame("link")


async def test_blame_registered_and_filtered(repo):
    from test_filters import Replace

    async with secure_connect(
        Path(repo.working_dir), [Replace("line", "MASKED")]
    ) as session:
        result = await session.call_tool("git_blame", {"path": "test.txt"})
        assert not result.is_error and "MASKED" in text(result)


def test_blame_normalizes_standard_relative_path_spellings(repo):
    root = Path(repo.working_dir)
    (root / "dir").mkdir()
    (root / "dir/file.txt").write_text("visible\n")
    repo.git.add(".")
    repo.index.commit("nested")
    r = reader(repo)
    assert r.blame("./test.txt") == r.blame("test.txt")
    assert r.blame("./dir//file.txt") == r.blame("dir/file.txt")
