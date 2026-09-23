"""Construction-time errors for the public parser."""

from __future__ import annotations

from dataclasses import dataclass

from .patterns import SourceSpan


class PatternDocumentError(ValueError):
    """The JSON-compatible pattern document has an invalid shape."""


@dataclass(frozen=True, slots=True)
class PatternIssue:
    """One source pattern that could not be compiled."""

    pattern_index: int
    pattern: str
    message: str
    span: SourceSpan


class PatternCompilationError(ValueError):
    """All pattern errors collected during one compilation pass."""

    def __init__(self, issues: tuple[PatternIssue, ...]) -> None:
        if not issues:
            raise ValueError("PatternCompilationError requires at least one issue")
        self.issues = issues
        first = issues[0]
        suffix = "" if len(issues) == 1 else f" (+{len(issues) - 1} more)"
        super().__init__(f"pattern #{first.pattern_index}: {first.message}{suffix}")
