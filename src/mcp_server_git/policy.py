"""Immutable operator-supplied path policy; malformed configuration never permits access."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DENIED = "Access denied according to project policy."
PARTIAL = "Some content is unavailable according to project policy."


class PolicyError(Exception):
    """Safe to report without revealing a protected path or command output."""


def normalize_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
    ):
        raise PolicyError(DENIED)
    parts = path.split("/")
    if ".." in parts or any(p.casefold() == ".git" for p in parts):
        raise PolicyError(DENIED)
    result = "/".join(p for p in parts if p not in ("", "."))
    if not result:
        raise PolicyError(DENIED)
    return result


@dataclass(frozen=True)
class PathPolicy:
    patterns: tuple[re.Pattern[str], ...]

    @classmethod
    def from_json(cls, value: str) -> "PathPolicy":
        try:
            data = json.loads(value)
            if (
                not isinstance(data, dict)
                or set(data) != {"deny"}
                or not isinstance(data["deny"], list)
            ):
                raise ValueError
            if any(not isinstance(p, str) for p in data["deny"]):
                raise ValueError
            return cls(tuple(re.compile(p) for p in data["deny"]))
        except (ValueError, TypeError, re.error):
            raise PolicyError("Invalid project policy configuration.") from None

    def allows(self, path: str) -> bool:
        try:
            normalized = normalize_path(path)
        except PolicyError:
            return False
        parts = normalized.split("/")
        return not any(
            pattern.search("/".join(parts[:n]))
            for n in range(1, len(parts) + 1)
            for pattern in self.patterns
        )

    def require(self, path: str) -> None:
        if not self.allows(path):
            raise PolicyError(DENIED)


def load_policy(
    config_path: Path | None, server_name: str = "secure-git"
) -> PathPolicy:
    try:
        if config_path is not None:
            data = json.loads(config_path.read_text())
            value = data["mcpServers"][server_name]["env"]["SECURE_GIT_POLICY"]
        else:
            value = os.environ["SECURE_GIT_POLICY"]
        return PathPolicy.from_json(value)
    except (OSError, ValueError, KeyError, TypeError):
        raise PolicyError("Missing or invalid project policy configuration.") from None
