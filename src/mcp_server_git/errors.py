"""Only server-owned codes/messages cross the MCP error boundary."""

from .policy import DENIED, PolicyError

MESSAGES = {
    "POLICY_DENIED": DENIED,
    "REPOSITORY_MISMATCH": "Repository path does not match the configured repository. Omit repo_path to use the pinned repository; access is restricted by project policy.",
    "INVALID_ARGUMENTS": "Invalid tool arguments. Details withheld according to project policy.",
    "GIT_OWNERSHIP": "Git rejected repository ownership. Run the container with the repository owner UID/GID; ownership checks remain enabled under project policy.",
    "GIT_TIMEOUT": "Git operation timed out. Details withheld according to project policy.",
    "GIT_UNAVAILABLE": "Git could not be executed. Details withheld according to project policy.",
    "GIT_FAILED": "Git operation failed. Check revision and repository state; details withheld according to project policy.",
    "OPERATION_REFUSED": "Operation refused. Check parameters and repository state; restrictions apply according to project policy.",
    "INTERNAL_ERROR": "Operation failed unexpectedly. Details withheld according to project policy.",
}


class SafeGitError(PolicyError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


def response_message(code: str) -> str:
    return f"{code}: {MESSAGES[code]}"
