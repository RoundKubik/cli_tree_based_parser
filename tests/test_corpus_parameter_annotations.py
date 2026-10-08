"""Repair type omissions without rewriting formats or inventing scalar types."""

from copy import deepcopy

import pytest
from test_corpus_pipeline import annotation, corpus_page

from scripts.corpus_pipeline.contract import validate_response
from scripts.corpus_pipeline.mock_device import convert_format, mock_placeholder
from scripts.corpus_pipeline.parameter_annotations import complete_types, repair_types
from scripts.corpus_pipeline.source import read_json, write_json


def test_known_annotations_survive_and_only_explicit_strings_fill_gaps(tmp_path):
    pattern = "x <known> <label> <as-number> <missing> <label>"
    page = corpus_page(tmp_path, [pattern]).pages["acl.json"]
    page.data["ParaDef"] = [
        {"Parameters": "known", "Info": "The value is a string."},
        {
            "Parameters": "label",
            "Info": 'The value is a string. Spaces require "quotes".',
        },
        {"Parameters": "as-number", "Info": "An integer or dotted notation x.y."},
    ]
    original = [{"parameter_name": "known", "parameter_type": "integer"}]
    result, changes = complete_types(page, pattern, original)
    assert original == [{"parameter_name": "known", "parameter_type": "integer"}]
    assert {p["parameter_name"]: p["parameter_type"] for p in result} == {
        "known": "integer",
        "label": "string",
        "as-number": "unknown",
        "missing": "unknown",
    }
    assert len(result) == 4
    assert changes[0]["evidence"] == page.data["ParaDef"][1]["Info"]
    assert complete_types(page, pattern, result) == (result, [])


@pytest.mark.parametrize("names", [["id", "id"], ["extra"]])
def test_completion_rejects_invalid_names_instead_of_dropping_them(tmp_path, names):
    page = corpus_page(tmp_path, ["x <id>"]).pages["acl.json"]
    types = [{"parameter_name": name, "parameter_type": "string"} for name in names]
    with pytest.raises(ValueError):
        complete_types(page, page.formats[0], types)


def test_explicit_unknown_requires_an_extraction_issue(tmp_path):
    corpus = corpus_page(tmp_path, ["acl <id>"])
    record = annotation()
    record["parameter_types"][0]["parameter_type"] = "unknown"
    with pytest.raises(ValueError, match="Missing parameter.*id"):
        validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)
    record["issues"] = [
        {"field": "parameter_types", "message": "Unsupported representation."}
    ]
    validate_response({"commands": [record]}, corpus.pages["acl.json"], corpus)


def test_repair_keeps_formats_semantics_view_positions_and_raw_input(tmp_path):
    corpus = corpus_page(tmp_path, ["x <id>"])
    record = {
        "format": "x <id>",
        "parameter_types": [],
        "creates": [{"custom": 1}],
        "switch_to_view": None,
    }
    flat = {"commands": [record]}
    grouped = {"views": {"A": [record], "B": [record]}}
    write_json(tmp_path / "source/documentation_flat.json", flat)
    write_json(
        tmp_path / "source/report.json",
        {"sources": [{"file": "acl.json", "command_start": 0, "format_indices": [0]}]},
    )
    write_json(tmp_path / "hierarchy/documentation_grouped.json", grouped)
    write_json(
        tmp_path / "hierarchy/hierarchy_report.json",
        {"origins": {"A": [0], "B": [0]}, "shared_views": ["B"]},
    )
    report = repair_types(
        tmp_path / "source",
        tmp_path / "hierarchy",
        corpus.directory,
        tmp_path / "fixed",
    )
    assert report["formats_with_unknown_types"] == 1
    assert read_json(tmp_path / "source/documentation_flat.json") == flat
    assert read_json(tmp_path / "hierarchy/documentation_grouped.json") == grouped
    repaired = read_json(tmp_path / "fixed/documentation_grouped.json")
    assert list(repaired["views"]) == ["A", "B"]
    expected = {
        **deepcopy(record),
        "parameter_types": [{"parameter_name": "id", "parameter_type": "unknown"}],
    }
    assert repaired == {
        "views": {"A": [expected], "B": [expected]},
        "shared_views": ["B"],
    }


def test_mock_ipv6_prefix_representation_is_not_a_scalar_address_or_length():
    assert mock_placeholder(
        "Specifies the IPv6 address and prefix length of an interface. "
        "IPv6 address/IPv6 address prefix length."
    ) == ("X:X::X:X/M", False)
    assert mock_placeholder(
        "IPv6 prefix length. The value is an integer ranging from 0 to 128."
    ) == ("INTEGER<0-128>", False)
    assert mock_placeholder(
        "Specifies the IPv6 destination address with a prefix. "
        "The value is a string of case-sensitive characters."
    ) == ("X:X::X:X/M", False)


def test_mock_unknown_composite_preserves_placeholder_and_reports_it(tmp_path):
    page = corpus_page(tmp_path, ["bgp <as-number>"]).pages["acl.json"]
    page.data["ParaDef"] = [
        {
            "Parameters": "as-number",
            "Info": "An AS number is an integer ranging from 1 to 4294967295, "
            "or dotted notation x.y.",
        }
    ]
    assert convert_format(page, page.formats[0]) == ("bgp <as-number>", ["as-number"])
