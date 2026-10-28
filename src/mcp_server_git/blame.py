"""Render porcelain blame only after checking each line's historical provenance."""

import ast
import json
import re

from .paths import selected_files
from .policy import DENIED, PARTIAL, PolicyError, normalize_path
from .repository import SecureRepository


def decode_git_path(value: bytes) -> str:
    if value.startswith(b'"'):
        try:
            decoded = ast.literal_eval("b" + value.decode("ascii"))
            if not isinstance(decoded, bytes):
                raise ValueError
            value = decoded
        except (ValueError, SyntaxError, UnicodeError):
            raise PolicyError("Historical filename unavailable.") from None
    return value.decode("utf-8", errors="surrogateescape")


def blame(
    backend: SecureRepository,
    path: str,
    revision: str = "HEAD",
    start_line: int = 1,
    end_line: int | None = None,
    max_count: int = 500,
) -> str:
    if (
        type(start_line) is not int
        or start_line < 1
        or type(max_count) is not int
        or not 1 <= max_count <= 10000
    ):
        raise PolicyError("Invalid blame range.")
    if end_line is not None and (type(end_line) is not int or end_line < start_line):
        raise PolicyError("Invalid blame range.")
    path = normalize_path(path)
    sha = backend.commit_ref(revision)
    files, _ = selected_files(backend, [path], sha)
    if not any(e.path == path for e in files):
        raise PolicyError(DENIED)
    args = [
        "-c",
        "blame.ignoreRevsFile=",
        "blame",
        "--line-porcelain",
        "--encoding=UTF-8",
    ]
    if start_line != 1 or end_line is not None:
        args.extend(["-L", f'{start_line},{end_line if end_line is not None else ""}'])
    raw = backend.run(*args, sha, "--", path)
    lines = iter(raw.split(b"\n"))
    output = []
    metadata_hidden = {}
    hidden = False
    truncated = False
    for header in lines:
        if not header:
            continue
        fields = header.split()
        if len(fields) not in (3, 4) or not re.fullmatch(
            rb"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]
        ):
            raise PolicyError("Blame output unavailable.")
        commit = fields[0].decode("ascii")
        number = int(fields[2])
        metadata: dict[bytes, bytes] = {}
        for record in lines:
            if record.startswith(b"\t"):
                content = record[1:].decode("utf-8", errors="replace")
                break
            key, _, value = record.partition(b" ")
            metadata[key] = value
        else:
            raise PolicyError("Blame output unavailable.")
        if b"filename" not in metadata:
            raise PolicyError("Blame output unavailable.")
        origin = decode_git_path(metadata[b"filename"])
        previous = metadata.get(b"previous")
        previous_path = (
            decode_git_path(previous.split(b" ", 1)[1])
            if previous and b" " in previous
            else None
        )
        if not backend.policy.allows(origin) or (
            previous_path is not None and not backend.policy.allows(previous_path)
        ):
            hidden = True
            continue
        if len(output) == max_count:
            truncated = True
            break
        if commit not in metadata_hidden:
            metadata_hidden[commit] = any(
                not c.allowed(backend.policy) for c in backend.commit_changes(commit)
            )
        row = {"line": number, "commit": commit, "text": content}
        if metadata_hidden[commit]:
            hidden = True
            row["metadata"] = "withheld according to project policy"
        else:
            row["author"] = metadata.get(b"author", b"").decode(
                "utf-8", errors="replace"
            )
            row["summary"] = metadata.get(b"summary", b"").decode(
                "utf-8", errors="replace"
            )
        output.append(json.dumps(row, ensure_ascii=True))
    if hidden:
        output.append(PARTIAL)
    if truncated:
        output.append("Results truncated at the requested limit.")
    return "\n".join(output)
