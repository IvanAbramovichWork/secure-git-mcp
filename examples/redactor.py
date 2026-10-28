"""An opt-in trusted filter example; directory policy itself does not scan secrets."""

import re
from mcp_server_git.filters import FilterContext


class TokenRedactor:
    def filter(self, text: str, context: FilterContext) -> str:
        return re.sub(r"EXAMPLE_TOKEN_[A-Za-z0-9]+", "[REDACTED]", text)


# Integrators pass filters=[TokenRedactor()] to secure_server.create_server(...).
# No plugin code is loaded from .mcp.json or tool arguments.
