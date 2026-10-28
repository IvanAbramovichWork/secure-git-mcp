"""Bind all policy operations to exactly one working tree and shared execution lock."""
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
            raise PolicyError('Invalid configured repository.') from None
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
