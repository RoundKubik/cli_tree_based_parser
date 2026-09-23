"""Prefix pruning must preserve every source-slot correspondence."""

from __future__ import annotations

import pytest

from vrp_format_matcher import FormatMatcher, MappingLimits
from vrp_format_matcher.documents.catalog import DocumentFormat
from vrp_format_matcher.preparation import candidates
from vrp_format_matcher.preparation.candidates import (
    ParameterPrefixCover,
    PrefixCandidateIndex,
)
from vrp_format_matcher.preparation.pairs import CompiledPattern, PairPreparation
from vrp_format_matcher.preparation.sources import TargetFormats


@pytest.mark.parametrize(
    "prefix", ["undo ", "one two three four five six seven eight nine ten "]
)
def test_divergent_keyword_catalog_does_not_build_prefix_products(prefix, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("divergent keywords must be rejected by the trie")

    monkeypatch.setattr(PairPreparation, "prefix", forbidden)
    result = FormatMatcher().compile_formats(
        [f"{prefix}device{i} <value>" for i in range(200)],
        [{"format": f"{prefix}documentation{i} <value>"} for i in range(200)],
        target_syntax="document",
    )
    assert result.pairs == ()
    assert all(d.status == "unmatched" for d in result.devices.values())


def test_broad_cover_does_not_export_keyword_only_prefix_graphs():
    common = "c { " + " | ".join(f"k{i}" for i in range(65)) + " } "
    result = FormatMatcher().compile_formats(
        [common + "device <value>"],
        [{"format": common + "documentation <value>"}],
        target_syntax="document",
    )
    assert result.pairs == ()
    assert next(iter(result.devices.values())).status == "unmatched"


@pytest.mark.parametrize("depth,width", [(8, 64), (2, 2)])
def test_prefix_index_retains_every_pair_with_bindings(depth, width, monkeypatch):
    cover = ParameterPrefixCover(depth=depth, width=width)
    monkeypatch.setattr(candidates, "ParameterPrefixCover", lambda: cover)
    formats = [
        "undo a <x>",
        "undo b <y>",
        "undo a",
        "c <x> left",
        "c <y> right",
        "c [ <x> ] next <y>",
        "c next <z>",
        "c { a <x> | b <y> }",
        "c { b <x> | a <y> }",
        "c [ a <x> | b <y> ] *",
        "c { a <x> | b <y> } *",
        "c { { a <x> | b <y> } * }",
        "c [ a | b ] * <x>",
        "c { a | b } * <x>",
        "c a b <x>",
        "c b a <x>",
        "c [ a ] <x>",
        "c [ <x> | a ] * <y>",
        "c { <x> | a } * <y>",
        "c { <x> [ to <y> ] } &<1-3>",
        "c <x> to <y>",
        "c { { <x> } &<1-2> | all }",
        "c all",
        "c [ a [ b <x> ] ] <y>",
        "c a b <x> <y>",
        "c [ a ] [ b ] [ d ] <x>",
        "c d b <y>",
        "c { a | b } [ <x> ] <y>",
        "c a <x> <y>",
        "c <x> <x>",
        "c [ <x> ]",
        "c <x>",
        "c a b d e f g h i <x>",
        "c a b d e f g h i <y> other",
        "c a b d e f g h j <x>",
        "c { " + " | ".join(f"branch{i} <x>" for i in range(66)) + " }",
        "c branch65 <y>",
        "c branch0 <y>",
        "c branch99 <y>",
    ]
    sources = TargetFormats(formats, "document").patterns()
    limits = MappingLimits()
    documents = [
        CompiledPattern(s.ast, limits.automaton_states, document=True) for s in sources
    ]
    devices = [
        CompiledPattern(s.ast, limits.automaton_states, document=False) for s in sources
    ]
    index = PrefixCandidateIndex(sources)
    with_bindings = 0
    for device, device_program in zip(sources, devices, strict=True):
        retained = {s.pattern_id for s in index.candidates(device.ast)}
        for document, document_program in zip(sources, documents, strict=True):
            pair = PairPreparation(
                DocumentFormat(document.pattern_id, document.original, document.ast),
                device,
                document_program,
                device_program,
                limits,
            ).prefix()
            assert pair.status != "unknown"
            if pair.bindings:
                with_bindings += 1
                assert document.pattern_id in retained, (
                    device.original,
                    document.original,
                    pair.bindings,
                )
    assert with_bindings > 400


@pytest.mark.parametrize("suffix", ["", " doc"])
def test_duplicate_sources_keep_ids_and_slots_without_repeating_work(
    suffix, monkeypatch
):
    device = "c { a <first> | b <second> } * device"
    document = "c { a <first> | b <second> } *" + (suffix or " device")
    calls = []
    method = "prefix" if suffix else "prepared"
    original = getattr(PairPreparation, method)

    def observed(pair):
        calls.append((pair.document.document_id, pair.device.pattern_id))
        return original(pair)

    monkeypatch.setattr(PairPreparation, method, observed)
    result = FormatMatcher().compile_formats(
        [device, device],
        [{"id": f"doc:{i}", "format": document} for i in range(3)],
        target_syntax="document",
    )
    assert len(calls) == 1
    assert len(result.pairs) == 6
    expected = {
        (f"p:{document.index('<first>')}", f"p:{device.index('<first>')}"),
        (f"p:{document.index('<second>')}", f"p:{device.index('<second>')}"),
    }
    for pattern_id, match in result.devices.items():
        assert [p.document_id for p in match.mappings] == ["doc:0", "doc:1", "doc:2"]
        for pair in match.mappings:
            assert pair.pattern_id == pattern_id
            assert {(b.document.slot_id, b.device.slot_id) for b in pair.bindings} == (
                expected
            )
