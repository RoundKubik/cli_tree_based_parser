"""Contextual parsing preserves runtime slots without repeating offline evidence."""

from __future__ import annotations

import copy
import json

import pytest
from test_format_matcher_catalogs import catalog, command, document

from vrp_format_matcher import FormatMatcher
from vrp_parser_automaton import (
    BlankLine,
    CommandLineParser,
    ConfigurationLayout,
    ConfigurationParser,
    ErrorLine,
    MatchStatus,
    ParsedCommand,
    PatternDocumentError,
    SeparatorLine,
)


def hierarchy(source="device"):
    record = command if source == "device" else document
    return catalog(
        source,
        views={
            "root": [
                {**record("enter"), "switch_to_view": "child"},
                {**record("keep"), "switch_to_view": None},
                record("unknown"),
                record("root-only"),
                record("same"),
            ],
            "child": [
                {**record("nested"), "switch_to_view": "leaf"},
                {**record("child-only"), "switch_to_view": None},
                record("same"),
            ],
            "leaf": [record("leaf-only")],
        },
    )


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_grouped_parsing_limits_recognition_and_suggestions_to_the_requested_view(
    source,
):
    parser = CommandLineParser(hierarchy(source))
    assert parser.parse("same").primary_match.pattern_index == 4
    assert parser.parse("same", view="child").primary_match.pattern_index == 7
    outside = parser.parse("child-only")
    assert isinstance(outside, ErrorLine) and outside.view == "root"
    assert "child-only" not in outside.error.suggestions
    with pytest.raises(ValueError, match="unknown view"):
        parser.parse("same", view="missing")
    # Source context matters even if the two formats have identical token languages.
    flat = parser.parse_flat("same")
    assert flat.status == MatchStatus.AMBIGUOUS and flat.view is None
    assert len(flat.matches) == 2
    assert parser.child_view(flat) is None


def test_parameters_from_another_view_do_not_change_validation_or_slot_ids():
    data = catalog(
        "device",
        views={
            "root": [command("set INTEGER<1-9>")],
            "child": [command("set STRING<1-9>")],
        },
    )
    parser = CommandLineParser(data)
    result = parser.parse("set abc")
    assert isinstance(result, ErrorLine)
    assert {f.type_id for f in result.error.failures} == {"integer"}
    allowed = parser.parse("set abc", view="child")
    assert isinstance(allowed, ParsedCommand)
    assert allowed.parameters[0].slot_id == "p:4"


def test_nested_blocks_dedents_separators_blanks_and_new_sessions():
    parser = ConfigurationParser(CommandLineParser(hierarchy()))
    report = parser.parse(
        "enter\n child-only\n nested\n  leaf-only\n\n child-only\n#\nroot-only\n"
    )
    assert not report.has_errors
    assert [r.view for r in report.lines if isinstance(r, ParsedCommand)] == [
        "root",
        "child",
        "child",
        "leaf",
        "child",
        "root",
    ]
    assert isinstance(report.lines[4], BlankLine)
    assert isinstance(report.lines[6], SeparatorLine)
    assert report.summary.commands == 6 and report.summary.total == 8
    assert parser.parse("root-only").lines[0].view == "root"


def test_unknown_transition_uses_flat_parsing_until_the_known_parent_resumes():
    report = ConfigurationParser(CommandLineParser(hierarchy())).parse(
        "unknown\n same\n nested\n  leaf-only\nroot-only"
    )
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", None, None, None, "root"]
    assert report.lines[1].status == MatchStatus.AMBIGUOUS
    # A unique flat match does not establish a view or restore its transitions.
    assert report.lines[2].status == MatchStatus.UNIQUE
    assert report.lines[3].view is None


def test_error_in_a_known_view_does_not_silently_retry_other_views():
    report = ConfigurationParser(CommandLineParser(hierarchy())).parse(
        "enter\n leaf-only\n  root-only\n child-only"
    )
    assert isinstance(report.lines[1], ErrorLine)
    assert report.lines[1].view == "child"
    assert report.lines[2].view is None
    assert report.lines[3].view == "child"


def test_null_transition_keeps_context_and_custom_layout_handles_tabs():
    parser = ConfigurationParser(
        CommandLineParser(hierarchy()),
        layout=ConfigurationLayout(separators=("!",), tab_width=4),
    )
    report = parser.parse("keep\n\tenter\n        child-only\n!\nroot-only")
    assert not report.has_errors
    assert [r.view for r in report.lines if isinstance(r, ParsedCommand)] == [
        "root",
        "root",
        "child",
        "root",
    ]
    assert report.lines[2].primary_match.parameters == ()


def test_conflicting_or_missing_declarations_do_not_choose_a_transition():
    data = hierarchy()
    data["views"]["root"] += [
        {"format": "enter", "switch_to_view": "leaf"},
        {"format": "keep"},
    ]
    parser = CommandLineParser(data)
    assert parser.child_view(parser.parse("enter")) is None
    assert parser.child_view(parser.parse("keep")) is None


def test_explicit_flat_mode_keeps_legacy_line_handling():
    parser = ConfigurationParser(CommandLineParser(hierarchy()), contextual=False)
    report = parser.parse("root-only\n leaf-only\n#")
    assert all(r.view is None for r in report.lines)
    assert isinstance(report.lines[2], ErrorLine)
    with pytest.raises(ValueError, match="grouped catalog"):
        ConfigurationParser(CommandLineParser({"commands": ["same"]}), contextual=True)


@pytest.mark.parametrize("source", ["device", "documentation"])
def test_flat_and_grouped_catalogs_keep_global_ids_and_runtime_slot_locations(source):
    pattern = "c <id> &<1-3>" if source == "documentation" else "c INTEGER<1-9> &<1-3>"
    record = document if source == "documentation" else command
    grouped = catalog(
        source,
        views={
            "first": [record(pattern)],
            "second": [record(pattern)],
        },
    )
    flat = catalog(source, [record(pattern), record(pattern)])
    parser = CommandLineParser(grouped)
    parsed = parser.parse(" c 1 2", view="second")
    flat_parsed = CommandLineParser(flat).parse(" c 1 2")
    assert parsed.primary_match == flat_parsed.matches[1]
    mapping = (
        FormatMatcher().compile_catalogs(grouped, grouped).to_dict()
        if (source == "documentation")
        else FormatMatcher()
        .compile_catalogs(
            grouped, catalog("documentation", [document("c <id> &<1-3>")])
        )
        .to_dict()
    )
    saved = mapping["devices"][parsed.primary_match.pattern_id]
    assert saved["source"] == {"view": "second", "index": 0}
    assert {p.slot_id for p in parsed.parameters} <= saved["slots"].keys()
    assert [p.raw for p in parsed.parameters] == ["1", "2"]
    assert parsed.parameters[0].iterations != parsed.parameters[1].iterations
    assert [parsed.raw[p.span.start : p.span.end] for p in parsed.parameters] == [
        "1",
        "2",
    ]


def test_document_annotations_do_not_validate_values_and_date_words_are_literals():
    data = catalog(
        "documentation", [document("set <id>"), document("clock YYYY-MM-DD")]
    )
    parser = CommandLineParser(data)
    result = parser.parse("set not-an-integer")
    assert isinstance(result, ParsedCommand)
    assert result.parameters[0].normalized == "not-an-integer"
    assert result.parameters[0].declaration == "<id>"
    assert parser.parse("clock YYYY-MM-DD").parameters == ()
    assert isinstance(parser.parse("clock 2024-01-01"), ErrorLine)


def test_json_only_adds_a_known_view_and_keeps_existing_null_parameter_values():
    data = catalog("device", [command("set INTEGER<1-9>")])
    legacy = ConfigurationParser(CommandLineParser({"commands": ["set INTEGER<1-9>"]}))
    flat = ConfigurationParser(CommandLineParser(data))
    assert (
        flat.parse("set 5\nwrong").to_dict() == legacy.parse("set 5\nwrong").to_dict()
    )
    report = (
        ConfigurationParser(CommandLineParser(hierarchy()))
        .parse("unknown\n same\n#\nwrong")
        .to_dict()
    )
    assert report["lines"][0]["view"] == "root"
    assert "view" not in report["lines"][1]
    assert report["lines"][2] == {
        "line_number": 3,
        "raw": "#",
        "indent": "",
        "kind": "separator",
    }
    assert report["lines"][3]["view"] == "root"
    assert "view" not in report["lines"][0]["primary_match"]
    assert (
        not {"context", "candidates", "transition", "mode"} & report["lines"][0].keys()
    )
    assert "separators" not in report["summary"]
    json.dumps(report)


@pytest.mark.parametrize(
    "change",
    [
        {"entry_view": []},
        {"entry_view": "absent"},
        {"source": []},
        {"views": []},
        {"commands": []},
        {"schema_version": True},
    ],
)
def test_malformed_grouped_catalogs_raise_document_errors(change):
    data = hierarchy()
    data.update(change)
    with pytest.raises(PatternDocumentError):
        CommandLineParser(data)


@pytest.mark.parametrize(
    "types",
    [
        [],
        [{"parameter_name": "id", "parameter_type": []}],
        [{"parameter_name": "id", "parameter_type": "unknown"}],
        [{"parameter_name": "extra", "parameter_type": "string"}],
    ],
)
def test_invalid_document_annotations_are_rejected(types):
    data = catalog("documentation", [{"format": "set <id>", "parameter_types": types}])
    with pytest.raises(PatternDocumentError):
        CommandLineParser(data)


def test_single_offline_view_candidate_remains_unknown_and_does_not_restrict_fallback():
    device = catalog(
        "device",
        views={
            "root": [command("enter")],
            "candidate": [command("rule INTEGER<1-9>")],
            "other": [command("unrelated")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("enter"), "switch_to_view": "Child"}],
            "Child": [document("rule <id>")],
        },
    )
    saved = json.loads(
        json.dumps(FormatMatcher().compile_catalogs(device, docs).to_dict())
    )
    assert len(saved["hierarchy"]["targets"]["Child"]["candidates"]) == 1
    report = ConfigurationParser(CommandLineParser(device, mapping=saved)).parse(
        "enter\n rule 1\n unrelated"
    )
    assert not report.has_errors
    assert [r.view for r in report.lines] == ["root", None, None]


@pytest.mark.parametrize("parameters", [False, True])
@pytest.mark.parametrize("brackets", [("[", "]"), ("{", "}")])
def test_offline_transition_applies_only_inside_the_intersection_scope(
    parameters, brackets
):
    a = "a INTEGER<1-9>" if parameters else "a"
    b = "b INTEGER<1-9>" if parameters else "b"
    doc_a = "a <id>" if parameters else "a"
    left, right = brackets
    device = catalog(
        "device", views={"root": [command(f"c {left} {a} | {b} {right} *")]}
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document(f"c {doc_a}"), "switch_to_view": None}],
        },
    )
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    parser = CommandLineParser(device, mapping=saved)
    exact = "c a 1" if parameters else "c a"
    extra = "c b 2 a 1" if parameters else "c b a"
    assert parser.child_view(parser.parse(exact)) == "root"
    assert parser.child_view(parser.parse(extra)) is None
    assert parser.child_view(parser.parse("c b 2" if parameters else "c b")) is None


def test_scoped_repeat_transition_uses_captured_iteration_coordinates():
    device = catalog(
        "device",
        views={
            "root": [command("c INTEGER<1-9> &<1-3>")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("c <id> &<2-2>"), "switch_to_view": None}],
        },
    )
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    parser = CommandLineParser(device, mapping=saved)
    assert parser.child_view(parser.parse(" c 1 2")) == "root"
    assert parser.child_view(parser.parse("c 1")) is None
    assert parser.child_view(parser.parse("c 1 2 3")) is None
    broken = copy.deepcopy(saved)
    for machine in broken["automata"].values():
        for edges in machine["edges"]:
            for edge in edges:
                if edge["label"] == "P":
                    edge["device"]["iterations"] = []
    parser = CommandLineParser(device, mapping=broken)
    assert parser.child_view(parser.parse("c 1 2")) is None


def test_resolved_destination_does_not_prove_an_unresolved_source_view():
    device = catalog(
        "device",
        views={
            "root": [{"format": "enter", "switch_to_view": "child"}],
            "child": [command("return")],
        },
    )
    docs = catalog(
        "documentation",
        views={
            "Root": [],
            "Child": [{**document("return"), "switch_to_view": "Root"}],
        },
    )
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    parser = CommandLineParser(device, mapping=saved)
    assert parser.child_view(parser.parse("enter")) == "child"
    assert parser.child_view(parser.parse("return", view="child")) is None


def test_input_transition_wins_over_documentation_and_no_predicates_are_evaluated():
    device = hierarchy()
    docs = catalog(
        "documentation",
        views={
            "Root": [
                {**document("enter"), "switch_to_view": None, "requires": object()},
            ]
        },
    )
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    parser = CommandLineParser(device, mapping=saved)
    assert parser.child_view(parser.parse("enter")) == "child"


@pytest.mark.parametrize("mutation", ["id", "format", "location", "scope", "index"])
def test_mismatched_or_broken_offline_mapping_is_rejected(mutation):
    device = catalog("device", views={"root": [command("c [ a | b ] *")]})
    docs = catalog(
        "documentation",
        views={
            "Root": [{**document("c a"), "switch_to_view": None}],
        },
    )
    saved = FormatMatcher().compile_catalogs(device, docs).to_dict()
    identifier, record = next(iter(saved["devices"].items()))
    if mutation == "id":
        saved["devices"]["wrong"] = saved["devices"].pop(identifier)
    elif mutation == "format":
        record["device_format"] = "different"
    elif mutation == "location":
        record["source"]["index"] = 1
    elif mutation == "scope":
        next(iter(saved["automata"].values()))["start"] = -1
    else:
        saved["hierarchy"]["transitions"][identifier][0]["mapping_index"] = -1
    with pytest.raises(PatternDocumentError):
        CommandLineParser(device, mapping=saved)
