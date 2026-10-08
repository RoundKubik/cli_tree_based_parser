"""Experiments expose unsupported annotations without weakening matcher types."""

from copy import deepcopy

import pytest
from test_format_matcher_catalogs import catalog, command, document

from scripts.corpus_pipeline.experiment import compatible_documentation
from scripts.corpus_pipeline.hierarchy_diagnostics import hierarchy_diagnostics
from vrp_format_matcher import FormatMatcher


def test_compatible_selection_retains_original_locations_and_empty_target_groups():
    document = {
        "type": "grouped",
        "views": {
            "Root": [
                {"format": "enter", "parameter_types": [], "switch_to_view": "Child"},
                {"format": "untyped <id>", "parameter_types": []},
                {
                    "format": "good <id>",
                    "parameter_types": [
                        {"parameter_name": "id", "parameter_type": "integer"}
                    ],
                },
            ],
            "Child": [
                {
                    "format": "description <text>",
                    "parameter_types": [
                        {"parameter_name": "text", "parameter_type": "unsupported"}
                    ],
                }
            ],
        },
    }
    original = deepcopy(document)
    selected, audit = compatible_documentation(document)
    assert document == original
    assert audit["origins"] == {"Root": [0, 1, 2], "Child": []}
    assert [(e["view"], e["index"]) for e in audit["excluded"]] == [
        ("Child", 0),
    ]
    assert selected["views"]["Child"] == []
    assert selected["views"]["Root"][0]["switch_to_view"] == "Child"


@pytest.mark.parametrize(
    "annotations",
    [None, [], [{"parameter_name": "id", "parameter_type": "unknown"}]],
)
def test_hierarchy_report_distinguishes_ambiguity_partial_coverage_and_unknown_types(
    annotations,
):
    device = catalog(
        "device",
        views={
            "r": [command("enter-a"), command("enter-b"), command("enter-c")],
            "one": [command("rule INTEGER<1-9>")],
            "two": [command("rule INTEGER<1-9>")],
            "other": [command("value X.X.X.X")],
        },
    )
    record = {"format": "value <id>"}
    if annotations is not None:
        record["parameter_types"] = annotations
    docs = catalog(
        "documentation",
        views={
            "R": [
                {**document(f"enter-{letter.lower()}"), "switch_to_view": letter}
                for letter in "ABC"
            ],
            "A": [document("rule <id>")],
            "B": [document("rule <id>"), document("absent")],
            "C": [record],
        },
    )
    report = hierarchy_diagnostics(FormatMatcher().prepare_catalogs(device, docs), docs)
    assert report["target_statuses"] == {
        "resolved": 1,
        "ambiguous": 1,
        "partial": 1,
        "unknown": 1,
    }
    assert report["targets"]["A"]["covered"] == ["one", "two"]
    assert report["targets"]["B"]["partial"] == ["one", "two"]
    assert report["targets"]["C"]["unknown"] == ["other"]
    assert report["unknown_parameter_types"] == [
        {
            "format": "value <id>",
            "parameters": ["id"],
            "sources": [{"view": "C", "index": 0}],
        }
    ]
