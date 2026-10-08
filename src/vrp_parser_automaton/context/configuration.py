"""Indentation-delimited context stacks; unknown child blocks use explicit fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vrp_parser_automaton.results import (
    BlankLine,
    ContextIssue,
    ErrorLine,
    LineResult,
    ParsedCommand,
    SeparatorLine,
)

if TYPE_CHECKING:
    from vrp_parser_automaton.api import CommandLineParser


@dataclass(frozen=True)
class ContextFrame:
    parent_indent: int
    view: str | None
    issue: ContextIssue | None = None


class ContextSession:
    def __init__(self, parser: CommandLineParser) -> None:
        self._parser = parser

    def parse(self, lines: tuple[str, ...]) -> tuple[LineResult, ...]:
        frames = [ContextFrame(-1, self._parser.entry_view)]
        previous: ContextFrame | None = None
        results: list[LineResult] = []
        for number, raw in enumerate(lines, 1):
            indent = raw[: len(raw) - len(raw.lstrip())]
            depth = len(indent)
            if not raw.strip():
                results.append(BlankLine(number, raw, indent))
                continue
            while len(frames) > 1 and depth <= frames[-1].parent_indent:
                frames.pop()
            if raw.strip() == "#":
                results.append(SeparatorLine(number, raw, indent))
                previous = None
                continue
            if previous is not None and depth > previous.parent_indent:
                frames.append(previous)
            frame = frames[-1]
            if frame.view is not None:
                result = self._parser.parse(raw, number, view=frame.view)
            else:
                assert frame.issue is not None
                result = self._parser.parse_unresolved(raw, number, frame.issue)
            assert isinstance(result, (ParsedCommand, ErrorLine))
            results.append(result)
            previous = self._child_frame(depth, result)
        return tuple(results)

    def _child_frame(
        self, depth: int, result: ParsedCommand | ErrorLine
    ) -> ContextFrame:
        if result.context_issue is not None:
            return ContextFrame(depth, None, result.context_issue)
        if isinstance(result, ErrorLine):
            return ContextFrame(
                depth,
                None,
                ContextIssue(
                    "parent_parse_error",
                    "The parent command could not be parsed; "
                    "its child view is unknown.",
                    result.line_number,
                ),
            )
        target, issue = self._parser.child_context(result)
        return ContextFrame(depth, target, issue)
