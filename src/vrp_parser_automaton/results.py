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

from .serialization import JsonValueConverter


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
    slot_id: str = ""
    iterations: tuple[tuple[str, int], ...] = ()


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
class ContextIssue:
    """Why a command or child block has no confirmed view."""

    code: Literal[
        "parent_parse_error",
        "unresolved_transition",
        "ambiguous_transition",
        "outside_view",
    ]
    message: str
    source_line: int


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """A successful line result, including unresolved ambiguity."""

    line_number: int
    raw: str
    indent: str
    status: MatchStatus
    primary_match: PatternMatch
    alternative_matches: tuple[PatternMatch, ...] = ()
    view: str | None = None
    context_issue: ContextIssue | None = None
    kind: Literal["command", "unresolved_command"] = field(
        default="command", init=False
    )

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
class UnresolvedCommand(ParsedCommand):
    """Complete syntax matches whose configuration context is not confirmed.

    This remains a ParsedCommand for callers consuming captures. Its distinct
    kind and context_issue prevent it from appearing as a confirmed view match.
    """

    kind: Literal["unresolved_command"] = field(
        default="unresolved_command", init=False
    )

    @classmethod
    def from_command(
        cls, command: ParsedCommand, issue: ContextIssue
    ) -> UnresolvedCommand:
        return cls(
            line_number=command.line_number,
            raw=command.raw,
            indent=command.indent,
            status=command.status,
            primary_match=command.primary_match,
            alternative_matches=command.alternative_matches,
            context_issue=issue,
        )


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
class CatalogMatch:
    """A complete, valid match found outside the selected view for diagnostics."""

    view: str
    pattern_id: str
    format: str


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
    catalog_matches: tuple[CatalogMatch, ...] | None = None


@dataclass(frozen=True, slots=True)
class ErrorLine:
    """A physical line that could not be parsed."""

    line_number: int
    raw: str
    indent: str
    error: ParseError
    view: str | None = None
    context_issue: ContextIssue | None = None
    kind: Literal["error"] = field(default="error", init=False)

    @property
    def parsed(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True)
class SeparatorLine:
    line_number: int
    raw: str
    indent: str
    kind: Literal["separator"] = field(default="separator", init=False)


type LineResult = (
    BlankLine | ParsedCommand | UnresolvedCommand | ErrorLine | SeparatorLine
)


@dataclass(frozen=True, slots=True)
class ParseSummary:
    """Counts derived from a complete configuration parse."""

    total: int
    blank: int
    commands: int
    ambiguous: int
    errors: int
    unresolved: int = 0


@dataclass(frozen=True, slots=True)
class ParseReport:
    """Result of parsing an entire configuration file."""

    lines: tuple[LineResult, ...]
    summary: ParseSummary

    @property
    def has_errors(self) -> bool:
        return self.summary.errors > 0

    @property
    def has_unresolved(self) -> bool:
        return self.summary.unresolved > 0

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-compatible tree of ordinary Python values."""

        converted = JsonValueConverter().convert(self)
        if not isinstance(converted, Mapping):
            raise RuntimeError("a parse report must serialize to an object")
        # Unknown/flat context adds no field to the existing line representation.
        for line in converted["lines"]:
            if line.get("view") is None:
                line.pop("view", None)
            if line.get("context_issue") is None:
                line.pop("context_issue", None)
            if line["kind"] == "error" and line["error"]["catalog_matches"] is None:
                line["error"].pop("catalog_matches")
        if not self.summary.unresolved:
            converted["summary"].pop("unresolved")
        return converted


class ParseReportFactory:
    """Build a report and keep summary counting outside parser orchestration."""

    def create(self, lines: tuple[LineResult, ...]) -> ParseReport:
        blank = sum(isinstance(line, BlankLine) for line in lines)
        errors = sum(isinstance(line, ErrorLine) for line in lines)
        commands = sum(isinstance(line, ParsedCommand) for line in lines)
        unresolved = sum(isinstance(line, UnresolvedCommand) for line in lines)
        ambiguous = sum(
            isinstance(line, ParsedCommand) and line.status is MatchStatus.AMBIGUOUS
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
                unresolved=unresolved,
            ),
        )
