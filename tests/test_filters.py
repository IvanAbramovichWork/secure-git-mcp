"""Output filters compose deterministically and never fall back to raw text."""

import pytest
from mcp_server_git.filters import FilterContext, FilterFailure, FilterPipeline


class Replace:
    def __init__(self, old, new):
        self.old, self.new = old, new

    def filter(self, text, context):
        return text.replace(self.old, self.new)


def test_ordered_filters_run_for_success_and_error():
    pipeline = FilterPipeline(
        [Replace("token", "first"), Replace("first", "[REDACTED]")]
    )
    for error in [False, True]:
        assert pipeline.apply("token", FilterContext("git_show", error)) == "[REDACTED]"


def test_filter_failure_never_returns_original():
    class Broken:
        def filter(self, text, context):
            raise RuntimeError("SECRET_ERROR")

    with pytest.raises(FilterFailure) as exc:
        FilterPipeline([Broken()]).apply(
            "SECRET_INPUT", FilterContext("git_show", False)
        )
    assert "SECRET" not in str(exc.value)
