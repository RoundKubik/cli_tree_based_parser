from __future__ import annotations

import pytest

from vrp_parser import (
    CommandLineParser,
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterType,
    ParsedCommand,
    PatternCompilationError,
    PatternDocumentError,
    default_parameter_registry,
)
from vrp_parser.parameters import ExactDeclarationRecognizer, SingleTokenReader


class _PrefixValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del declaration
        if ":" in raw and "/" in raw:
            return ParameterResult.success(raw.lower())
        return ParameterResult.not_applicable()


class _NoneValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del raw, declaration
        return ParameterResult.success(None)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"commands": "display clock"},
        {"commands": []},
        {"commands": ["display clock", 10]},
        {"commands": [""]},
    ],
)
def test_invalid_pattern_documents_are_rejected(document: object) -> None:
    with pytest.raises(PatternDocumentError):
        CommandLineParser(document)  # type: ignore[arg-type]


def test_frontend_collects_all_pattern_errors() -> None:
    with pytest.raises(PatternCompilationError) as caught:
        CommandLineParser({"commands": ["{ missing", "[ empty | ]"]})

    assert [issue.pattern_index for issue in caught.value.issues] == [0, 1]
    assert all(issue.span.start <= issue.span.end for issue in caught.value.issues)


@pytest.mark.parametrize(
    "pattern",
    [
        "peer X.X.X.X",
        "peer X:X::X:X",
        "description TEXT<1-80>",
        "pair STRING<1-10>/<1-10>",
        "description TEXT<1-4096>",
        "mac H-H-H/suffix",
        "clock <hh:mm>/suffix",
        "date YYYY-MM-DD/suffix",
        "time HH:MM:SS/suffix",
    ],
)
def test_disabled_or_unsupported_placeholders_fail_at_construction(
    pattern: str,
) -> None:
    with pytest.raises(PatternCompilationError):
        CommandLineParser({"commands": [pattern]})


def test_standalone_text_is_the_only_supported_text_declaration() -> None:
    parser = CommandLineParser({"commands": ["TEXT<1-4096>"]})

    assert parser.command_count == 1


def test_disabled_builtin_spelling_can_be_added_as_a_plugin() -> None:
    registry = default_parameter_registry()
    registry.register(
        ParameterType(
            type_id="ipv6-prefix",
            family=ParameterFamily.STRUCTURED,
            declaration_recognizer=ExactDeclarationRecognizer("X:X::X:X/M"),
            reader=SingleTokenReader(),
            validator=_PrefixValidator(),
        )
    )
    parser = CommandLineParser(
        {"commands": ["peer X:X::X:X/M"]},
        parameter_types=registry,
    )

    result = parser.parse("peer 2001:DB8::1/64")

    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized == "2001:db8::1/64"


def test_plugin_does_not_hide_a_later_disabled_placeholder() -> None:
    registry = default_parameter_registry()
    registry.register(
        ParameterType(
            type_id="ipv6-prefix",
            family=ParameterFamily.STRUCTURED,
            declaration_recognizer=ExactDeclarationRecognizer("X:X::X:X/M"),
            reader=SingleTokenReader(),
            validator=_PrefixValidator(),
        )
    )

    with pytest.raises(PatternCompilationError):
        CommandLineParser(
            {"commands": ["peer X:X::X:X/M X:X::X:X"]},
            parameter_types=registry,
        )


def test_plugin_can_use_none_as_a_valid_normalized_value() -> None:
    registry = default_parameter_registry()
    registry.register(
        ParameterType(
            type_id="unit",
            family=ParameterFamily.STRUCTURED,
            declaration_recognizer=ExactDeclarationRecognizer("UNIT"),
            reader=SingleTokenReader(),
            validator=_NoneValidator(),
        )
    )
    parser = CommandLineParser(
        {"commands": ["command UNIT"]},
        parameter_types=registry,
    )

    result = parser.parse("command anything")

    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized is None
