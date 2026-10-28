"""Ordered, trusted server-side filters run only after structural access checks."""

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class FilterContext:
    tool_name: str
    is_error: bool


class OutputFilter(Protocol):
    def filter(self, text: str, context: FilterContext) -> str: ...


class FilterFailure(Exception):
    pass


class FilterPipeline:
    def __init__(self, filters: Sequence[OutputFilter] = ()):
        self.filters = tuple(filters)

    def apply(self, text: str, context: FilterContext) -> str:
        try:
            for output_filter in self.filters:
                text = output_filter.filter(text, context)
                if not isinstance(text, str):
                    raise TypeError
            return text
        except Exception:
            raise FilterFailure(
                "Output filtering failed; response withheld according to project policy."
            ) from None
