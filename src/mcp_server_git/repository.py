"""Bind all policy operations to exactly one working tree and shared execution lock."""

from dataclasses import dataclass
import threading
from pathlib import Path

import git

from .policy import DENIED, PathPolicy, PolicyError


class SecureRepository:
    def __init__(self, root: Path, policy: PathPolicy):
        try:
            self.repo = git.Repo(root, search_parent_directories=True)
            if self.repo.bare or self.repo.working_tree_dir is None:
                raise PolicyError(DENIED)
            self.root = Path(self.repo.working_tree_dir).resolve()
        except (git.GitError, OSError):
            raise PolicyError("Invalid configured repository.") from None
        self.policy = policy
        self.lock = threading.RLock()

    def validate_repo_path(self, path: str) -> None:
        try:
            candidate = Path(path).resolve(strict=True)
            # Only the configured root is a valid tool repository, including real aliases.
            if candidate != self.root:
                raise PolicyError(DENIED)
            with git.Repo(candidate) as repo:
                if Path(repo.git_dir).resolve() != Path(self.repo.git_dir).resolve():
                    raise PolicyError(DENIED)
        except (git.GitError, OSError, RuntimeError):
            raise PolicyError(DENIED) from None

    def run(self, *args: str, literal_paths: bool = True) -> bytes:
        """No shell, literal paths, no hooks/pagers, and no raw stderr in responses."""
        import os
        import subprocess

        env = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_GLOB_PATHSPECS",
            "GIT_NOGLOB_PATHSPECS",
            "GIT_ICASE_PATHSPECS",
        ):
            env.pop(name, None)
        env.update(
            GIT_LITERAL_PATHSPECS="1" if literal_paths else "0",
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_TERMINAL_PROMPT="0",
        )
        cmd = [
            "git",
            "-C",
            str(self.root),
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "submodule.recurse=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.quotePath=true",
            *args,
        ]
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise PolicyError(
                "Git operation failed; details withheld according to project policy."
            ) from None
        if result.returncode:
            raise PolicyError(
                "Git operation failed; details withheld according to project policy."
            )
        return result.stdout

    def commit_ref(self, revision: str) -> str:
        import re

        if (
            not revision
            or revision.startswith("-")
            or ":" in revision
            or "\x00" in revision
            or any(c.isspace() for c in revision)
        ):
            raise PolicyError(DENIED)
        value = (
            self.run(
                "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise PolicyError(DENIED)
        return value

    def head(self) -> str | None:
        try:
            return self.commit_ref("HEAD")
        except PolicyError:
            # Missing HEAD is only an unborn branch when its symbolic ref does not exist.
            ref = self.run("symbolic-ref", "-q", "HEAD").decode().strip()
            if not ref.startswith("refs/heads/"):
                raise PolicyError(DENIED)
            try:
                self.run("show-ref", "--verify", ref)
            except PolicyError:
                return None
            raise

    def changes(self, *args: str) -> list["Change"]:
        raw = self.run(
            "diff",
            "--raw",
            "--ita-visible-in-index",
            "-z",
            "--no-abbrev",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            "--ignore-submodules=none",
            *args,
            "--",
        )
        return parse_changes(raw)

    def commit_changes(self, commit: str) -> list["Change"]:
        # --root includes an initial commit, --no-commit-id avoids a leading SHA record.
        raw = self.run(
            "diff-tree",
            "--root",
            "--no-commit-id",
            "-r",
            "-z",
            "--raw",
            "--no-abbrev",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            commit,
            "--",
        )
        # Merge commits are compared to the first parent, just like git show.
        parents = (
            self.run("rev-list", "--parents", "-n", "1", commit).decode().split()[1:]
        )
        if parents:
            return self.changes(parents[0], commit)
        return parse_changes(raw)


@dataclass(frozen=True)
class Change:
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    kind: str

    def allowed(self, policy: PathPolicy) -> bool:
        return (
            policy.allows(self.old_path)
            and policy.allows(self.new_path)
            and self.old_mode not in ("120000", "160000")
            and self.new_mode not in ("120000", "160000")
        )


def parse_changes(raw: bytes) -> list[Change]:
    records = iter(raw.split(b"\0"))
    changes = []
    for header in records:
        if not header:
            continue
        fields = header.decode("ascii").split()
        old = next(records).decode("utf-8", errors="surrogateescape")
        new = (
            next(records).decode("utf-8", errors="surrogateescape")
            if fields[4][0] in "RC"
            else old
        )
        changes.append(Change(old, new, fields[0][1:], fields[1], fields[4][0]))
    return changes
