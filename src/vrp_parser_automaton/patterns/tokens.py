"""Token value objects for the Huawei pattern language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open character range in a source pattern."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("a span must satisfy 0 <= start <= end")

    @classmethod
    def covering(cls, first: SourceSpan, last: SourceSpan) -> SourceSpan:
        """Return the smallest span covering ``first`` through ``last``."""

        return cls(first.start, last.end)


class TokenKind(StrEnum):
    """Kinds understood by the recursive-descent parser."""

    PARAMETER = "parameter"
    LITERAL = "literal"
    LEFT_BRACE = "left_brace"
    RIGHT_BRACE = "right_brace"
    LEFT_BRACKET = "left_bracket"
    RIGHT_BRACKET = "right_bracket"
    PIPE = "pipe"
    STAR = "star"
    REPEAT = "repeat"
    END = "end"


@dataclass(frozen=True, slots=True)
class Token:
    """One lexeme and optional metadata supplied by a parameter recognizer."""

    kind: TokenKind
    text: str
    span: SourceSpan
    parameter: object | None = None
    repeat_bounds: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.kind is TokenKind.PARAMETER and self.parameter is None:
            raise ValueError("a parameter token requires parameter metadata")
        if self.kind is TokenKind.REPEAT and self.repeat_bounds is None:
            raise ValueError("a repeat token requires repeat bounds")
        if self.kind is not TokenKind.PARAMETER and self.parameter is not None:
            raise ValueError("only a parameter token can carry parameter metadata")
        if self.kind is not TokenKind.REPEAT and self.repeat_bounds is not None:
            raise ValueError("only a repeat token can carry repeat bounds")
