"""Specific trusted error codes help callers without revealing raw arguments or stderr."""

from pathlib import Path
from unittest.mock import patch
import subprocess

import pytest

from test_secure_protocol import secure_connect, text


async def test_wrong_repository_is_not_reported_as_content_denial(repo):
    async with secure_connect(Path(repo.working_dir)) as session:
        result = await session.call_tool(
            "git_status", {"repo_path": "/SECRET_HOST_PATH"}
        )
        assert result.is_error
        assert "REPOSITORY_MISMATCH" in text(result)
        assert "Omit repo_path" in text(result)
        assert "SECRET" not in text(result)
        assert "Access denied" not in text(result)


async def test_sdk_validation_is_distinct_and_never_echoes_input(repo):
    async with secure_connect(Path(repo.working_dir)) as session:
        result = await session.call_tool("git_diff", {"target": {"SECRET": "VALUE"}})
        assert result.is_error
        assert "INVALID_ARGUMENTS" in text(result)
        assert "SECRET" not in text(result)
        result = await session.call_tool("git_show", {"revision": "HEAD:private/key"})
        assert "POLICY_DENIED" in text(result)
        assert "Access denied according to project policy." in text(result)


@pytest.mark.parametrize(
    "failure,code",
    [
        ("ownership", "GIT_OWNERSHIP"),
        ("timeout", "GIT_TIMEOUT"),
        ("failed", "GIT_FAILED"),
    ],
)
async def test_git_failures_have_safe_classification(repo, failure, code):
    original = subprocess.run

    def run(command, **kwargs):
        if isinstance(command, list) and "--porcelain=v1" in command:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(
                    command, 60, stderr=b"SECRET_DIAGNOSTIC"
                )
            stderr = (
                b"fatal: detected dubious ownership in repository at 'SECRET_PATH'\n"
                if failure == "ownership"
                else b"SECRET_DIAGNOSTIC"
            )
            return subprocess.CompletedProcess(command, 128, b"", stderr)
        return original(command, **kwargs)

    async with secure_connect(Path(repo.working_dir)) as session:
        with patch("subprocess.run", run):
            result = await session.call_tool("git_status", {})
        assert result.is_error
        assert code in text(result)
        assert "SECRET" not in text(result)


async def test_custom_filters_also_process_classified_errors(repo):
    from test_filters import Replace

    async with secure_connect(
        Path(repo.working_dir), [Replace("REPOSITORY_MISMATCH", "FILTERED")]
    ) as session:
        result = await session.call_tool("git_status", {"repo_path": "/invalid"})
        assert "FILTERED" in text(result)


def test_invalid_regex_cannot_spoof_git_ownership_classification(repo):
    from mcp_server_git.errors import SafeGitError
    from mcp_server_git.policy import PathPolicy
    from mcp_server_git.repository import SecureRepository

    backend = SecureRepository(
        Path(repo.working_dir), PathPolicy.from_json('{"deny":[]}')
    )
    with pytest.raises(SafeGitError) as exc:
        backend.run(
            "grep",
            "-E",
            "-e",
            "[detected dubious ownership in repository",
            "--",
            "test.txt",
        )
    assert exc.value.code == "GIT_FAILED"
