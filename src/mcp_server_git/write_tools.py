"""Guard complete write effects before executing any mutation, under the repository lock."""

from functools import wraps
from pathlib import Path
from typing import Callable, ParamSpec, TypeVar

from .policy import DENIED, PolicyError, normalize_path
from .repository import Change, SecureRepository

_P = ParamSpec("_P")
_R = TypeVar("_R")


def locked(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        instance = args[0]
        with instance.backend.lock:  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

    return wrapper


class WriteTools:
    def __init__(self, repository: SecureRepository):
        self.backend = repository

    def _clean_operation_state(self) -> None:
        for name in (
            "MERGE_HEAD",
            "MERGE_AUTOSTASH",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        ):
            if Path(self.backend.repo.git_dir, name).exists():
                raise PolicyError(
                    "In-progress Git operation is unavailable according to project policy."
                )
        if self.backend.run("ls-files", "--unmerged", "-z"):
            raise PolicyError(
                "Unmerged index is unavailable according to project policy."
            )

    def _require_changes(self, changes: list[Change]) -> None:
        if any(not c.allowed(self.backend.policy) for c in changes):
            raise PolicyError(DENIED)

    def _require_worktree_path(self, path: str) -> None:
        self.backend.policy.require(path)
        try:
            resolved = (self.backend.root / path).resolve()
            target = resolved.relative_to(self.backend.root).as_posix()
        except (ValueError, OSError, RuntimeError):
            raise PolicyError(DENIED) from None
        self.backend.policy.require(target)

    @locked
    def add(self, files: list[str]) -> str:
        self._clean_operation_state()
        if not files:
            raise PolicyError("No files supplied.")
        selections = []
        for name in files:
            if name == ".":
                selections.append(".")
            else:
                name = normalize_path(name)
                self._require_worktree_path(name)
                selections.append(name)
        # ls-files also returns tracked deletions; -z and literal pathspecs preserve filenames.
        raw = self.backend.run(
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *selections,
        )
        paths = list(
            dict.fromkeys(
                p.decode("utf-8", errors="surrogateescape")
                for p in raw.split(b"\0")
                if p
            )
        )
        modes = self.backend.run("ls-files", "--stage", "-z", "--", *selections)
        if any(record.startswith(b"160000 ") for record in modes.split(b"\0")):
            raise PolicyError(DENIED)
        for path in paths:
            self._require_worktree_path(path)
            # ls-files emits a directory for an untracked embedded repository.
            # Never stage a gitlink whose descendant policy cannot be evaluated.
            if (self.backend.root / path).is_dir():
                raise PolicyError(DENIED)
        if not paths:
            return "No matching files were staged."
        # Stage only the exact validated expansion, including tracked deletions.
        self.backend.run("add", "--all", "--", *paths)
        return "Files staged successfully"

    @locked
    def commit(self, message: str) -> str:
        self._clean_operation_state()
        changes = self.backend.changes("--cached")
        self._require_changes(changes)
        if not changes:
            raise PolicyError("No changes staged for commit.")
        self.backend.run(
            "-c", "commit.gpgSign=false", "commit", "--no-verify", "-m", message
        )
        return "Changes committed successfully with hash " + str(self.backend.head())

    @locked
    def reset(self) -> str:
        self._clean_operation_state()
        self._require_changes(self.backend.changes("--cached"))
        if self.backend.head() is None:
            self.backend.run("read-tree", "--empty")
        else:
            self.backend.run("reset", "--mixed", "HEAD", "--")
        return "All staged changes reset"

    @locked
    def create_branch(self, branch_name: str, base_branch: str | None = None) -> str:
        self._clean_operation_state()
        self._branch_name(branch_name)
        sha = self.backend.commit_ref(base_branch or "HEAD")
        self.backend.run("branch", "--", branch_name, sha)
        return "Branch created successfully"

    @locked
    def checkout(self, branch_name: str) -> str:
        self._clean_operation_state()
        sha = self.backend.commit_ref(branch_name)
        head = self.backend.head()
        if head is None:
            raise PolicyError(DENIED)
        self._require_changes(self.backend.changes(head, sha))
        self._require_changes(self.backend.changes("--cached"))
        self.backend.run(
            "checkout", "--no-recurse-submodules", "--no-overwrite-ignore", branch_name
        )
        return "Checkout completed successfully"

    def _branch_name(self, name: str) -> None:
        if not name or name.startswith("-") or "\x00" in name:
            raise PolicyError(DENIED)
        self.backend.run("check-ref-format", "--branch", name)
