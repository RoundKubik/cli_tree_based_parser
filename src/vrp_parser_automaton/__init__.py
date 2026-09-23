"""Drop-in parsing facade backed by a compact automaton compiled from AST."""

from vrp_parser_automaton.automata.model import CommandAutomaton, Instruction

from .api import CommandLineParser, ConfigurationParser
from .errors import (
    PatternCompilationError,
    PatternDocumentError,
    PatternIssue,
)
from .parameters import (
    ParameterDeclaration,
    ParameterFamily,
    ParameterIssue,
    ParameterResult,
    ParameterStatus,
    ParameterType,
    ParameterTypeRegistry,
    default_parameter_registry,
)
from .results import (
    BlankLine,
    ErrorCode,
    ErrorLine,
    ExpectedElement,
    LineResult,
    MatchStatus,
    ParameterValue,
    ParsedCommand,
    ParseError,
    ParseReport,
    ParseSummary,
    PatternMatch,
    TextSpan,
    ValidationFailure,
    VariationStep,
)

__all__ = [
    "CommandAutomaton",
    "Instruction",
    "BlankLine",
    "CommandLineParser",
    "ConfigurationParser",
    "ErrorCode",
    "ErrorLine",
    "ExpectedElement",
    "LineResult",
    "MatchStatus",
    "ParameterDeclaration",
    "ParameterFamily",
    "ParameterIssue",
    "ParameterResult",
    "ParameterStatus",
    "ParameterType",
    "ParameterTypeRegistry",
    "ParameterValue",
    "ParseError",
    "ParseReport",
    "ParseSummary",
    "ParsedCommand",
    "PatternCompilationError",
    "PatternDocumentError",
    "PatternIssue",
    "PatternMatch",
    "TextSpan",
    "ValidationFailure",
    "VariationStep",
    "default_parameter_registry",
]
