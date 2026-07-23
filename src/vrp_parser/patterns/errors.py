"""Errors raised by the standalone pattern-language frontend."""

from __future__ import annotations

from .tokens import SourceSpan


class PatternLanguageError(ValueError):
    """A syntax error with its original source and exact character span."""

    def __init__(self, message: str, source: str, span: SourceSpan) -> None:
        self.message = message
        self.source = source
        self.span = span
        super().__init__(f"{message} at characters {span.start}:{span.end}")

