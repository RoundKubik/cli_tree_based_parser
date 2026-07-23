"""Immutable results returned by the public parsers.

The result model deliberately contains no graph or matcher objects.  A caller
can safely serialize it, store it, or inspect it without knowing how matching
is implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from vrp_parser.serialization import JsonValueConverter


class MatchStatus(StrEnum):
    """Resolution of a successfully parsed command."""

    UNIQUE = "unique"
    EQUIVALENT = "equivalent"
    AMBIGUOUS = "ambiguous"


class ErrorCode(StrEnum):
    """Stable machine-readable categories for line errors."""

    UNKNOWN_COMMAND = "unknown_command"
    SYNTAX_ERROR = "syntax_error"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True, slots=True)
class TextSpan:
    """A half-open character range inside the original input line."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("a text span must satisfy 0 <= start <= end")


@dataclass(frozen=True, slots=True)
class ParameterValue:
    """One parameter value captured from a command."""

    type_id: str
    declaration: str
    raw: str
    normalized: Any
    span: TextSpan


@dataclass(frozen=True, slots=True)
class VariationStep:
    """One choice made while resolving a source pattern."""

    kind: Literal["choice", "optional", "set", "repeat", "enum"]
    path: str
    selected: tuple[int | str, ...] = ()


@dataclass(frozen=True, slots=True)
class PatternMatch:
    """One source pattern and variation that accepted the command."""

    pattern_id: str
    pattern_index: int
    original_pattern: str
    variation: str
    variation_id: str
    parameters: tuple[ParameterValue, ...] = ()
    trace: tuple[VariationStep, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """A successful line result, including unresolved ambiguity."""

    line_number: int
    raw: str
    indent: str
    status: MatchStatus
    primary_match: PatternMatch
    alternative_matches: tuple[PatternMatch, ...] = ()
    kind: Literal["command"] = field(default="command", init=False)

    @property
    def parsed(self) -> Literal[True]:
        """Ambiguous commands are successful parsed results too."""

        return True

    @property
    def matches(self) -> tuple[PatternMatch, ...]:
        """All matches, with the JSON-order representative first."""

        return (self.primary_match, *self.alternative_matches)

    @property
    def parameters(self) -> tuple[ParameterValue, ...]:
        """Parameters captured by the representative match."""

        return self.primary_match.parameters


@dataclass(frozen=True, slots=True)
class BlankLine:
    """A blank or whitespace-only physical line."""

    line_number: int
    raw: str
    indent: str
    kind: Literal["blank"] = field(default="blank", init=False)


@dataclass(frozen=True, slots=True)
class ExpectedElement:
    """An element that could have continued a failed match."""

    description: str
    position: int


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """A parameter-shaped value rejected by its validator."""

    type_id: str
    declaration: str
    raw: str
    span: TextSpan
    message: str
    reason_code: str | None = None
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True, slots=True)
class ParseError:
    """Structured details for one erroneous configuration line."""

    code: ErrorCode
    message: str
    position: int | None = None
    expected: tuple[ExpectedElement, ...] = ()
    failures: tuple[ValidationFailure, ...] = ()
    candidate_patterns: tuple[str, ...] = ()
    candidate_variations: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ErrorLine:
    """A physical line that could not be parsed."""

    line_number: int
    raw: str
    indent: str
    error: ParseError
    kind: Literal["error"] = field(default="error", init=False)

    @property
    def parsed(self) -> Literal[False]:
        return False


type LineResult = BlankLine | ParsedCommand | ErrorLine


@dataclass(frozen=True, slots=True)
class ParseSummary:
    """Counts derived from a complete configuration parse."""

    total: int
    blank: int
    commands: int
    ambiguous: int
    errors: int


@dataclass(frozen=True, slots=True)
class ParseReport:
    """Result of parsing an entire configuration file."""

    lines: tuple[LineResult, ...]
    summary: ParseSummary

    @property
    def has_errors(self) -> bool:
        return self.summary.errors > 0

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-compatible tree of ordinary Python values."""

        converted = JsonValueConverter().convert(self)
        if not isinstance(converted, Mapping):
            raise RuntimeError("a parse report must serialize to an object")
        return converted


class ParseReportFactory:
    """Build a report and keep summary counting outside parser orchestration."""

    def create(self, lines: tuple[LineResult, ...]) -> ParseReport:
        blank = sum(isinstance(line, BlankLine) for line in lines)
        errors = sum(isinstance(line, ErrorLine) for line in lines)
        commands = len(lines) - blank - errors
        ambiguous = sum(
            isinstance(line, ParsedCommand)
            and line.status is MatchStatus.AMBIGUOUS
            for line in lines
        )
        return ParseReport(
            lines=lines,
            summary=ParseSummary(
                total=len(lines),
                blank=blank,
                commands=commands,
                ambiguous=ambiguous,
                errors=errors,
            ),
        )
