"""List only selected permitted regular paths, without filename/pathspec ambiguity."""

import json
from pathlib import Path
import pytest
from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from test_secure_protocol import secure_connect, text


@pytest.fixture
def listing(repo):
    root = Path(repo.working_dir)
    for name in [
        "public/a.txt",
        "private/key",
        "public/new\nline",
        "public/literal*.txt",
    ]:
        p = root / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("content")
    (root / "public/link").symlink_to("../private/key")
    repo.git.add(".")
    repo.index.commit("files")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, ReadTools(backend)
    backend.repo.close()


def test_ls_files_filters_modes_and_escapes_names(listing):
    repo, root, reader = listing
    result = reader.ls_files()
    assert "private" not in result and "link" not in result
    assert "public/a.txt" in result
    assert "public/new\\nline" in result
    assert "Some content is unavailable" in result


def test_selectors_are_literal_and_history_is_supported(listing):
    repo, root, reader = listing
    assert reader.ls_files(paths=["public/literal*.txt"]) == json.dumps(
        "public/literal*.txt"
    )
    assert "private" not in reader.ls_files(paths=["public"])
    sha = repo.head.commit.hexsha
    (root / "public/a.txt").unlink()
    repo.git.add(".")
    repo.index.commit("delete")
    assert "public/a.txt" not in reader.ls_files()
    assert "public/a.txt" in reader.ls_files(revision=sha)
    for paths in [[], ["private"], ["../outside"]]:
        with pytest.raises(PolicyError):
            reader.ls_files(paths=paths)


def test_untracked_is_opt_in_and_ignored_files_are_excluded(listing):
    repo, root, reader = listing
    (root / "new.txt").write_text("new")
    (root / "private/untracked").write_text("hidden")
    (root / ".gitignore").write_text("ignored\n")
    (root / "ignored").write_text("ignored")
    assert "new.txt" not in reader.ls_files()
    result = reader.ls_files(include_untracked=True)
    assert "new.txt" in result and "ignored" not in result and "private" not in result
    with pytest.raises(PolicyError):
        reader.ls_files(revision="HEAD", include_untracked=True)


async def test_ls_files_is_registered_and_filter_pipeline_applies(listing):
    repo, root, _ = listing
    from test_filters import Replace

    async with secure_connect(root, [Replace("public", "REDACTED")]) as session:
        result = await session.call_tool("git_ls_files", {"paths": ["public"]})
        assert not result.is_error
        assert "REDACTED" in text(result) and "public" not in text(result)
