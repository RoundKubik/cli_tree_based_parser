from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import pytest

from vrp_parser_automaton import (
    BlankLine,
    CommandLineParser,
    ConfigurationParser,
    ErrorCode,
    ErrorLine,
    MatchStatus,
    ParameterDeclaration,
    ParameterFamily,
    ParameterResult,
    ParameterType,
    ParsedCommand,
    default_parameter_registry,
)
from vrp_parser_automaton.parameters import (
    ExactDeclarationRecognizer,
    SingleTokenReader,
)


class _AddressValidator:
    def probe(
        self,
        raw: str,
        declaration: ParameterDeclaration,
    ) -> ParameterResult:
        del declaration
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return ParameterResult.not_applicable()
        return ParameterResult.success(address)


def _parser() -> CommandLineParser:
    return CommandLineParser(
        {
            "commands": [
                "#",
                "TEXT<1-4096>",
                "interface STRING<1-63>",
                "preference INTEGER<1-15>",
            ]
        }
    )


def _configuration_parser() -> ConfigurationParser:
    return ConfigurationParser(_parser())


def test_line_parser_rejects_multiple_physical_lines() -> None:
    parser = _parser()

    with pytest.raises(ValueError, match="one line"):
        parser.parse("interface one\ninterface two")


@pytest.mark.parametrize("line_number", [0, -1])
def test_line_parser_requires_positive_line_number(line_number: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _parser().parse("#", line_number)


def test_line_parser_preserves_raw_indent_line_number_and_parameter_span() -> None:
    result = _parser().parse(
        "  interface GigabitEthernet0/0/1   ",
        line_number=7,
    )

    assert isinstance(result, ParsedCommand)
    assert result.line_number == 7
    assert result.raw == "  interface GigabitEthernet0/0/1   "
    assert result.indent == "  "
    assert result.parameters[0].raw == "GigabitEthernet0/0/1"
    assert result.parameters[0].span.start == len("  interface ")


def test_command_line_parser_can_be_built_from_json() -> None:
    parser = CommandLineParser.from_json('{"commands": ["display clock"]}')

    assert parser.command_count == 1
    assert isinstance(parser.parse("display clock"), ParsedCommand)


def test_command_line_parser_can_be_built_from_json_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "commands.json"
    source.write_text(
        '{"commands": ["display clock", "#"]}',
        encoding="utf-8",
    )

    parser = CommandLineParser.from_json_file(source)

    assert parser.command_count == 2
    assert isinstance(parser.parse("#"), ParsedCommand)


def test_configuration_parser_handles_crlf_lf_cr_and_continues_after_error() -> None:
    report = _configuration_parser().parse(
        "#\r\npreference 16\n\rinterface Vlanif100\r\n! generated\n"
    )

    assert [line.line_number for line in report.lines] == [1, 2, 3, 4, 5]
    assert isinstance(report.lines[0], ParsedCommand)
    assert isinstance(report.lines[1], ErrorLine)
    assert isinstance(report.lines[2], BlankLine)
    assert isinstance(report.lines[3], ParsedCommand)
    assert isinstance(report.lines[4], ParsedCommand)
    assert report.summary.total == 5
    assert report.summary.blank == 1
    assert report.summary.commands == 3
    assert report.summary.errors == 1
    assert report.has_errors


def test_ambiguous_line_counts_as_command_not_error() -> None:
    parser = ConfigurationParser(
        CommandLineParser({"commands": ["value INTEGER<1-10>", "value HEX<1-A>"]})
    )

    report = parser.parse("value 5")

    result = report.lines[0]
    assert isinstance(result, ParsedCommand)
    assert result.status is MatchStatus.AMBIGUOUS
    assert report.summary.commands == 1
    assert report.summary.ambiguous == 1
    assert report.summary.errors == 0
    assert not report.has_errors


def test_empty_content_has_no_synthetic_line() -> None:
    report = _configuration_parser().parse("")

    assert report.lines == ()
    assert report.summary.total == 0


def test_report_dictionary_is_json_serializable() -> None:
    report = _configuration_parser().parse("#\nunknown")

    encoded = json.dumps(report.to_dict(), ensure_ascii=False)

    assert '"kind": "command"' in encoded
    assert '"unknown_command"' in encoded


def test_report_stringifies_non_json_plugin_values() -> None:
    registry = default_parameter_registry()
    registry.register(
        ParameterType(
            type_id="address",
            family=ParameterFamily.STRUCTURED,
            declaration_recognizer=ExactDeclarationRecognizer("ADDRESS"),
            reader=SingleTokenReader(),
            validator=_AddressValidator(),
        )
    )
    parser = ConfigurationParser(
        CommandLineParser(
            {"commands": ["peer ADDRESS"]},
            parameter_types=registry,
        )
    )

    report = parser.parse("peer 192.0.2.1")
    payload = report.to_dict()
    encoded = json.dumps(payload)

    assert "192.0.2.1" in encoded


def test_incomplete_known_command_reports_syntax_error_at_end() -> None:
    result = _parser().parse("interface")

    assert isinstance(result, ErrorLine)
    assert result.error.code is ErrorCode.SYNTAX_ERROR
    assert result.error.position == len("interface")
    assert result.error.expected[0].description == "STRING<1-63>"
