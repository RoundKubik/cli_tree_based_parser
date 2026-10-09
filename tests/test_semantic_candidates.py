"""Examples connect runtime alternatives to original semantics without guessing."""

import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest
from test_format_matcher_catalogs import catalog, command, document

from examples.semantic_candidates import direct_candidates, mapped_candidates
from vrp_format_matcher import FormatMatcher
from vrp_parser_automaton import CommandLineParser


def test_system_creation_and_nested_references_use_distinct_document_records():
    pattern = "acl INTEGER<1-9999>"
    target = catalog(
        "device",
        views={
            "root": [command(pattern)],
            "a": [command(pattern)],
            "b": [command(pattern)],
        },
    )
    create = {
        **document("acl <id>"),
        "creates": [{"kind": "entity", "entity_type": "acl", "parameter_name": "id"}],
    }
    require = {
        **document("acl <number>"),
        "requires": [
            {"kind": "entity", "entity_type": "acl", "parameter_name": "number"}
        ],
    }
    docs = catalog(
        "documentation", views={"R": [create], "A": [require], "B": [require]}
    )
    mapping = json.loads(
        json.dumps(FormatMatcher().compile_catalogs(target, docs).to_dict())
    )
    original = deepcopy((target, docs, mapping))
    parser = CommandLineParser(target)
    root = mapped_candidates(parser.parse("acl 2018"), mapping, docs)
    nested = mapped_candidates(parser.parse(" acl 2018"), mapping, docs)
    assert len(root) == 1 and root[0]["creates"] == create["creates"]
    assert len(nested) == 4
    assert {c["source"]["view"] for c in nested} == {"A", "B"}
    assert len({c["pattern_id"] for c in nested}) == 2
    assert all(
        c["requires"] == require["requires"] and not c["creates"] for c in nested
    )
    assert all(c["parameters"][0]["value"] == 2018 for c in root + nested)
    assert (target, docs, mapping) == original


def test_repeated_slots_and_absent_optional_parameters_keep_their_coordinates():
    target = catalog(
        "device", [command("vlan { INTEGER<1-99> [ to INTEGER<1-99> ] } &<1-3>")]
    )
    docs = catalog("documentation", [document("vlan { <first> [ to <last> ] } &<1-3>")])
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    parsed = CommandLineParser(target).parse("vlan 10 to 20 30")
    (candidate,) = mapped_candidates(parsed, saved, docs)
    assert {
        (p["parameter_name"], p["value"], p["iterations"][0][1])
        for p in candidate["parameters"]
    } == {("first", 10, 0), ("last", 20, 0), ("first", 30, 1)}
    assert {p["iterations"][0][0] for p in candidate["parameters"]} == {"r:5"}


@pytest.mark.parametrize(
    "target_format,doc_format,line",
    [
        (
            "c INTEGER<1-9> [ to INTEGER<1-9> ]",
            "c { <single> | <first> to <last> }",
            "c 1 to 2",
        ),
        ("c INTEGER<1-9> device", "c <id> documentation", "c 1 device"),
    ],
)
def test_conditional_or_prefix_bindings_are_not_applied_as_whole_command_semantics(
    target_format, doc_format, line
):
    target = catalog("device", [command(target_format)])
    docs = catalog("documentation", [document(doc_format)])
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    (candidate,) = mapped_candidates(CommandLineParser(target).parse(line), saved, docs)
    assert candidate["needs_trace"] is True
    assert "parameters" not in candidate
    assert candidate["automaton_id"] in saved["automata"]


def test_keyword_only_semantics_are_preserved_without_bindings():
    target = catalog("device", [command("enable")])
    docs = catalog(
        "documentation",
        [{"format": "enable", "requires": [{"kind": "command", "name": "prepare"}]}],
    )
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    (candidate,) = mapped_candidates(
        CommandLineParser(target).parse("enable"), saved, docs
    )
    assert candidate["parameters"] == [] and candidate["requires"]


def test_direct_documentation_uses_source_indices_and_retains_all_captures():
    docs = catalog(
        "documentation",
        views={
            "root": [document("enter")],
            "A": [document("c <id> &<1-3>")],
            "B": [document("c <id> &<1-3>")],
        },
    )
    parsed = CommandLineParser(docs).parse(" c 1 2")
    candidates = direct_candidates(parsed, docs)
    assert [c["source"] for c in candidates] == [
        {"view": "A", "index": 0},
        {"view": "B", "index": 0},
    ]
    assert all([p["normalized"] for p in c["parameters"]] == [1, 2] for c in candidates)


def test_alternative_slot_assignments_remain_separate_semantic_candidates():
    target = catalog("device", [command("c [ INTEGER<1-9> ] [ INTEGER<1-9> ]")])
    docs = catalog("documentation", [document("c [ <a> ] [ <b> ]")])
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    parsed = CommandLineParser(target).parse("c 1")
    candidates = mapped_candidates(parsed, saved, docs)
    assert {c["parameters"][0]["parameter_name"] for c in candidates} == {"a", "b"}
    assert len({c["variation_id"] for c in candidates}) == 2


def test_changed_documentation_is_rejected_instead_of_attaching_unrelated_semantics():
    target = catalog("device", [command("c")])
    docs = catalog("documentation", [document("c")])
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    docs["commands"][0]["format"] = "different"
    with pytest.raises(ValueError, match="documentation has changed"):
        mapped_candidates(CommandLineParser(target).parse("c"), saved, docs)


def test_example_cli_accepts_the_original_files(tmp_path):
    target = catalog(
        "device",
        views={"root": [command("enter")], "A": [command("value INTEGER<1-9>")]},
    )
    docs = catalog(
        "documentation", views={"R": [document("enter")], "C": [document("value <id>")]}
    )
    saved = FormatMatcher().compile_catalogs(target, docs).to_dict()
    for name, value in [("target", target), ("docs", docs), ("mapping", saved)]:
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    config = tmp_path / "config.txt"
    config.write_text("enter\n value 5")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.semantic_candidates",
            "--patterns",
            str(tmp_path / "target.json"),
            "--documentation",
            str(tmp_path / "docs.json"),
            "--mapping",
            str(tmp_path / "mapping.json"),
            "--config",
            str(config),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    output = json.loads(completed.stdout)
    assert output[1]["candidates"][0]["parameters"][0]["value"] == 5
