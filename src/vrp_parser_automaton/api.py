"""Small public facades for line and configuration parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vrp_parser_automaton.automata.compiler import PatternCompiler
from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.runtime.matcher import CommandMatcher
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
    ) -> None:
        commands = self._commands(pattern_document)
        source_registry = parameter_types or default_parameter_registry()
        self._parameter_types = source_registry.clone().freeze()
        self._graph = PatternCompiler(self._parameter_types).compile(commands)
        self._matcher = CommandMatcher(self._graph, self._parameter_types)

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

    def parse(self, line: str, line_number: int = 1) -> LineResult:
        self._validate_input(line, line_number)
        indent_end = self._indent_end(line)
        indent = line[:indent_end]
        command = line[indent_end:].rstrip()
        if not command:
            return BlankLine(line_number, line, indent)

        outcome = self._matcher.match(command, span_offset=indent_end)
        if isinstance(outcome, ParseError):
            return ErrorLine(line_number, line, indent, outcome)
        return self._parsed(line_number, line, indent, outcome)

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
    ) -> CommandLineParser:
        try:
            document = json.loads(source)
        except json.JSONDecodeError as error:
            raise PatternDocumentError(f"invalid pattern JSON: {error}") from error
        if not isinstance(document, Mapping):
            raise PatternDocumentError("pattern JSON root must be an object")
        return cls(document, parameter_types=parameter_types)

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        parameter_types: ParameterTypeRegistry | None = None,
    ) -> CommandLineParser:
        return cls.from_json(
            Path(path).read_text(encoding="utf-8"),
            parameter_types=parameter_types,
        )

    @staticmethod
    def _commands(document: Mapping[str, Any]) -> tuple[str, ...]:
        if "commands" not in document:
            raise PatternDocumentError("pattern document requires 'commands'")
        value = document["commands"]
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise PatternDocumentError("'commands' must be an array of strings")
        commands: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise PatternDocumentError(f"command at index {index} must be a string")
            if not item.strip():
                raise PatternDocumentError(f"command at index {index} cannot be empty")
            commands.append(item)
        if not commands:
            raise PatternDocumentError("'commands' cannot be empty")
        return tuple(commands)


class ConfigurationParser:
    """Apply one reusable line parser to a complete configuration."""

    def __init__(
        self,
        line_parser: CommandLineParser,
        report_factory: ParseReportFactory | None = None,
    ) -> None:
        self._line_parser = line_parser
        self._report_factory = report_factory or ParseReportFactory()

    @property
    def line_parser(self) -> CommandLineParser:
        return self._line_parser

    def parse(self, content: str) -> ParseReport:
        if not isinstance(content, str):
            raise TypeError("configuration content must be a string")
        lines = tuple(
            self._line_parser.parse(raw, line_number)
            for line_number, raw in enumerate(
                self._physical_lines(content),
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
