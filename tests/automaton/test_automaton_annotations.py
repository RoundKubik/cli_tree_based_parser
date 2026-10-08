"""Per-command type annotations reuse validators without rewriting source formats."""

import pytest

from vrp_format_matcher import FormatMatcher
from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    ErrorLine,
    ParsedCommand,
    PatternCompilationError,
    PatternDocumentError,
    UnresolvedCommand,
)


def annotated(pattern, **types):
    return {
        "format": pattern,
        "parameter_types": [
            {"parameter_name": name, "parameter_type": type_id}
            for name, type_id in types.items()
        ],
    }


@pytest.mark.parametrize(
    "type_id,value,normalized,invalid",
    [
        ("integer", "-002", -2, "abc"),
        ("string", "value", "value", "two words"),
        ("passwordex", "opaque-value", "opaque-value", "two words"),
        ("ipv4-address", "192.0.2.1", "192.0.2.1", "192.0.2.999"),
        ("ipv6-address", "2001:DB8::1", "2001:db8::1", "2001:db8::wrong"),
        ("ipv6-prefix", "2001:DB8::/64", "2001:db8::/64", "2001:db8::/129"),
        ("hex", "0xFF", 255, "xyz"),
        ("mac", "a-b-c", "000a-000b-000c", "aaaa-bbbb-cccc-dddd"),
        ("date-slash", "2024/02/29", "2024/02/29", "2023/02/29"),
        ("date-iso", "2024-02-29", "2024-02-29", "2023-02-29"),
        ("month-day", "02-29", "02-29", "02-30"),
        ("date-us", "02-29-2024", "02-29-2024", "02-29-2023"),
        (
            "datetime-slash",
            "2024/02/29,12:30:00",
            "2024/02/29,12:30:00",
            "2024/02/29,25:00:00",
        ),
        ("time-seconds", "12:30:00", "12:30:00", "25:00:00"),
        ("time", "12:30", "12:30", "25:00"),
        ("text", "two words", "two words", ""),
    ],
)
def test_named_types_validate_and_normalize_like_device_types(
    type_id, value, normalized, invalid
):
    parser = CommandLineParser({"commands": [annotated("set <value>", value=type_id)]})
    result = parser.parse(f"  set {value}")
    assert isinstance(result, ParsedCommand)
    (parameter,) = result.parameters
    assert parameter.type_id == type_id
    assert parameter.normalized == normalized
    assert parameter.declaration == "<value>"
    assert parameter.slot_id == "p:4"
    assert result.raw[parameter.span.start : parameter.span.end] == value
    assert isinstance(parser.parse(f"set {invalid}"), ErrorLine)


def test_annotations_do_not_invent_numeric_or_length_bounds():
    parser = CommandLineParser(
        {"commands": [annotated("set <id> <label>", id="integer", label="string")]}
    )
    result = parser.parse("set -999999999999999999999999 " + "x" * 300)
    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized == -999999999999999999999999
    assert len(result.parameters[1].normalized) == 300


def test_missing_and_unknown_types_preserve_unvalidated_named_parameters():
    record = annotated("set <id> <unknown> <missing>", id="integer", unknown="unknown")
    result = CommandLineParser({"commands": [record]}).parse("set 001 abc def")
    assert isinstance(result, ParsedCommand)
    assert [p.type_id for p in result.parameters] == ["integer", "named", "named"]
    assert [p.normalized for p in result.parameters] == [1, "abc", "def"]


def test_types_are_local_to_each_command_even_for_identical_formats():
    parser = CommandLineParser(
        {
            "commands": [
                annotated("acl <id>", id="integer"),
                annotated("acl <id>", id="string"),
            ]
        }
    )
    number = parser.parse("acl 2018")
    name = parser.parse("acl my-acl")
    assert number.primary_match.pattern_index == 0
    assert name.primary_match.pattern_index == 1
    assert number.parameters[0].normalized == 2018
    assert name.parameters[0].normalized == "my-acl"


def test_grouped_validation_selects_the_transition_and_preserves_context():
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "system",
            "views": {
                "system": [
                    {**annotated("acl <id>", id="integer"), "switch_to_view": "number"},
                    {**annotated("acl <id>", id="string"), "switch_to_view": "name"},
                ],
                "number": [annotated("rule <id>", id="integer")],
                "name": [annotated("label <id>", id="string")],
            },
        }
    )
    report = ConfigurationParser(parser).parse("acl 2018\n rule 5\nacl named\n label x")
    assert not report.has_errors and not report.has_unresolved
    assert [line.view for line in report.lines] == [
        "system",
        "number",
        "system",
        "name",
    ]


def test_foreign_view_fallback_validates_annotated_parameters():
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "system",
            "views": {
                "system": [{"format": "keep"}],
                "other": [annotated("acl <id>", id="integer")],
            },
        }
    )
    assert isinstance(parser.parse("acl incorrect"), ErrorLine)
    valid = parser.parse("acl 2018")
    assert isinstance(valid, UnresolvedCommand)
    assert valid.parameters[0].normalized == 2018


def test_mapping_ids_slots_and_iterations_survive_annotation_binding():
    pattern = "vlan { <id> [ to <id> ] } &<1-3>"
    record = annotated(pattern, id="integer")
    parser = CommandLineParser({"commands": [record]})
    plain = CommandLineParser({"commands": [pattern]}).parse("vlan 1 to 2 3")
    result = parser.parse("vlan 1 to 2 3")
    assert result.primary_match.pattern_id == plain.primary_match.pattern_id
    assert result.primary_match.original_pattern == pattern
    assert result.primary_match.variation_id == plain.primary_match.variation_id
    assert [p.normalized for p in result.parameters] == [1, 2, 3]
    assert [(p.slot_id, p.iterations) for p in result.parameters] == [
        (p.slot_id, p.iterations) for p in plain.parameters
    ]
    mapping = FormatMatcher().compile(parser, [record]).to_dict()
    saved = mapping["devices"][result.primary_match.pattern_id]
    assert {p.slot_id for p in result.parameters} == saved["slots"].keys()
    assert len(saved["mappings"][0]["bindings"]) == 2
    assert isinstance(parser.parse("vlan 1 to wrong 3"), ErrorLine)


def test_explicit_types_and_bounds_take_precedence_over_named_annotations():
    record = annotated("clock <hh:mm> INTEGER<1-9>", **{"hh:mm": "string"})
    parser = CommandLineParser({"commands": [record]})
    valid = parser.parse("clock 12:30 5")
    assert [p.type_id for p in valid.parameters] == ["time", "integer"]
    assert isinstance(parser.parse("clock invalid 5"), ErrorLine)
    assert isinstance(parser.parse("clock 12:30 10"), ErrorLine)


@pytest.mark.parametrize(
    "annotations",
    [
        None,
        {},
        "integer",
        [None],
        [{}],
        [{"parameter_name": "id", "parameter_type": 1}],
    ],
)
def test_malformed_annotations_fail_at_construction(annotations):
    with pytest.raises(PatternDocumentError, match="parameter_"):
        CommandLineParser(
            {"commands": [{"format": "acl <id>", "parameter_types": annotations}]}
        )


def test_duplicate_type_annotations_fail_even_when_they_agree():
    record = annotated("acl <id>", id="integer")
    record["parameter_types"] *= 2
    with pytest.raises(PatternDocumentError, match="Duplicate"):
        CommandLineParser({"commands": [record]})


@pytest.mark.parametrize("type_id", ["misspelled", "enum"])
def test_unsupported_types_fail_instead_of_silently_disabling_validation(type_id):
    with pytest.raises(PatternCompilationError, match="Unsupported parameter type"):
        CommandLineParser({"commands": [annotated("acl <id>", id=type_id)]})


def test_annotation_for_an_absent_parameter_fails_at_construction():
    with pytest.raises(PatternCompilationError, match="absent named parameters"):
        CommandLineParser({"commands": [annotated("acl <id>", wrong="integer")]})


@pytest.mark.parametrize(
    "pattern", ["description <value> suffix", "description { <value> } &<1-3>"]
)
def test_named_text_keeps_the_existing_terminal_non_repeated_policy(pattern):
    with pytest.raises(PatternCompilationError):
        CommandLineParser({"commands": [annotated(pattern, value="text")]})
