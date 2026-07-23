"""Factories and English messages for failed command matching."""

from __future__ import annotations

from vrp_parser.results import ErrorCode, ExpectedElement, ParseError

from .diagnostics import MatchDiagnostics
from .suggestions import CommandSuggester


class ExpectedElementFormatter:
    """Turn structured expectations into a short readable phrase."""

    def format(
        self,
        expected: tuple[ExpectedElement, ...],
        maximum: int = 5,
    ) -> str:
        descriptions = tuple(item.description for item in expected)
        visible = descriptions[:maximum]
        if not visible:
            return "a valid continuation"
        if len(descriptions) > maximum:
            hidden = len(descriptions) - maximum
            noun = "option" if hidden == 1 else "options"
            return f"{self._join(visible)}, and {hidden} more {noun}"
        return self._join(visible)

    @staticmethod
    def _join(items: tuple[str, ...]) -> str:
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} or {items[1]}"
        return f"{', '.join(items[:-1])}, or {items[-1]}"


class CommandErrorFactory:
    """Create structured syntax errors and their user-facing messages."""

    def __init__(
        self,
        suggester: CommandSuggester,
        expected_formatter: ExpectedElementFormatter | None = None,
    ) -> None:
        self._suggester = suggester
        self._expected_formatter = expected_formatter or ExpectedElementFormatter()

    def create(
        self,
        command: str,
        diagnostics: MatchDiagnostics,
        *,
        span_offset: int,
    ) -> ParseError:
        code = (
            ErrorCode.UNKNOWN_COMMAND
            if diagnostics.position == 0
            else ErrorCode.SYNTAX_ERROR
        )
        position = diagnostics.position + span_offset
        expected = diagnostics.elements(offset=span_offset)
        suggestions_allowed = diagnostics.allows_keyword_suggestions
        suggestions = self._suggester.suggest(command) if suggestions_allowed else ()
        return ParseError(
            code=code,
            message=self._message(
                command,
                code,
                position,
                expected,
                suggestions,
                suggestions_allowed,
            ),
            position=position,
            expected=expected,
            suggestions=suggestions,
        )

    def _message(
        self,
        command: str,
        code: ErrorCode,
        position: int,
        expected: tuple[ExpectedElement, ...],
        suggestions: tuple[str, ...],
        suggestions_allowed: bool,
    ) -> str:
        if code is ErrorCode.UNKNOWN_COMMAND:
            reason = "No complete command pattern accepted the first token."
        else:
            column = position + 1
            wanted = self._expected_formatter.format(expected)
            reason = f"Parsing stopped at column {column}; expected {wanted}."
        if suggestions:
            return (
                f"Command {command!r} was not recognized. Did you mean:\n"
                f"{self._numbered(suggestions)}\n"
                f"Reason: {reason}"
            )
        message = f"Command {command!r} was not recognized. {reason}"
        no_suggestion = self._no_suggestion_message(
            code,
            suggestions_allowed,
        )
        if no_suggestion:
            return f"{message} {no_suggestion}"
        return message

    @staticmethod
    def _numbered(suggestions: tuple[str, ...]) -> str:
        return "\n".join(
            f"  {index}. {pattern}"
            for index, pattern in enumerate(suggestions, start=1)
        )

    @staticmethod
    def _no_suggestion_message(
        code: ErrorCode,
        suggestions_allowed: bool,
    ) -> str:
        if not suggestions_allowed:
            return (
                "Keyword suggestions were not generated because an "
                "applicable parameter-led pattern matched farther than any "
                "literal-led pattern."
            )
        if code is ErrorCode.UNKNOWN_COMMAND:
            return "No similar literal command patterns were found."
        return "No sufficiently similar literal command patterns were found."
