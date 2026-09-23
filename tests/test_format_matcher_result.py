"""Compact JSON preserves bindings and every annotated scope transition."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits


def restored_pair(data, pattern_id, pair):
    """Test-only expansion verifies lossless references against the Python API."""
    device = data["devices"][pattern_id]
    document = data["documents"][pair["document_id"]]

    def tag(side, reference):
        if reference is None:
            return None
        source = device if side == "device" else document
        return {
            **source["slots"][reference["slot_id"]],
            **reference,
        }

    machine = None
    if "automaton_id" in pair:
        scope = data["automata"][pair["automaton_id"]]
        machine = {
            **scope,
            "edges": [
                [
                    {
                        "target": arc["target"],
                        "label": arc["label"],
                        "document": tag("document", arc.get("document")),
                        "device": tag("device", arc.get("device")),
                    }
                    for arc in edges
                ]
                for edges in scope["edges"]
            ],
        }
    return {
        "document_id": pair["document_id"],
        "document_format": document["document_format"],
        "pattern_id": pattern_id,
        "device_format": device["device_format"],
        "status": pair["status"],
        "stage": pair["stage"],
        "binding_mode": pair["binding_mode"],
        "structurally_identical": pair["stage"] == "exact",
        "bindings": [
            {
                side: tag(side, {"slot_id": binding[side], "iterations": []})
                for side in ("device", "document")
            }
            for binding in pair["bindings"]
        ],
        "automaton": machine,
    }


@pytest.mark.parametrize(
    "device,document,limits",
    [
        ("c { <x> [ to <y> ] } &<1-3>", "c { <a> [ to <b> ] } &<1-3>", None),
        ("c { b <y> | a <x> } *", "c { a <a> | b <b> } *", None),
        ("c [ a <x> | b <y> ] *", "c { a <a> | b <b> }", None),
        ("c <x> [ to <y> ]", "c { <single> | <first> to <last> }", None),
        (
            "c { { <x> } &<1-2> end } &<1-2> device",
            "c { { <a> } &<1-2> end } &<1-2> doc",
            None,
        ),
        ("c { a | b }", "c { b | d }", None),
        ("c [ <x> ]", "c <a>", MappingLimits(analysis_steps=1)),
        ("undo vlan <x>", "undo interface <a>", None),
    ],
)
def test_json_references_restore_all_ids_bindings_and_scope_arcs(
    device, document, limits
):
    result = FormatMatcher(limits).compile_formats(
        [device, device],
        [{"id": "first", "format": document}, {"id": "copy", "format": document}],
        target_syntax="document",
    )
    data = json.loads(json.dumps(result.to_dict()))
    for pattern_id, match in result.devices.items():
        saved = data["devices"][pattern_id]
        assert saved["status"] == match.status
        assert saved["stage"] == match.stage
        assert len(saved["mappings"]) == len(match.mappings)
        for original, pair in zip(match.mappings, saved["mappings"], strict=True):
            assert restored_pair(data, pattern_id, pair) == json.loads(
                json.dumps(asdict(original))
            )
    assert result.to_dict() == data


def test_equal_scopes_share_graphs_but_not_document_parameter_names():
    result = FormatMatcher().compile_formats(
        ["c <x> device"],
        [{"id": str(i), "format": f"c <name{i}> documentation{i}"} for i in range(100)],
        target_syntax="document",
    )
    data = result.to_dict()
    assert len(data["automata"]) == 1
    assert len(data["documents"]) == 100
    assert len(next(iter(data["devices"].values()))["slots"]) == 1
    for pattern_id, match in data["devices"].items():
        assert len(match["mappings"]) == 100
        for pair in match["mappings"]:
            restored = restored_pair(data, pattern_id, pair)
            assert restored["bindings"][0]["document"]["name"] == (
                "name" + pair["document_id"]
            )
    assert len(json.dumps(data)) < len(json.dumps(asdict(result))) / 2
