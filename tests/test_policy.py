"""Policy inputs fail closed and cannot widen the configured repository."""

import json
from pathlib import Path

import git
import pytest

from mcp_server_git.policy import PathPolicy, PolicyError, load_policy
from mcp_server_git.repository import SecureRepository


@pytest.mark.parametrize(
    "value",
    [
        "",
        "{}",
        "{",
        '{"deny":"private"}',
        '{"deny":[1]}',
        '{"deny":["["]}',
        '{"deny":[],"allow":true}',
    ],
)
def test_invalid_policy(value):
    with pytest.raises(PolicyError):
        PathPolicy.from_json(value)


def test_regex_and_ancestor_semantics():
    p = PathPolicy.from_json('{"deny":["^private$","^modules/payments(/|$)"]}')
    for name in ["private", "private/x/a.py", "modules/payments/a.py"]:
        assert not p.allows(name)
    for name in ["public/x.py", "modules/payment/a.py", "notprivate/a.py"]:
        assert p.allows(name)


@pytest.mark.parametrize(
    "name",
    ["/etc/passwd", "../x", "a/../x", ".git/config", "a/.git/x", "a\\b", "x\x00y", ""],
)
def test_invalid_paths_denied_even_with_empty_policy(name):
    assert not PathPolicy.from_json('{"deny":[]}').allows(name)


def test_config_precedence_and_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("SECURE_GIT_POLICY", raising=False)
    with pytest.raises(PolicyError):
        load_policy(None)
    monkeypatch.setenv("SECURE_GIT_POLICY", '{"deny":[]}')
    assert load_policy(None).allows("private/a")
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "secure-git": {"env": {"SECURE_GIT_POLICY": '{"deny":["private"]}'}}
                }
            }
        )
    )
    assert not load_policy(cfg).allows("private/a")
    cfg.write_text("{}")
    with pytest.raises(PolicyError):
        load_policy(cfg)


def test_fixed_repository(repo, tmp_path):
    root = Path(repo.working_dir)
    secure = SecureRepository(root, PathPolicy.from_json('{"deny":[]}'))
    secure.validate_repo_path(str(root / "."))
    other = git.Repo.init(tmp_path / "other")
    nested = git.Repo.init(root / "nested")
    for name in [
        str(other.working_dir),
        str(nested.working_dir),
        str(root / "missing"),
    ]:
        with pytest.raises(PolicyError):
            secure.validate_repo_path(name)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    secure.validate_repo_path(str(alias))
    other.close()
    nested.close()


def test_bare_repository_rejected(tmp_path):
    bare = git.Repo.init(tmp_path / "bare", bare=True)
    with pytest.raises(PolicyError):
        SecureRepository(Path(bare.git_dir), PathPolicy.from_json('{"deny":[]}'))
    bare.close()


def test_linked_worktree_is_bound_to_its_own_root(repo, tmp_path):
    linked = tmp_path / "linked"
    repo.git.worktree("add", "-b", "linked-branch", str(linked))
    secure = SecureRepository(linked, PathPolicy.from_json('{"deny":[]}'))
    secure.validate_repo_path(str(linked))
    with pytest.raises(PolicyError):
        secure.validate_repo_path(str(repo.working_dir))
    secure.repo.close()
