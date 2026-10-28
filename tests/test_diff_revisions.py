"""Compare committed trees, preserving path and rename boundary checks."""

from pathlib import Path
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from test_secure_protocol import secure_connect, text


@pytest.fixture
def compared(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/key").write_text("HIDDEN_OLD\n")
    (root / "literal*.txt").write_text("VISIBLE_OLD\n")
    repo.git.add(".")
    base = repo.index.commit("base").hexsha
    (root / "private/key").write_text("HIDDEN_NEW\n")
    (root / "literal*.txt").write_text("VISIBLE_NEW\n")
    (root / "other.txt").write_text("OTHER\n")
    repo.git.add(".")
    target = repo.index.commit("target").hexsha
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, ReadTools(backend), base, target
    backend.repo.close()


def test_two_commits_ignore_uncommitted_worktree_and_filter_denies(compared):
    repo, root, reader, base, target = compared
    (root / "literal*.txt").write_text("UNCOMMITTED\n")
    result = reader.diff_revisions(base, target)
    assert "VISIBLE_OLD" in result and "VISIBLE_NEW" in result
    assert all(x not in result for x in ["UNCOMMITTED", "HIDDEN", "private"])
    assert "Some content is unavailable" in result
    assert reader.diff_revisions(target, target) == ""


def test_diff_literal_path_filter_and_validation(compared):
    _, _, reader, base, target = compared
    result = reader.diff_revisions(base, target, paths=["literal*.txt"])
    assert "VISIBLE_NEW" in result and "OTHER" not in result
    assert reader.diff_revisions(base, target, paths=["missing"]) == ""
    for kwargs in [
        {"base": "--output=forbidden"},
        {"paths": ["private"]},
        {"paths": []},
        {"context_lines": -1},
    ]:
        with pytest.raises(PolicyError):
            reader.diff_revisions(**({"base": base, "target": target} | kwargs))


def test_boundary_rename_is_omitted(compared):
    repo, root, reader, _, base = compared
    repo.git.mv("private/key", "renamed.txt")
    target = repo.index.commit("rename").hexsha
    result = reader.diff_revisions(base, target, paths=["renamed.txt"])
    assert "HIDDEN" not in result and "renamed.txt" not in result
    assert "Some content is unavailable" in result


def test_file_directory_transition_cannot_reintroduce_denied_patch(repo):
    root = Path(repo.working_dir)
    (root / "slot").write_text("VISIBLE\n")
    repo.git.add(".")
    base = repo.index.commit("file").hexsha
    (root / "slot").unlink()
    (root / "slot").mkdir()
    (root / "slot/key").write_text("HIDDEN\n")
    repo.git.add(".")
    target = repo.index.commit("directory").hexsha
    reader = ReadTools(
        SecureRepository(root, PathPolicy.from_json('{"deny":["^slot/key$"]}'))
    )
    result = reader.diff_revisions(base, target)
    assert "VISIBLE" in result and "HIDDEN" not in result


async def test_two_revision_diff_registered(compared):
    _, root, _, base, target = compared
    async with secure_connect(root) as session:
        result = await session.call_tool(
            "git_diff_revisions",
            {"base": base, "target": target, "paths": ["literal*.txt"]},
        )
        assert not result.is_error and "VISIBLE_NEW" in text(result)
