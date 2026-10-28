"""Imported Claude denies remain additive, immutable, and independent of filesystem expansion."""

import json
from pathlib import Path

import pytest

from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.read_tools import ReadTools
from mcp_server_git.write_tools import WriteTools


def settings(root, deny):
    path = root / ".claude/settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"sandbox": {"filesystem": {"denyRead": deny, "allowRead": ["."]}}})
    )
    return path


def base():
    return PathPolicy.from_json('{"deny":["^mcp-only$"]}')


def test_missing_claude_file_falls_back_and_snapshot_is_frozen(repo):
    root = Path(repo.working_dir)
    p = base().with_claude_settings(root)
    assert p.allows("private/key")
    assert not p.allows("mcp-only/key")
    settings(root, ["private"])
    assert p.with_claude_settings(root).allows("private/key")
    assert not base().with_claude_settings(root).allows("private/key")


@pytest.mark.parametrize(
    "rule,denied,allowed",
    [
        ("private", "private/a/b", "notprivate/a"),
        ("./private/", "private/a", "public/a"),
        ("private.v1", "private.v1/a", "privateXv1/a"),
        ("*.pem", "root.pem", "nested/key.pem"),
        ("**/*.pem", "nested/key.pem", "nested/key.txt"),
        ("**/.env", ".env", ".env-example"),
        ("src/**/secret?.[ch]", "src/deep/secret1.c", "src/secret12.c"),
        ("**.pem", "deep/key.pem", "deep/key.txt"),
        ("config/[ab].key", "config/a.key", "config/c.key"),
        ("config/[*].key", "config/*.key", "config/x.key"),
        ("app/[user-id]/key", "app/[user-id]/key", "app/u/key"),
    ],
)
def test_globs_are_not_regex_and_cover_absent_history(repo, rule, denied, allowed):
    root = Path(repo.working_dir)
    settings(root, [rule])
    p = base().with_claude_settings(root)
    assert not p.allows(denied)
    assert p.allows(allowed)
    assert not p.allows("mcp-only/key")


@pytest.mark.parametrize(
    "value",
    [
        "{",
        "[]",
        '{"sandbox":null}',
        '{"sandbox":{"filesystem":[]}}',
        '{"sandbox":{"filesystem":{"denyRead":null}}}',
        '{"sandbox":{"filesystem":{"denyRead":[1]}}}',
    ],
)
def test_existing_invalid_file_never_falls_back(repo, value):
    root = Path(repo.working_dir)
    path = settings(root, [])
    path.write_text(value)
    with pytest.raises(PolicyError):
        base().with_claude_settings(root)


@pytest.mark.parametrize(
    "value", [{}, {"sandbox": {}}, {"sandbox": {"filesystem": {}}}]
)
def test_missing_section_is_empty(repo, value):
    root = Path(repo.working_dir)
    settings(root, []).write_text(json.dumps(value))
    p = base().with_claude_settings(root)
    assert p.allows("private/a") and not p.allows("mcp-only/a")


@pytest.mark.parametrize(
    "rule", ["", "~unknown_person/keys", "a/**/../keys", "a/**/", "a\\b"]
)
def test_unsupported_patterns_fail_closed(repo, rule):
    settings(Path(repo.working_dir), [rule])
    with pytest.raises(PolicyError):
        base().with_claude_settings(Path(repo.working_dir))


def test_absolute_ancestor_home_and_symlink_alias(repo, monkeypatch):
    root = Path(repo.working_dir)
    settings(root, [str(root.parent)])
    assert not base().with_claude_settings(root).allows("anything")
    settings(root, ["~/private"])
    monkeypatch.setattr(
        "os.path.expanduser", lambda p: str(root / "private") if p == "~/private" else p
    )
    assert not base().with_claude_settings(root).allows("private/key")
    (root / "private").mkdir()
    (root / "alias").symlink_to(root / "private", target_is_directory=True)
    settings(root, ["alias"])
    p = base().with_claude_settings(root)
    assert not p.allows("private/key") and not p.allows("alias/key")


def test_unreadable_and_dangling_file_are_errors(repo):
    root = Path(repo.working_dir)
    path = settings(root, [])
    path.chmod(0)
    try:
        with pytest.raises(PolicyError):
            base().with_claude_settings(root)
    finally:
        path.chmod(0o600)
    path.unlink()
    path.symlink_to(root / "missing-settings")
    with pytest.raises(PolicyError):
        base().with_claude_settings(root)


def test_all_repository_apis_inherit_read_and_write_policy(repo):
    root = Path(repo.working_dir)
    settings(root, ["claude-only"])
    (root / "claude-only").mkdir()
    (root / "claude-only/key").write_text("HIDDEN_FROM_CLAUDE")
    repo.git.add(".")
    sha = repo.index.commit("private history").hexsha
    (root / "claude-only/key").unlink()
    repo.git.add(".")
    repo.index.commit("delete private file")
    backend = SecureRepository(root, base())
    with pytest.raises(PolicyError):
        ReadTools(backend).show(sha + ":claude-only/key")
    assert "HIDDEN_FROM_CLAUDE" not in ReadTools(backend).show(sha)
    (root / "claude-only/key").write_text("new hidden")
    before = Path(repo.git_dir, "index").read_bytes()
    with pytest.raises(PolicyError):
        WriteTools(backend).add(["claude-only/key"])
    assert Path(repo.git_dir, "index").read_bytes() == before


def test_negated_bracket_and_literal_regex_characters(repo):
    root = Path(repo.working_dir)
    settings(root, ["private/[!!]", "config/a+b"])
    p = base().with_claude_settings(root)
    assert not p.allows("private/a")
    assert p.allows("private/!")
    assert not p.allows("config/a+b/key")
    assert p.allows("config/aaab/key")


def test_recursive_directory_glob_hides_directory_entry(repo):
    root = Path(repo.working_dir)
    settings(root, ["modules/payments/**"])
    p = base().with_claude_settings(root)
    assert not p.allows("modules/payments")
    assert not p.allows("modules/payments/deleted/key")
    assert p.allows("modules/other/key")


def test_repository_subdirectory_uses_root_settings(repo):
    root = Path(repo.working_dir)
    settings(root, ["private"])
    (root / "sub").mkdir()
    backend = SecureRepository(root / "sub", base())
    assert not backend.policy.allows("private/key")


def test_globstar_covers_newline_directories(repo):
    root = Path(repo.working_dir)
    settings(root, ["**/*.pem"])
    assert not base().with_claude_settings(root).allows("nested\nname/key.pem")


def test_repository_root_is_literal_even_with_glob_characters(tmp_path):
    root = tmp_path / "repo[ab]"
    root.mkdir()
    settings(root, ["**/*.pem"])
    assert not base().with_claude_settings(root).allows("nested/key.pem")


def test_symlink_parent_traversal_is_resolved_before_collapsing(repo):
    root = Path(repo.working_dir)
    (root / "nested/deep").mkdir(parents=True)
    (root / "alias").symlink_to(root / "nested/deep", target_is_directory=True)
    settings(root, ["alias/../secret"])
    assert not base().with_claude_settings(root).allows("nested/secret/key")


def test_canonical_symlink_prefix_keeps_brackets_literal(repo, tmp_path):
    root = Path(repo.working_dir)
    target = root / "target[ab]"
    target.mkdir()
    (root / "alias").symlink_to(target, target_is_directory=True)
    settings(root, ["alias/**/*.pem"])
    assert not base().with_claude_settings(root).allows("target[ab]/nested/key.pem")
