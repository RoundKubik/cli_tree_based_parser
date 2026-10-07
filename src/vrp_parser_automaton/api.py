"""Small public facades for line and configuration parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from vrp_parser_automaton.automata.compiler import PatternCompiler
from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.catalogs.documentation import documentation_parameter
from vrp_parser_automaton.catalogs.source import PatternCatalog
from vrp_parser_automaton.context.configuration import (
    ConfigurationLayout,
    ContextSession,
)
from vrp_parser_automaton.context.transitions import TransitionRules
from vrp_parser_automaton.context.views import ViewMatchers
from vrp_parser_automaton.runtime.resolution import ResolvedMatch

from .errors import PatternDocumentError
from .parameters import (
    ParameterTypeRegistry,
    default_parameter_registry,
)
from .results import (
    BlankLine,
    ErrorLine,
    LineResult,
    MatchStatus,
    ParsedCommand,
    ParseError,
    ParseReport,
    ParseReportFactory,
)


class CommandLineParser:
    """Compile command patterns once and parse one physical CLI line."""

    def __init__(
        self,
        pattern_document: Mapping[str, Any],
        *,
        parameter_types: ParameterTypeRegistry | None = None,
        mapping: Mapping[str, Any] | None = None,
    ) -> None:
        catalog = PatternCatalog.read(pattern_document)
        commands = tuple(command.format for command in catalog.commands)
        source_registry = parameter_types or default_parameter_registry()
        registry = source_registry.clone()
        if catalog.documentation:
            registry.register(documentation_parameter())
        self._parameter_types = registry.freeze()
        self._graph = PatternCompiler(self._parameter_types).compile(
            commands, documentation=catalog.documentation
        )
        catalog.validate_parameters(self._graph)
        self._entry_view = catalog.entry_view
        self._views = catalog.views
        self._pattern_views = tuple(command.view for command in catalog.commands)
        self._matchers = ViewMatchers(self._graph, catalog, self._parameter_types)
        self._transitions = TransitionRules(catalog, self._graph, mapping)

    @property
    def entry_view(self) -> str | None:
        return self._entry_view

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
        """Parse in the requested view, defaulting to the grouped catalog entry."""
        return self._parse_at(
            line, line_number, self.entry_view if view is None else view
        )

    def parse_flat(self, line: str, line_number: int = 1) -> LineResult:
        """Explicitly search all source formats, including all grouped views."""
        return self._parse_at(line, line_number, None)

    def child_view(self, command: ParsedCommand) -> str | None:
        """Known context for a nested block, or None when it needs flat parsing."""
        return self._transitions.for_command(command)

    def _parse_at(self, line: str, line_number: int, view: str | None) -> LineResult:
        self._validate_input(line, line_number)
        matcher = self._matchers.for_view(view)
        indent_end = self._indent_end(line)
        indent = line[:indent_end]
        command = line[indent_end:].rstrip()
        if not command:
            return BlankLine(line_number, line, indent)

        outcome = matcher.match(command, span_offset=indent_end)
        if isinstance(outcome, ParseError):
            return ErrorLine(line_number, line, indent, outcome, view)
        result = self._parsed(line_number, line, indent, outcome)
        if not self.views:
            return result
        return replace(
            result,
            status=MatchStatus.AMBIGUOUS
            if len({self._pattern_views[m.pattern_index] for m in result.matches}) > 1
            else result.status,
            view=view,
        )

    @staticmethod
    def _parsed(
        line_number: int,
        raw: str,
        indent: str,
        outcome: ResolvedMatch,
    ) -> ParsedCommand:
        return ParsedCommand(
            line_number=line_number,
            raw=raw,
            indent=indent,
            status=outcome.status,
            primary_match=outcome.primary_match,
            alternative_matches=outcome.alternative_matches,
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
        mapping: Mapping[str, Any] | None = None,
    ) -> CommandLineParser:
        try:
            document = json.loads(source)
        except json.JSONDecodeError as error:
            raise PatternDocumentError(f"invalid pattern JSON: {error}") from error
        if not isinstance(document, Mapping):
            raise PatternDocumentError("pattern JSON root must be an object")
        return cls(document, parameter_types=parameter_types, mapping=mapping)

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        parameter_types: ParameterTypeRegistry | None = None,
        mapping: Mapping[str, Any] | None = None,
    ) -> CommandLineParser:
        return cls.from_json(
            Path(path).read_text(encoding="utf-8"),
            parameter_types=parameter_types,
            mapping=mapping,
        )


class ConfigurationParser:
    """Apply one reusable line parser to a complete configuration."""

    def __init__(
        self,
        line_parser: CommandLineParser,
        report_factory: ParseReportFactory | None = None,
        *,
        contextual: bool | None = None,
        layout: ConfigurationLayout | None = None,
    ) -> None:
        self._line_parser = line_parser
        self._report_factory = report_factory or ParseReportFactory()
        self._contextual = (
            line_parser.entry_view is not None if contextual is None else contextual
        )
        if self._contextual and line_parser.entry_view is None:
            raise ValueError("contextual parsing requires a grouped catalog")
        self._layout = layout or ConfigurationLayout()

    @property
    def line_parser(self) -> CommandLineParser:
        return self._line_parser

    def parse(self, content: str) -> ParseReport:
        if not isinstance(content, str):
            raise TypeError("configuration content must be a string")
        physical = self._physical_lines(content)
        if self._contextual:
            return self._report_factory.create(
                ContextSession(self._line_parser, self._layout).parse(physical)
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
