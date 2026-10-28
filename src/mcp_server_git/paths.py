"""Shared logical-file enumeration and literal selection for policy-aware Git tools."""

import stat
from dataclasses import dataclass

from .policy import PathPolicy, PolicyError, normalize_path
from .repository import SecureRepository

REGULAR_MODES = {"100644", "100755"}


@dataclass(frozen=True)
class FileEntry:
    path: str
    mode: str
    oid: str | None = None


class PathSelection:
    def __init__(self, paths: list[str] | None, policy: PathPolicy):
        if paths == []:
            raise PolicyError("Empty path selection.")
        selections = []
        for path in paths or ["."]:
            if path == ".":
                selections.append("")
            else:
                normalized = normalize_path(path)
                policy.require(normalized)
                selections.append(normalized)
        self.paths = tuple(selections)

    def matches(self, path: str) -> bool:
        return any(not p or path == p or path.startswith(p + "/") for p in self.paths)


def entries(
    backend: SecureRepository,
    revision: str | None = None,
    include_untracked: bool = False,
) -> list[FileEntry]:
    if revision is not None and include_untracked:
        raise PolicyError("Untracked files have no commit revision.")
    result = {}
    if revision is not None:
        sha = backend.commit_ref(revision)
        raw = backend.run("ls-tree", "-rz", "--full-tree", sha)
    else:
        raw = backend.run("ls-files", "--stage", "-z")
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        fields = header.decode("ascii").split()
        path = name.decode("utf-8", errors="surrogateescape")
        mode = fields[0]
        if revision is None:
            if fields[2] != "0":
                mode = "unmerged"
            oid = fields[1]
        else:
            oid = fields[2]
        result[path] = FileEntry(path, mode, oid)
    if include_untracked:
        raw = backend.run("ls-files", "--others", "--exclude-standard", "-z")
        for name in raw.split(b"\0"):
            if not name:
                continue
            path = name.decode("utf-8", errors="surrogateescape")
            try:
                mode = (
                    "100644"
                    if stat.S_ISREG((backend.root / path).lstat().st_mode)
                    else "other"
                )
            except OSError:
                mode = "other"
            result[path] = FileEntry(path, mode)
    return list(result.values())


def selected_files(
    backend: SecureRepository,
    paths: list[str] | None = None,
    revision: str | None = None,
    include_untracked: bool = False,
) -> tuple[list[FileEntry], bool]:
    selection = PathSelection(paths, backend.policy)
    visible = []
    hidden = False
    for entry in entries(backend, revision, include_untracked):
        if not selection.matches(entry.path):
            continue
        if entry.mode in REGULAR_MODES and backend.policy.allows(entry.path):
            visible.append(entry)
        else:
            hidden = True
    return visible, hidden


def require_working_file(
    backend: SecureRepository, path: str, missing_ok: bool = False
) -> None:
    """Never read or update a working file through a symlink ancestor."""
    from pathlib import Path

    backend.policy.require(path)
    candidate = backend.root
    try:
        for component in Path(path).parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise PolicyError("Access denied according to project policy.")
        backend.policy.require(candidate.resolve().relative_to(backend.root).as_posix())
        if candidate.exists() and not candidate.is_file():
            raise PolicyError("Access denied according to project policy.")
        if not missing_ok and not candidate.is_file():
            raise PolicyError("Working file unavailable.")
    except (ValueError, OSError, RuntimeError):
        raise PolicyError("Access denied according to project policy.") from None


def exact_pathspecs(paths: list[str]) -> list[str]:
    return [
        spec
        for path in dict.fromkeys(paths)
        for spec in (f":(top,literal){path}", f":(top,exclude,literal){path}/")
    ]
