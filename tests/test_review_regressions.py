"""Reproduce boundary failures found by independent review before accepting fixes."""

from pathlib import Path

import git
import pytest

from mcp_server_git.policy import PARTIAL, PathPolicy, PolicyError
from mcp_server_git.read_tools import ReadTools
from mcp_server_git.repository import SecureRepository
from mcp_server_git.write_tools import WriteTools


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("operation", ["staged", "target", "show"])
def test_file_to_directory_patch_never_includes_denied_descendants(
    repo, operation, reverse
):
    root = Path(repo.working_dir)
    slot = root / "slot"
    if reverse:
        slot.mkdir()
        (slot / "secret").write_text("HIDDEN_SENTINEL\n")
        (slot / "allowed").write_text("ALLOWED_BEFORE\n")
    else:
        slot.write_text("ALLOWED_BEFORE\n")
    repo.git.add(".")
    repo.index.commit("before")
    if reverse:
        __import__("shutil").rmtree(slot)
        slot.write_text("ALLOWED_AFTER\n")
    else:
        slot.unlink()
        slot.mkdir()
        (slot / "secret").write_text("HIDDEN_SENTINEL\n")
        (slot / "allowed").write_text("ALLOWED_AFTER\n")
    repo.git.add(".")
    reads = ReadTools(
        SecureRepository(root, PathPolicy.from_json('{"deny":["^slot/secret$"]}'))
    )
    if operation == "staged":
        text = reads.diff_staged()
    elif operation == "target":
        text = reads.diff("HEAD")
    else:
        repo.index.commit("mixed")
        text = reads.show("HEAD")
    assert "HIDDEN_SENTINEL" not in text
    assert "slot/secret" not in text
    assert "ALLOWED_BEFORE" in text
    assert "ALLOWED_AFTER" in text
    assert PARTIAL in text


def test_denied_intent_to_add_cannot_be_unstaged(repo):
    root = Path(repo.working_dir)
    (root / "private").mkdir()
    (root / "private/secret").write_text("HIDDEN_SENTINEL\n")
    repo.git.add("-N", "private/secret")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    before = Path(repo.git_dir, "index").read_bytes()
    with pytest.raises(PolicyError):
        WriteTools(backend).reset()
    assert Path(repo.git_dir, "index").read_bytes() == before
    assert PARTIAL in ReadTools(backend).diff_staged()


def test_checkout_cannot_delete_ignored_denied_descendant(repo):
    root = Path(repo.working_dir)
    (root / ".gitignore").write_text("slot/\n")
    repo.git.add(".")
    repo.index.commit("ignore slot directory")
    repo.git.checkout("-b", "target")
    (root / "slot").write_text("ALLOWED_TARGET_FILE\n")
    repo.git.add(".")
    repo.index.commit("target regular file")
    repo.git.checkout("main")
    (root / "slot").mkdir()
    secret = root / "slot/secret"
    secret.write_text("HIDDEN_SENTINEL\n")
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^slot/secret$"]}'))
    before = Path(repo.git_dir, "index").read_bytes(), repo.head.commit.hexsha
    with pytest.raises(PolicyError):
        WriteTools(backend).checkout("target")
    assert secret.read_text() == "HIDDEN_SENTINEL\n"
    assert (Path(repo.git_dir, "index").read_bytes(), repo.head.commit.hexsha) == before


@pytest.mark.parametrize("tracked", [False, True])
def test_add_never_stages_embedded_repository_or_gitlink(repo, tracked):
    root = Path(repo.working_dir)
    inner = git.Repo.init(root / "nested")
    with inner.config_writer() as cfg:
        cfg.set_value("user", "name", "Test").set_value(
            "user", "email", "test@example.com"
        )
    (root / "nested/private").mkdir()
    secret = root / "nested/private/secret"
    secret.write_text("HIDDEN_OLD\n")
    inner.git.add(".")
    inner.index.commit("inner seed")
    if tracked:
        repo.git.add("nested")
        repo.index.commit("outer gitlink")
        secret.write_text("HIDDEN_NEW\n")
        inner.git.add(".")
        inner.index.commit("inner change")
    backend = SecureRepository(
        root, PathPolicy.from_json('{"deny":["^nested/private$"]}')
    )
    before = Path(repo.git_dir, "index").read_bytes()
    with pytest.raises(PolicyError):
        WriteTools(backend).add(["nested"])
    assert Path(repo.git_dir, "index").read_bytes() == before
    inner.close()


def test_exact_patch_selection_preserves_allowed_rename_into_own_directory(repo):
    root = Path(repo.working_dir)
    slot = root / "slot"
    slot.write_text("ALLOWED_SAME_CONTENT\n")
    repo.git.add(".")
    repo.index.commit("file")
    slot.unlink()
    slot.mkdir()
    (slot / "allowed").write_text("ALLOWED_SAME_CONTENT\n")
    (slot / "secret").write_text("HIDDEN_SENTINEL\n")
    repo.git.add(".")
    reads = ReadTools(
        SecureRepository(root, PathPolicy.from_json('{"deny":["^slot/secret$"]}'))
    )
    text = reads.diff_staged()
    assert "HIDDEN_SENTINEL" not in text
    assert "+++ b/slot/allowed" in text
    assert "-ALLOWED_SAME_CONTENT" in text
    assert "+ALLOWED_SAME_CONTENT" in text
