"""Corpus extraction preserves source identity and exposes unresolved semantics."""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import ValidationError

from scripts.corpus_pipeline.contract import (
    PARAMETER_TYPES,
    RESPONSE_SCHEMA,
    validate_response,
)
from scripts.corpus_pipeline.extraction import (
    assemble,
    extract_page,
    page_input,
    validate_responses,
)
from scripts.corpus_pipeline.mock_device import (
    PLACEHOLDERS,
    build_mock,
    convert_format,
    mock_placeholder,
)
from scripts.corpus_pipeline.selectors import ParameterCombination
from scripts.corpus_pipeline.source import Corpus, read_json, write_json
from vrp_parser_automaton import CommandLineParser
from vrp_parser_automaton.parameters.builtins import builtin_parameter_types


def test_mock_compound_number_bounds_do_not_become_a_scalar_integer():
    placeholder, fallback = mock_placeholder(
        "Formats: 2-byte AS number:4-byte user-defined number, such as 101:3. "
        "Each component is an integer ranging from 0 to 65535."
    )
    assert placeholder is None
    assert fallback is True


@pytest.mark.parametrize("family", ["IPv4", "IPv6"])
def test_mock_string_identifier_is_not_typed_as_its_related_address(family):
    assert mock_placeholder(
        f"Specifies a VPN instance bound to the source {family} address. "
        "The value is a string of 1 to 31 case-sensitive characters."
    ) == ("STRING<1-65535>", False)


def corpus_page(tmp_path: Path, formats: list[str]) -> Corpus:
    directory = tmp_path / "cmd_corpus"
    directory.mkdir(exist_ok=True)
    write_json(
        directory / "acl.json",
        {
            "PageTitle": "acl number",
            "CLIs": formats,
            "ParentView": ["System view"],
            "FuncDef": (
                "The acl number command creates an ACL and displays the ACL view."
            ),
            "ParaDef": [
                {"Parameters": "id", "Info": "An integer ranging from 2000 to 2999."}
            ],
        },
    )
    return Corpus(directory)


def test_mock_shared_declaration_preserves_flat_formats_and_separate_scope(tmp_path):
    corpus_page(tmp_path, ["acl <id>"])
    write_json(
        tmp_path / "cmd_corpus/shared.json",
        {"CLIs": ["quit", "display example"], "ParentView": ["common-id"]},
    )
    header = {"entry_view": "System view"}
    directory = tmp_path / "mock"
    build_mock(
        Corpus(tmp_path / "cmd_corpus"), header, directory, shared_views=("common-id",)
    )
    grouped = read_json(directory / "device_grouped.json")
    flat = read_json(directory / "device_flat.json")
    assert grouped["shared_views"] == ["common-id"]
    assert "shared_views" not in flat
    assert grouped["views"]["common-id"] == [{"format": "quit"}]
    assert [c["format"] for c in flat["commands"]] == ["acl INTEGER<2000-2999>", "quit"]


def annotation(index: int = 0) -> dict:
    return {
        "format_index": index,
        "description": "Create an ACL.",
        "switch_to_view": "ACL view",
        "switch_evidence": (
            "The acl number command creates an ACL and displays the ACL view."
        ),
        "parameter_types": [{"parameter_name": "id", "parameter_type": "integer"}],
        "creates": [{"kind": "entity", "parameter_name": "id", "entity_type": "acl"}],
        "requires": [],
        "issues": [],
    }


@pytest.mark.parametrize("selection", [["acl.json", "acl.json"], [], {}, [1]])
def test_cli_reads_saved_selection_without_processing_other_pages(
    tmp_path: Path, monkeypatch, selection
):
    from scripts.corpus_pipeline import __main__ as cli

    corpus_page(tmp_path, ["acl <id>"])
    write_json(
        tmp_path / "cmd_corpus/unselected.json",
        {"CLIs": ["unselected"], "ParentView": ["System view"]},
    )
    write_json(tmp_path / "selection.json", selection)
    agent = Mock(model="test", extract=Mock(return_value={"commands": [annotation()]}))
    monkeypatch.setattr(cli, "Codex", lambda *args: agent)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/mock/codex")
    monkeypatch.setattr(
        "sys.argv",
        [
            "corpus_pipeline",
            "extract",
            "--corpus",
            str(tmp_path / "cmd_corpus"),
            "--output-dir",
            str(tmp_path / "out"),
            "--model-type",
            "test",
            "--software-version",
            "test",
            "--model",
            "test",
            "--selection",
            str(tmp_path / "selection.json"),
        ],
    )
    if selection == ["acl.json", "acl.json"]:
        cli.main()
        assert agent.extract.call_count == 1
        assert read_json(tmp_path / "out/report.json")["pages_selected"] == 1
    else:
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
        agent.extract.assert_not_called()


def test_preserves_each_source_variant_and_parameterless_commands(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl [ number ] <id>", "undo acl all"])
    create, undo = annotation(), annotation(1)
    undo.update(
        switch_to_view=None, switch_evidence=None, parameter_types=[], creates=[]
    )
    result = validate_response(
        {"commands": [undo, create]}, corpus.pages["acl.json"], corpus
    )
    assert [record["format_index"] for record in result] == [0, 1]
    assert result[1]["creates"] == []


@pytest.mark.parametrize("indices", [[], [0, 0], [1], [True]])
def test_rejects_missing_duplicate_or_unknown_variant(tmp_path: Path, indices):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    with pytest.raises((ValueError, ValidationError)):
        validate_response(
            {"commands": [annotation(i) for i in indices]},
            corpus.pages["acl.json"],
            corpus,
        )


def test_rejects_agent_rewriting_formats(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = {**annotation(), "format": "acl <other>"}
    with pytest.raises(ValidationError):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


def test_unknown_parameter_type_requires_visible_issue(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = {**annotation(), "parameter_types": []}
    with pytest.raises(ValueError, match="Missing parameter"):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)
    record["issues"] = [{"field": "parameter_types", "message": "Type is unspecified."}]
    validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


def test_rejects_narrower_invented_acl_target(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = {**annotation(), "switch_to_view": "Advanced ACL view"}
    with pytest.raises(ValueError, match="verbatim"):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


def test_combination_must_remain_in_one_branch_in_original_order(tmp_path: Path):
    pattern = "interface { <name> | <type> <number> }"
    corpus = corpus_page(tmp_path, [pattern])
    record = annotation()
    record["parameter_types"] = [
        {"parameter_name": name, "parameter_type": "string"}
        for name in ("name", "type", "number")
    ]
    entity = {"kind": "entity", "entity_type": "interface"}
    for combination in (["name", "type"], ["number", "type"], ["type", "type"]):
        record["creates"] = [{**entity, "parameter_comb": combination}]
        with pytest.raises(ValueError, match="parameter_comb"):
            validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)
    record["creates"] = [
        {**entity, "parameter_name": "name"},
        {**entity, "parameter_comb": ["type", "number"]},
    ]
    validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


@pytest.mark.parametrize(
    ("pattern", "names", "expected"),
    [
        ("x <a> { <b> | <c> <d> } <e>", ("a", "b", "e"), False),
        ("x <a> { <b> | <c> <d> } <e>", ("a", "c", "d", "e"), False),
        ("x <a> { <b> | <c> <d> } <e>", ("c", "d"), True),
        ("x <a> { <b> | <c> <d> } <e>", ("b", "c"), False),
        ("x <a> marker <b>", ("a", "b"), False),
        ("x <a> <c> <b>", ("a", "b"), False),
        ("x <a> [ marker ] <b>", ("a", "b"), False),
        ("x <a> [ index <b> ]", ("a", "b"), False),
        ("x <a>  <b>", ("a", "b"), True),
        ("x <a><b>", ("a", "b"), False),
        ("x <a> <b>", ("b", "a"), False),
        ("x { <a> | <b> } &<1-1>", ("a", "b"), False),
        ("x { <a> | <b> } &<1-2>", ("a", "b"), False),
        ("x { <a> | <b> | <c> } &<1-2>", ("a", "b", "c"), False),
        ("x [ <a> | <b> ] *", ("b", "a"), False),
        ("x { <a> marker <b> | <c> } *", ("b", "c", "a"), False),
    ],
)
def test_combinations_require_adjacent_placeholders_in_the_source(
    pattern, names, expected
):
    assert ParameterCombination(pattern, names).exists() is expected


def test_unresolved_dependency_context_keeps_requirement(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = annotation()
    rule = {
        "kind": "command",
        "name": "acl number",
        "format": "acl <id>",
        "context": "unresolved",
    }
    record["requires"] = [rule]
    result = validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)
    assert result[0]["requires"] == [rule]
    record["requires"][0]["format"] = "acl [ number ] <id>"
    with pytest.raises(ValueError, match="original format"):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


def test_cache_reuses_raw_response_but_invalidates_source_changes(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    agent = Mock(model="test", extract=Mock(return_value={"commands": [annotation()]}))
    page = corpus.pages["acl.json"]
    output = tmp_path / "results"
    assert extract_page(page, corpus, agent, output)[1] is False
    assert extract_page(page, corpus, agent, output)[1] is True
    assert agent.extract.call_count == 1
    page.data["UsageGuidelines"] = "Additional documentation."
    assert extract_page(page, corpus, agent, output)[1] is False
    assert agent.extract.call_count == 2


def test_invalid_semantic_response_is_saved_before_validation(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = annotation()
    record["switch_to_view"] = "Unsubstantiated ACL target"
    response = {"commands": [record]}
    agent = Mock(model="test", extract=Mock(return_value=response))
    page = corpus.pages["acl.json"]
    output = tmp_path / "results"
    received, _ = extract_page(page, corpus, agent, output)
    assert read_json(output / "pages/acl.json")["response"] == response
    valid, errors = validate_responses(corpus, [page], {"acl.json": received})
    assert valid == {}
    assert errors["acl.json"].startswith("validation: ")
    assert read_json(output / "pages/acl.json")["response"] == response
    assert agent.extract.call_count == 1  # Validation never silently rewrites it.


def test_assembly_preserves_known_target_without_a_matching_group(tmp_path: Path):
    pattern = "acl  [ number ] <id>"
    corpus = corpus_page(tmp_path, [pattern])
    record = annotation()
    header = {"source": "documentation", "entry_view": "System view"}
    output = tmp_path / "results"
    report = assemble(
        corpus, list(corpus.pages.values()), {"acl.json": [record]}, {}, header, output
    )
    flat = read_json(output / "documentation_flat.json")
    grouped = read_json(output / "documentation_grouped.json")
    assert flat["commands"][0]["format"] == pattern
    assert flat["commands"][0]["switch_to_view"] == "ACL view"
    assert grouped["views"]["System view"][0]["switch_to_view"] == "ACL view"
    assert "ACL view" not in grouped["views"]  # No invented empty target group.
    assert report["transitions_unresolved"] == 0
    assert report["transitions_established"] == 1
    assert report["targets_without_groups"] == 1
    assert report["formats_complete"] == 1
    assert report["issues"][0]["issues"][0]["field"] == "view_membership"
    assert report["sources"][0]["file"] == "acl.json"


def test_unknown_transition_is_distinct_from_stay(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = annotation()
    record.update(
        switch_to_view=None,
        switch_evidence=None,
        issues=[{"field": "switch_to_view", "message": "Parameter-dependent."}],
    )
    output = tmp_path / "results"
    assemble(
        corpus,
        list(corpus.pages.values()),
        {"acl.json": [record]},
        {},
        {"entry_view": "System view"},
        output,
    )
    assert (
        read_json(output / "documentation_flat.json")["commands"][0]["switch_to_view"]
        is None
    )
    assert read_json(output / "documentation_grouped.json")["views"]["System view"][0][
        "switch_to_view"
    ] == {"status": "unresolved"}


def test_mock_replaces_parameters_only_and_keeps_repeat_and_set_syntax(tmp_path: Path):
    pattern = "vlan { <id> [ to <id> ] } &<1-10> [ all | reserved ]*"
    corpus = corpus_page(tmp_path, [pattern])
    converted, unknown = convert_format(corpus.pages["acl.json"], pattern)
    assert converted == (
        "vlan { INTEGER<2000-2999> [ to INTEGER<2000-2999> ] } &<1-10> "
        "[ all | reserved ]*"
    )
    assert unknown == []
    parser = CommandLineParser({"commands": [converted]})
    assert parser.parse("vlan 2001 to 2003 2010 reserved all").parsed
    assert not parser.parse("vlan 1999").parsed


def test_unknown_mock_type_is_reported_not_presented_as_extracted():
    assert mock_placeholder("Identifies an unspecified object.") == (
        None,
        True,
    )
    assert mock_placeholder("An IPv4 address in dotted decimal notation.") == (
        "X.X.X.X",
        False,
    )
    assert mock_placeholder("An integer ranging from -1 to 10.") == (
        "INTEGER<-1-10>",
        False,
    )


def test_portable_schema_and_type_table_match_the_implementation():
    schema_file = (
        Path(__file__).parents[1] / "scripts/corpus_pipeline/response.schema.json"
    )
    assert read_json(schema_file) == RESPONSE_SCHEMA
    assert set(PARAMETER_TYPES) - {"unknown"} == set(PLACEHOLDERS)
    assert set(PARAMETER_TYPES) - {"unknown"} <= {
        kind.type_id for kind in builtin_parameter_types()
    }


def test_source_rejects_duplicate_json_fields(tmp_path: Path):
    path = tmp_path / "input.json"
    path.write_text('{"CLIs": [], "CLIs": ["quit"]}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        read_json(path)


def test_display_filter_preserves_original_indices_on_mixed_page(tmp_path: Path):
    corpus = corpus_page(tmp_path, [" display acl <id>", "acl <id>", "DISPLAY acl all"])
    page = corpus.pages["acl.json"]
    assert page.included_formats == {1: "acl <id>"}
    assert [item["format_index"] for item in page_input(page, corpus)["formats"]] == [1]
    validate_response({"commands": [annotation(1)]}, page, corpus)
    with pytest.raises(ValueError, match="format_index"):
        validate_response({"commands": [annotation(0)]}, page, corpus)
    assert corpus.display_formats_skipped == 2
    assert corpus.display_pages_skipped == 0
    assert corpus.command_formats["acl number"] == {"acl <id>"}


def test_display_only_pages_are_not_selected_or_sent_to_agent(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["display [ invalid grammar"])
    assert corpus.pages == {}
    assert corpus.selected(30) == []
    assert corpus.display_pages_skipped == corpus.display_formats_skipped == 1


def test_display_filter_uses_keyword_boundary(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["display-name <id>"])
    assert corpus.pages["acl.json"].included_formats == {0: "display-name <id>"}


def test_schema_rejects_extra_fields_and_mixed_selectors(tmp_path: Path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = copy.deepcopy(annotation())
    record["creates"][0]["parameter_comb"] = ["id", "id"]
    with pytest.raises(ValidationError):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)
