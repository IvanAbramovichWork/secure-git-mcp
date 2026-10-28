"""Validate complete affected sets and check rejected writes leave all state intact."""
from pathlib import Path

import git
import pytest

from mcp_server_git.policy import PathPolicy, PolicyError
from mcp_server_git.repository import SecureRepository
from mcp_server_git.write_tools import WriteTools


@pytest.fixture
def secured(repo):
    root = Path(repo.working_dir)
    (root / 'private').mkdir()
    (root / 'private/key').write_text('HIDDEN\n')
    repo.git.add('.')
    repo.index.commit('seed')
    backend = SecureRepository(root, PathPolicy.from_json('{"deny":["^private$"]}'))
    yield repo, root, WriteTools(backend)
    backend.repo.close()


def snapshot(repo, root):
    files = {str(p.relative_to(root)): p.read_bytes() if not p.is_symlink() else str(p.readlink()).encode() for p in root.rglob('*') if '.git' not in p.parts and (p.is_file() or p.is_symlink())}
    return (Path(repo.git_dir, 'index').read_bytes() if Path(repo.git_dir, 'index').exists() else None, repo.git.rev_parse('--verify', 'HEAD') if repo.head.is_valid() else None, repo.git.show_ref() if repo.head.is_valid() else '', files)


@pytest.mark.parametrize('selection', [['private/key'], ['private'], ['.'], ['test.txt', 'private/key']])
def test_rejected_add_has_no_partial_effect(secured, selection):
    repo, root, writes = secured
    (root / 'test.txt').write_text('ALLOWED\n')
    (root / 'private/key').write_text('HIDDEN_NEW\n')
    before = snapshot(repo, root)
    with pytest.raises(PolicyError):
        writes.add(selection)
    assert snapshot(repo, root) == before


def test_allowed_workflow_with_untouched_denied_files(secured):
    repo, root, writes = secured
    (root / 'test.txt').write_text('ALLOWED_NEW\n')
    writes.add(['test.txt'])
    writes.reset()
    assert repo.git.diff('--cached') == ''
    writes.add(['test.txt'])
    previous = repo.head.commit.hexsha
    writes.commit('allowed commit')
    assert repo.head.commit.hexsha != previous
    writes.create_branch('allowed-branch')
    writes.checkout('allowed-branch')
    assert repo.active_branch.name == 'allowed-branch'


@pytest.mark.parametrize('operation', ['commit', 'reset'])
def test_staged_denied_content_blocks_global_writes(secured, operation):
    repo, root, writes = secured
    (root / 'private/key').write_text('HIDDEN_NEW\n')
    (root / 'test.txt').write_text('ALLOWED_NEW\n')
    repo.git.add('.')
    before = snapshot(repo, root)
    with pytest.raises(PolicyError):
        writes.commit('message') if operation == 'commit' else writes.reset()
    assert snapshot(repo, root) == before


def test_checkout_denied_tree_change_and_rename(secured):
    repo, root, writes = secured
    previous = repo.head.commit.hexsha
    repo.git.mv('private/key', 'moved')
    repo.git.add('.')
    repo.index.commit('rename')
    before = snapshot(repo, root)
    with pytest.raises(PolicyError):
        writes.checkout(previous)
    assert snapshot(repo, root) == before


@pytest.mark.parametrize('target', ['../private/key', '/etc/passwd'])
def test_add_symlink_target_denied(secured, target):
    repo, root, writes = secured
    (root / 'public').mkdir()
    (root / 'public/link').symlink_to(target)
    before = snapshot(repo, root)
    with pytest.raises(PolicyError):
        writes.add(['public'])
    assert snapshot(repo, root) == before


@pytest.mark.parametrize('name', ['literal*.txt', ':(glob)*', '-option.txt'])
def test_add_literal_filename(secured, name):
    repo, root, writes = secured
    (root / name).write_text('ALLOWED\n')
    writes.add([name])
    assert name in repo.git.diff('--cached', '--name-only', '-z').split('\x00')


@pytest.mark.parametrize('state', ['MERGE_HEAD', 'MERGE_AUTOSTASH', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'])
def test_in_progress_operations_rejected(secured, state):
    repo, root, writes = secured
    (root / 'test.txt').write_text('new\n')
    repo.git.add('test.txt')
    marker = Path(repo.git_dir, state)
    marker.mkdir() if '-' in state or state == 'sequencer' else marker.write_text(repo.head.commit.hexsha)
    before = snapshot(repo, root)
    with pytest.raises(PolicyError):
        writes.commit('disallowed')
    with pytest.raises(PolicyError):
        writes.checkout('main')
    assert snapshot(repo, root) == before


def test_commit_and_checkout_do_not_run_hooks(secured):
    repo, root, writes = secured
    hooks = Path(repo.git_dir, 'hooks')
    for hook in ['pre-commit', 'prepare-commit-msg', 'post-commit', 'post-checkout']:
        path = hooks / hook
        path.write_text('#!/bin/sh\ntouch "' + str(root / 'HOOK_RAN') + '"\n')
        path.chmod(0o755)
    (root / 'test.txt').write_text('allowed\n')
    writes.add(['test.txt'])
    writes.commit('message')
    writes.create_branch('new')
    writes.checkout('new')
    assert not (root / 'HOOK_RAN').exists()


def test_unmerged_index_and_gitlink_rejected(secured):
    repo, root, writes = secured
    sha = repo.head.commit.hexsha
    repo.git.update_index('--add', '--cacheinfo', f'160000,{sha},submodule')
    with pytest.raises(PolicyError):
        writes.commit('gitlink')
    repo.git.reset()
    blob = repo.git.rev_parse('HEAD:test.txt')
    data = f'0 {"0" * 40}\ttest.txt\n100644 {blob} 1\ttest.txt\n100644 {blob} 2\ttest.txt\n'
    __import__('subprocess').run(['git', '-C', str(root), 'update-index', '--index-info'], input=data.encode(), check=True, capture_output=True)
    with pytest.raises(PolicyError):
        writes.commit('unmerged')


def test_unborn_commit_and_reset(tmp_path):
    repo = git.Repo.init(tmp_path / 'unborn')
    with repo.config_writer() as cfg:
        cfg.set_value('user', 'name', 'Test').set_value('user', 'email', 'test@example.com')
    root = Path(repo.working_dir)
    writes = WriteTools(SecureRepository(root, PathPolicy.from_json('{"deny":[]}')))
    (root / 'allowed').write_text('new')
    writes.add(['allowed'])
    writes.reset()
    assert repo.git.ls_files() == ''
    with pytest.raises(PolicyError):
        writes.checkout('main')
    writes.add(['allowed'])
    writes.commit('first commit')
    assert repo.head.is_valid()
    repo.close()
