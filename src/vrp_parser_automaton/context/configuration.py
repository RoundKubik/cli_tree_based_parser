"""Indentation-delimited context stacks; unknown child blocks use explicit fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vrp_parser_automaton.results import (
    BlankLine,
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


class ContextSession:
    def __init__(self, parser: CommandLineParser) -> None:
        self._parser = parser

    def parse(self, lines: tuple[str, ...]) -> tuple[LineResult, ...]:
        frames = [ContextFrame(-1, self._parser.entry_view)]
        previous: tuple[int, str | None] | None = None
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
            if previous is not None and depth > previous[0]:
                parent_depth, view = previous
                frames.append(ContextFrame(parent_depth, view))
            frame = frames[-1]
            result = (
                self._parser.parse(raw, number, view=frame.view)
                if frame.view is not None
                else self._parser.parse_flat(raw, number)
            )
            target = (
                self._parser.child_view(result)
                if isinstance(result, ParsedCommand)
                else None
            )
            results.append(result)
            previous = depth, target
        return tuple(results)
