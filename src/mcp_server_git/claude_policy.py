"""Import project denyRead as anchored logical path globs, including historical paths."""

import fnmatch
import json
import os
import re
from pathlib import Path

from .policy import PolicyError

ERROR = "Invalid or unreadable Claude project policy."


def _glob(pattern: str) -> str:
    result = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            end = i + 1
            while end < len(pattern) and pattern[end] == "*":
                end += 1
            if end - i > 1:
                if (
                    (i == 0 or pattern[i - 1] == "/")
                    and end < len(pattern)
                    and pattern[end] == "/"
                ):
                    result.append("(?:.*/)?")
                    end += 1
                else:
                    result.append(".*")
            else:
                result.append("[^/]*")
            i = end
            continue
        if char == "?":
            result.append("[^/]")
        elif char == "[":
            end = i + 1
            if end < len(pattern) and pattern[end] == "!":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            end = pattern.find("]", end)
            if end != -1:
                token = pattern[i : end + 1]
                body = token[1:-1]
                if body.startswith("!"):
                    body = body[1:]
                backwards = any(
                    body[n] == "-" and body[n - 1] > body[n + 1]
                    for n in range(1, len(body) - 1)
                )
                result.append(
                    re.escape(token)
                    if not body or backwards
                    else fnmatch.translate(token)[4:-3]
                )
                i = end + 1
                continue
            result.append(r"\[")
        else:
            result.append(re.escape(char))
        i += 1
    return r"\A" + "".join(result) + r"\Z"


def _patterns(value: str, root: Path) -> list[re.Pattern[str]]:
    if not value or "\x00" in value or "\\" in value:
        raise PolicyError(ERROR)
    expanded = os.path.expanduser(value)
    if expanded.startswith("~"):
        raise PolicyError(ERROR)
    segments = expanded.split("/")
    seen_glob = False
    for segment in segments:
        if segment == ".." and seen_glob:
            raise PolicyError(ERROR)
        seen_glob |= any(c in segment for c in "*?[")
    if seen_glob and expanded.endswith("/"):
        raise PolicyError(ERROR)
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    # Normalize literal leading '..' without expanding glob matches on disk.
    absolute = Path(os.path.abspath(path)).as_posix()
    parts = path.parts
    root_parts = (
        len(root.parts)
        if path.as_posix().startswith(root.as_posix().rstrip("/") + "/")
        else 0
    )
    pivot = next(
        (
            n
            for n, part in enumerate(parts)
            if n >= root_parts and any(c in part for c in "*?[")
        ),
        len(parts),
    )
    prefix = Path(*parts[:pivot])
    suffix = "/".join(parts[pivot:])
    fixed_prefixes = (
        Path(os.path.abspath(prefix)).as_posix(),
        prefix.resolve().as_posix(),
    )
    result = [re.compile(r"\A" + re.escape(absolute) + r"\Z")]
    suffixes = [suffix]
    if suffix == "**":
        suffixes.append("")
    elif suffix.endswith("/**"):
        suffixes.append(suffix[:-3])
    for fixed in dict.fromkeys(fixed_prefixes):
        for tail in suffixes:
            # Physical root and resolved symlink targets are literal filesystem
            # prefixes. Only text supplied as glob components is interpreted.
            lead = fixed.rstrip("/") + "/" if tail else fixed
            expression = r"\A" + re.escape(lead) + (_glob(tail)[2:] if tail else r"\Z")
            result.append(re.compile(expression, re.DOTALL))
            candidate = lead + tail
            result.append(re.compile(r"\A" + re.escape(candidate) + r"\Z"))
    return result


def load_claude_patterns(root: Path) -> tuple[re.Pattern[str], ...]:
    path = root / ".claude/settings.json"
    try:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if path.is_symlink() or path.parent.is_symlink():
                raise PolicyError(ERROR)
            return ()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError
        sandbox = data.get("sandbox", {})
        if not isinstance(sandbox, dict):
            raise ValueError
        filesystem = sandbox.get("filesystem", {})
        if not isinstance(filesystem, dict):
            raise ValueError
        values = filesystem.get("denyRead", [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError
        return tuple(p for value in values for p in _patterns(value, root))
    except (OSError, ValueError, TypeError, RuntimeError):
        raise PolicyError(ERROR) from None
