"""Render Git reads only after whole-path records pass the project policy."""

import json
from pathlib import PurePosixPath

from .policy import DENIED, PARTIAL, PolicyError, normalize_path
from .repository import Change, SecureRepository


def bounded(value: int, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise PolicyError("Invalid numeric argument.")
    return value


def with_notice(text: str, hidden: bool) -> str:
    return text + ("\n" + PARTIAL if hidden else "")


class ReadTools:
    def __init__(self, repository: SecureRepository):
        self.backend = repository
        self.policy = repository.policy

    def status(self) -> str:
        raw = self.backend.run(
            "status",
            "--porcelain=v1",
            "--renames",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
        records = iter(raw.split(b"\0"))
        visible = []
        hidden = False
        for record in records:
            if not record:
                continue
            state = record[:2].decode("ascii")
            path = record[3:].decode("utf-8", errors="surrogateescape")
            old = (
                next(records).decode("utf-8", errors="surrogateescape")
                if "R" in state or "C" in state
                else path
            )
            if self.policy.allows(path) and self.policy.allows(old):
                visible.append(f"{state} {json.dumps(path, ensure_ascii=True)}")
            else:
                hidden = True
        return with_notice("\n".join(visible), hidden)

    def _patch(self, changes: list[Change], args: list[str], context_lines: int) -> str:
        bounded(context_lines, 10000)
        output = []
        hidden = False
        for change in changes:
            if not change.allowed(self.policy):
                hidden = True
                continue
            patch = self.backend.run(
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--ignore-submodules=all",
                f"--unified={context_lines}",
                *args,
                "--",
                change.old_path,
                change.new_path,
            )
            output.append(patch.decode("utf-8", errors="replace"))
        return with_notice("".join(output), hidden)

    def diff_unstaged(self, context_lines: int = 3) -> str:
        return self._patch(self.backend.changes(), [], context_lines)

    def diff_staged(self, context_lines: int = 3) -> str:
        return self._patch(
            self.backend.changes("--cached"), ["--cached"], context_lines
        )

    def diff(self, target: str, context_lines: int = 3) -> str:
        sha = self.backend.commit_ref(target)
        return self._patch(self.backend.changes(sha), [sha], context_lines)

    def _metadata(self, sha: str, hidden: bool) -> str:
        if hidden:
            return f"Commit: {sha}\nCommit metadata withheld according to project policy.\n"
        return self.backend.run(
            "show",
            "-s",
            "--format=Commit: %H%nAuthor: %an <%ae>%nDate: %aI%nMessage: %B",
            sha,
            "--",
        ).decode("utf-8", errors="replace")

    def show(self, revision: str) -> str:
        if ":" in revision:
            ref, path = revision.split(":", 1)
            sha = self.backend.commit_ref(ref)
            if path:
                path = normalize_path(path)
                self.policy.require(path)
            tree = (
                self.backend.run("ls-tree", "-z", sha, "--", path)
                if path
                else self.backend.run("ls-tree", "-z", sha)
            )
            if path:
                entries = [r for r in tree.split(b"\0") if r]
                if len(entries) != 1:
                    raise PolicyError("Requested Git object is unavailable.")
                header, _ = entries[0].split(b"\t", 1)
                mode, kind, oid = header.decode().split()
                if kind == "blob" and mode != "120000":
                    return self.backend.run("cat-file", "blob", oid).decode(
                        "utf-8", errors="replace"
                    )
                if kind != "tree":
                    raise PolicyError(DENIED)
                tree = self.backend.run("ls-tree", "-z", oid)
            visible = []
            hidden = False
            for record in tree.split(b"\0"):
                if not record:
                    continue
                header, raw_name = record.split(b"\t", 1)
                name = raw_name.decode("utf-8", errors="surrogateescape")
                full = str(PurePosixPath(path) / name) if path else name
                if self.policy.allows(full):
                    visible.append(name + ("/" if header.split()[1] == b"tree" else ""))
                else:
                    hidden = True
            return with_notice("\n".join(visible), hidden)
        sha = self.backend.commit_ref(revision)
        changes = self.backend.commit_changes(sha)
        hidden = any(not c.allowed(self.policy) for c in changes)
        parents = (
            self.backend.run("rev-list", "--parents", "-n", "1", sha)
            .decode()
            .split()[1:]
        )
        # Git knows the canonical empty tree without writing it to the object store.
        base = (
            parents[0]
            if parents
            else self.backend.run("hash-object", "-t", "tree", "--stdin")
            .decode()
            .strip()
        )
        return self._metadata(sha, hidden) + self._patch(changes, [base, sha], 3)

    def log(
        self,
        max_count: int = 10,
        start_timestamp: str | None = None,
        end_timestamp: str | None = None,
    ) -> str:
        bounded(max_count, 10000)
        args = []
        for flag, value in [("since", start_timestamp), ("until", end_timestamp)]:
            if value is not None:
                if not value or value.startswith("-") or "\x00" in value:
                    raise PolicyError("Invalid timestamp argument.")
                args.append(f"--{flag}={value}")
        if self.backend.head() is None or max_count == 0:
            return ""
        commits = (
            self.backend.run("rev-list", *args, "HEAD", "--").decode().splitlines()
        )
        output = []
        hidden = False
        for sha in commits:
            changes = self.backend.commit_changes(sha)
            denied = any(not c.allowed(self.policy) for c in changes)
            hidden |= denied
            if changes and not any(c.allowed(self.policy) for c in changes):
                continue
            output.append(self._metadata(sha, denied))
            if len(output) >= max_count:
                break
        return with_notice("\n".join(output), hidden)

    def branch(
        self,
        branch_type: str,
        contains: str | None = None,
        not_contains: str | None = None,
    ) -> str:
        if branch_type not in ("local", "remote", "all"):
            raise PolicyError("Invalid branch type.")
        args = []
        if contains is not None:
            args.append("--contains=" + self.backend.commit_ref(contains))
        if not_contains is not None:
            args.append("--no-contains=" + self.backend.commit_ref(not_contains))
        roots = (
            ["refs/heads"]
            if branch_type == "local"
            else ["refs/remotes"]
            if branch_type == "remote"
            else ["refs/heads", "refs/remotes"]
        )
        return self.backend.run(
            "for-each-ref", "--format=%(refname:short)", *args, *roots
        ).decode("utf-8", errors="replace")
