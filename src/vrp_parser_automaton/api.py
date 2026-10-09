"""Small public facades for line and configuration parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from vrp_parser_automaton.automata.compiler import PatternCompiler
from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.catalogs.source import PatternCatalog
from vrp_parser_automaton.context.configuration import ContextSession
from vrp_parser_automaton.context.views import ViewMatchers
from vrp_parser_automaton.runtime.resolution import ResolvedMatch

from .errors import PatternDocumentError
from .parameters import (
    ParameterTypeRegistry,
    default_parameter_registry,
)
from .results import (
    BlankLine,
    ContextIssue,
    ErrorLine,
    LineResult,
    MatchStatus,
    ParsedCommand,
    ParseError,
    ParseReport,
    ParseReportFactory,
    SeparatorLine,
    UnresolvedCommand,
)


class CommandLineParser:
    """Compile command patterns once and parse one physical CLI line."""

    def __init__(
        self,
        pattern_document: Mapping[str, Any],
        *,
        parameter_types: ParameterTypeRegistry | None = None,
        context_mode: Literal["system", "hierarchy"] = "system",
    ) -> None:
        if context_mode not in {"system", "hierarchy"}:
            raise ValueError("context_mode must be 'system' or 'hierarchy'")
        self._context_mode = context_mode
        catalog = PatternCatalog.read(
            pattern_document, validate_transitions=context_mode == "hierarchy"
        )
        commands = tuple(command.format for command in catalog.commands)
        source_registry = parameter_types or default_parameter_registry()
        self._parameter_types = source_registry.clone().freeze()
        self._graph = PatternCompiler(self._parameter_types).compile(
            commands,
            annotations=tuple(command.parameter_types for command in catalog.commands),
        )
        self._entry_view = catalog.entry_view
        self._global_view = catalog.global_view
        self._views = catalog.views
        self._pattern_views = tuple(command.view for command in catalog.commands)
        self._child_views = tuple(command.child_view for command in catalog.commands)
        self._matchers = ViewMatchers(self._graph, catalog, self._parameter_types)

    @property
    def entry_view(self) -> str | None:
        return self._entry_view

    @property
    def global_view(self) -> str | None:
        return self._global_view

    @property
    def context_mode(self) -> Literal["system", "hierarchy"]:
        return self._context_mode

    @property
    def views(self) -> tuple[str, ...]:
        return self._views

    @property
    def command_count(self) -> int:
        return len(self._graph.patterns)

    @property
    def command_graph(self) -> CommandAutomaton:
        return self._graph

    @property
    def automaton(self) -> CommandAutomaton:
        """The compiled instructions; command_graph is a compatibility alias."""
        return self._graph

    @property
    def parameter_types(self) -> ParameterTypeRegistry:
        return self._parameter_types

    def parse(
        self, line: str, line_number: int = 1, *, view: str | None = None
    ) -> LineResult:
        """Grouped inputs default to system/non-system selection by indentation."""
        if (
            view is None
            and self.entry_view is not None
            and self.context_mode == "system"
        ):
            self._validate_input(line, line_number)
            nested = bool(self._indent_end(line))
            return self._parse_at(
                line,
                line_number,
                None if nested else self.entry_view,
                partition=nested,
            )
        return self._parse_at(
            line, line_number, self.entry_view if view is None else view
        )

    def parse_flat(self, line: str, line_number: int = 1) -> LineResult:
        """Explicitly search all source formats, including all grouped views."""
        return self._parse_at(line, line_number, None)

    def parse_unresolved(
        self, line: str, line_number: int, issue: ContextIssue
    ) -> LineResult:
        """Keep the best valid syntax matches when a grouped context is unknown."""
        result = self._parse_at(line, line_number, None, valid_only=True)
        if isinstance(result, ParsedCommand):
            return UnresolvedCommand.from_command(result, issue)
        if isinstance(result, ErrorLine):
            return replace(result, context_issue=issue)
        return result

    def child_view(self, command: ParsedCommand) -> str | None:
        """Use the input hierarchy; conflicting parses leave the context unknown."""
        return self.child_context(command)[0]

    def child_context(
        self, command: ParsedCommand
    ) -> tuple[str | None, ContextIssue | None]:
        """Keep the cause of an unknown child view alongside the transition."""
        if self.context_mode == "system":
            return None, None
        if isinstance(command, UnresolvedCommand) or command.view is None:
            return None, command.context_issue
        targets = {
            command.view
            if self._global_view is not None
            and self._pattern_views[match.pattern_index] == self._global_view
            and self._child_views[match.pattern_index] == self._global_view
            else self._child_views[match.pattern_index]
            for match in command.matches
        }
        if None in targets:
            return None, ContextIssue(
                "unresolved_transition",
                "The parent command has an unresolved view transition.",
                command.line_number,
            )
        if len(targets) > 1:
            return None, ContextIssue(
                "ambiguous_transition",
                "The parent command matches formats with different view transitions.",
                command.line_number,
            )
        return targets.pop(), None

    def _parse_at(
        self,
        line: str,
        line_number: int,
        view: str | None,
        *,
        valid_only: bool = False,
        partition: bool | None = None,
    ) -> LineResult:
        self._validate_input(line, line_number)
        matcher = (
            self._matchers.for_view(view)
            if partition is None
            else self._matchers.for_partition(partition)
        )
        indent_end = self._indent_end(line)
        indent = line[:indent_end]
        command = line[indent_end:].rstrip()
        if not command:
            return BlankLine(line_number, line, indent)
        if partition is not None and command == "#":
            return SeparatorLine(line_number, line, indent)

        outcome = matcher.match(
            command,
            span_offset=indent_end,
            valid_only=valid_only,
            retain_all=partition is not None,
        )
        if isinstance(outcome, ParseError):
            if partition is not None:
                scope = "non-system views" if partition else f"system view {view!r}"
                outcome = replace(
                    outcome,
                    message=f"{outcome.message} Search was restricted to {scope}.",
                )
            elif view is not None and self.context_mode == "hierarchy":
                fallback = self._matchers.outside_view(view).match(
                    command, span_offset=indent_end, valid_only=True
                )
                if isinstance(fallback, ResolvedMatch):
                    return UnresolvedCommand.from_command(
                        self._parsed(line_number, line, indent, fallback),
                        ContextIssue(
                            "outside_view",
                            f"Parsing failed in view {view!r}. Complete matches "
                            "were found in other views; the context is unconfirmed.",
                            line_number,
                        ),
                    )
                outcome = replace(
                    outcome,
                    message=f"{outcome.message} Parsing failed in view {view!r}. "
                    "No complete match with valid parameters was found in other views.",
                    catalog_matches=(),
                )
            return ErrorLine(line_number, line, indent, outcome, view)
        return self._parsed(line_number, line, indent, outcome, view)

    def _parsed(
        self,
        line_number: int,
        raw: str,
        indent: str,
        outcome: ResolvedMatch,
        view: str | None = None,
    ) -> ParsedCommand:
        matches = (outcome.primary_match, *outcome.alternative_matches)
        if self._views:
            matches = tuple(
                replace(match, source_view=self._pattern_views[match.pattern_index])
                for match in matches
            )
        views = {match.source_view for match in matches}
        return ParsedCommand(
            line_number=line_number,
            raw=raw,
            indent=indent,
            status=MatchStatus.AMBIGUOUS if len(views) > 1 else outcome.status,
            primary_match=matches[0],
            alternative_matches=matches[1:],
            view=view,
        )

    @staticmethod
    def _validate_input(line: str, line_number: int) -> None:
        if not isinstance(line, str):
            raise TypeError("configuration line must be a string")
        if "\r" in line or "\n" in line:
            raise ValueError(
                "CommandLineParser accepts one line without a line terminator"
            )
        if isinstance(line_number, bool) or not isinstance(line_number, int):
            raise TypeError("line_number must be an integer")
        if line_number < 1:
            raise ValueError("line_number must be at least 1")

    @staticmethod
    def _indent_end(line: str) -> int:
        position = 0
        while position < len(line) and line[position].isspace():
            position += 1
        return position

    @classmethod
    def from_json(
        cls,
        source: str,
        *,
        parameter_types: ParameterTypeRegistry | None = None,
        context_mode: Literal["system", "hierarchy"] = "system",
    ) -> CommandLineParser:
        try:
            document = json.loads(source)
        except json.JSONDecodeError as error:
            raise PatternDocumentError(f"invalid pattern JSON: {error}") from error
        if not isinstance(document, Mapping):
            raise PatternDocumentError("pattern JSON root must be an object")
        return cls(document, parameter_types=parameter_types, context_mode=context_mode)

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        parameter_types: ParameterTypeRegistry | None = None,
        context_mode: Literal["system", "hierarchy"] = "system",
    ) -> CommandLineParser:
        return cls.from_json(
            Path(path).read_text(encoding="utf-8"),
            parameter_types=parameter_types,
            context_mode=context_mode,
        )


class ConfigurationParser:
    """Apply one reusable line parser to a complete configuration."""

    def __init__(
        self,
        line_parser: CommandLineParser,
        report_factory: ParseReportFactory | None = None,
        *,
        contextual: bool | None = None,
    ) -> None:
        self._line_parser = line_parser
        self._report_factory = report_factory or ParseReportFactory()
        self._contextual = (
            line_parser.entry_view is not None if contextual is None else contextual
        )
        if self._contextual and line_parser.entry_view is None:
            raise ValueError("contextual parsing requires a grouped catalog")

    @property
    def line_parser(self) -> CommandLineParser:
        return self._line_parser

    def parse(self, content: str) -> ParseReport:
        if not isinstance(content, str):
            raise TypeError("configuration content must be a string")
        physical = self._physical_lines(content)
        if self._contextual:
            if self._line_parser.context_mode == "system":
                return self._report_factory.create(
                    tuple(
                        self._line_parser.parse(raw, number)
                        for number, raw in enumerate(physical, 1)
                    )
                )
            return self._report_factory.create(
                ContextSession(self._line_parser).parse(physical)
            )
        lines = tuple(
            self._line_parser.parse_flat(raw, line_number)
            for line_number, raw in enumerate(
                physical,
                start=1,
            )
        )
        return self._report_factory.create(lines)

    @staticmethod
    def _physical_lines(content: str) -> tuple[str, ...]:
        if not content:
            return ()
        lines = re.split(r"\r\n|\r|\n", content)
        if content.endswith(("\r", "\n")):
            lines.pop()
        return tuple(lines)
