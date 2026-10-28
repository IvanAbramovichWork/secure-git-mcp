"""Search validated file sets with structured grep output and bounded responses."""

import json

from .paths import exact_pathspecs, require_working_file, selected_files
from .policy import PARTIAL, PolicyError
from .repository import SecureRepository


def grep(
    backend: SecureRepository,
    pattern: str,
    paths: list[str] | None = None,
    revision: str | None = None,
    regex: bool = False,
    max_count: int = 1000,
) -> str:
    if not isinstance(pattern, str) or "\0" in pattern or len(pattern) > 4096:
        raise PolicyError("Invalid search pattern.")
    if type(max_count) is not int or not 1 <= max_count <= 10000:
        raise PolicyError("Invalid search count.")
    sha = backend.commit_ref(revision) if revision is not None else None
    files, hidden = selected_files(backend, paths, sha)
    eligible = []
    for entry in files:
        if sha is None:
            try:
                require_working_file(backend, entry.path)
            except PolicyError:
                hidden = True
                continue
        eligible.append(entry.path)
    chunks: list[list[str]] = []
    for path in eligible:
        if (
            not chunks
            or sum(
                len(p.encode("utf-8", errors="surrogateescape")) * 2 + 64
                for p in chunks[-1]
            )
            > 24000
        ):
            chunks.append([])
        chunks[-1].append(path)
    output = []
    truncated = False
    for chunk in chunks:
        args = [
            "grep",
            "-n",
            "-z",
            "-H",
            "-I",
            "--full-name",
            "--no-column",
            "--no-heading",
            "--no-break",
            "--no-color",
            "--no-textconv",
            "--no-recurse-submodules",
            "--threads=1",
            "-m",
            str(max_count + 1),
            "-E" if regex else "-F",
            "-e",
            pattern,
        ]
        if sha is not None:
            args.append(sha)
        raw = backend.run(
            *args, "--", *exact_pathspecs(chunk), literal_paths=False, ok_codes=(0, 1)
        )
        offset = 0
        while offset < len(raw):
            first = raw.find(b"\0", offset)
            second = raw.find(b"\0", first + 1)
            end = raw.find(b"\n", second + 1)
            if min(first, second, end) < 0:
                raise PolicyError("Search output unavailable.")
            name = raw[offset:first].decode("utf-8", errors="surrogateescape")
            if sha is not None:
                if not name.startswith(sha + ":"):
                    raise PolicyError("Search output unavailable.")
                name = name[len(sha) + 1 :]
            number = raw[first + 1 : second].decode("ascii")
            if name not in chunk or not number.isdecimal():
                raise PolicyError("Search output unavailable.")
            content = raw[second + 1 : end].decode("utf-8", errors="replace")
            if len(output) == max_count:
                truncated = True
                break
            output.append(f"{json.dumps(name, ensure_ascii=True)}:{number}:{content}")
            offset = end + 1
        if truncated:
            break
    if truncated:
        output.append("Results truncated at the requested limit.")
    if hidden:
        output.append(PARTIAL)
    return "\n".join(output)
