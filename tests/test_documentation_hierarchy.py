"""Documentation hierarchy recovery retains source data and explicit uncertainty."""

from copy import deepcopy

import pytest

from scripts.corpus_pipeline.extraction import Codex
from scripts.corpus_pipeline.hierarchy.evidence import (
    EvidenceRequests,
    collect_evidence,
    validate_evidence,
)
from scripts.corpus_pipeline.hierarchy.input import AnnotatedCorpus, ViewNames
from scripts.corpus_pipeline.hierarchy.pipeline import recover_hierarchy
from scripts.corpus_pipeline.hierarchy.recovery import DocumentationHierarchy
from scripts.corpus_pipeline.source import Corpus, read_json, write_json
from vrp_parser_automaton import CommandLineParser


def sample(tmp_path, specifications):
    corpus_dir, input_dir = tmp_path / "corpus", tmp_path / "input"
    commands, sources, issues = [], [], []
    for i, (views, pattern, target) in enumerate(specifications):
        filename = f"page_{i}.json"
        write_json(
            corpus_dir / filename,
            {
                "PageTitle": pattern,
                "CLIs": [pattern],
                "ParentView": views,
                "FuncDef": "The command enters Child view.",
            },
        )
        commands.append(
            {
                "format": pattern,
                "parent_views": views,
                "description": "Original.",
                "switch_to_view": None if target == "?" else target,
                "parameter_types": [],
                "creates": [{"kind": "entity", "entity_type": "x"}],
                "requires": [],
            }
        )
        sources.append(
            {
                "file": filename,
                "command_start": i,
                "command_count": 1,
                "format_indices": [0],
            }
        )
        if target == "?":
            issues.append(
                {
                    "file": filename,
                    "format_index": 0,
                    "issues": [
                        {"field": "switch_to_view", "message": "Unknown destination."}
                    ],
                }
            )
    write_json(
        input_dir / "documentation_flat.json",
        {
            "schema_version": 1,
            "source": "documentation",
            "type": "flat",
            "vendor": "Example",
            "device": "Example OS",
            "model_type": "Test",
            "commands": commands,
        },
    )
    write_json(input_dir / "report.json", {"sources": sources, "issues": issues})
    return AnnotatedCorpus(input_dir, Corpus(corpus_dir))


def decision(index, kind, target=None, members=()):
    return (
        {str(index): {"commands": [{"index": index}]}},
        {
            str(index): {
                "kind": kind,
                "target": target,
                "members": list(members),
                "explanation": "Documentary evidence.",
            }
        },
    )


def test_exact_names_win_and_normalized_collisions_remain_ambiguous():
    names = ViewNames(["Child view", "CHILD VIEW"])
    assert names.find("Child view") == (["Child view"], "exact")
    assert names.find(" child  view ") == (["Child view", "CHILD VIEW"], "normalized")


def test_grouping_preserves_records_and_normalizes_only_unique_targets(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter", " child   VIEW "),
            (["Child view", "Other"], "leaf", None),
        ],
    )
    original = deepcopy(source.document)
    document, report = DocumentationHierarchy(source, "Root", set()).recover({}, {}, {})
    assert set(document["views"]) == {"Root", "Child view", "Other"}
    assert document["views"]["Root"][0]["switch_to_view"] == "Child view"
    assert document["views"]["Child view"][0] == source.commands[1]
    assert document["views"]["Other"][0] == source.commands[1]
    assert source.document == original
    assert report["transitions"][0]["method"] == "normalized"
    assert report["reachability"]["unreachable_views"] == ["Other"]


def test_unknown_missing_and_ambiguous_are_distinct_and_stay_is_preserved(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "unknown", "?"),
            (["Root"], "missing", "Absent"),
            (["Root"], "ambiguous", " child "),
            (["Root"], "stay", None),
            (["CHILD"], "a", None),
            (["Child"], "b", None),
        ],
    )
    document, report = DocumentationHierarchy(source, "Root", set()).recover({}, {}, {})
    assert [r["status"] for r in report["transitions"]] == [
        "unknown",
        "missing",
        "ambiguous",
    ]
    assert "Absent" not in document["views"]
    assert all(
        c["switch_to_view"] == {"status": "unresolved"}
        for c in document["views"]["Root"][:3]
    )
    assert document["views"]["Root"][3]["switch_to_view"] is None
    assert report["transitions"][1]["requested_target"] == "Absent"


def test_documented_group_keeps_members_and_reports_partial_coverage(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter", "Family view"),
            (["First"], "first", None),
            (["Second"], "second", None),
        ],
    )
    requests, answers = decision(
        0, "group", "Family view", ["First", "Second", "Third"]
    )
    document, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, answers, {}
    )
    assert set(document["views"]) == {"Root", "First", "Second", "Family view"}
    assert [c["format"] for c in document["views"]["Family view"]] == [
        "first",
        "second",
    ]
    assert report["aggregates"]["Family view"]["members_without_commands"] == ["Third"]
    assert report["aggregates"]["Family view"]["exhaustive"] is False
    assert report["reachability"]["entered_views"] == ["Family view", "Root"]
    assert report["reachability"]["members_covered_by_aggregates"] == [
        "First",
        "Second",
    ]
    assert source.commands[0]["switch_to_view"] == "Family view"


def test_documentary_alias_resolves_one_transition_without_renaming_groups(tmp_path):
    source = sample(
        tmp_path, [(["Root"], "enter", "Other label"), (["Child view"], "leaf", None)]
    )
    requests, answers = decision(0, "alias", "Child view")
    document, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, answers, {}
    )
    assert list(document["views"]) == ["Root", "Child view"]
    assert report["transitions"][0]["requested_target"] == "Other label"
    assert report["transitions"][0]["target"] == "Child view"


def test_group_evidence_cannot_overwrite_a_normalized_name_collision(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter", " child "),
            (["CHILD"], "first", None),
            (["Child"], "second", None),
        ],
    )
    requests, answers = decision(0, "group", " child ", ["CHILD", "Child"])
    document, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, answers, {}
    )
    assert set(document["views"]) == {"Root", "CHILD", "Child"}
    assert report["transitions"][0]["status"] == "ambiguous"
    assert report["aggregates"] == {}


def test_common_commands_and_exits_are_separate_from_child_reachability(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter", "Child view"),
            (["Child view"], "leaf", None),
            (["All views"], "quit", "?"),
            (["All views"], "return", "User view"),
            (["User view"], "begin", "Root"),
        ],
    )
    requests, answers = decision(2, "exit_parent")
    r, a = decision(3, "exit_view", "User view")
    requests.update(r)
    answers.update(a)
    document, report = DocumentationHierarchy(source, "Root", {"All views"}).recover(
        requests, answers, {}
    )
    assert document["shared_views"] == ["All views"]
    parser = CommandLineParser(document)
    for view in ("Root", "Child view", "User view"):
        assert parser.parse("quit", view=view).primary_match is not None
        assert parser.parse("return", view=view).primary_match is not None
    graph = report["reachability"]
    assert "All views" not in graph["entered_views"]
    assert "User view" not in graph["entered_views"]
    assert "User view" in graph["reachable_with_fixed_exits"]
    assert report["summary"]["exit_parent"] == 1
    assert report["summary"]["exit_view"] == 1
    assert report["summary"]["shared_copies"] == 6


def test_ambiguous_evidence_and_failures_cannot_silently_choose_a_candidate(tmp_path):
    source = sample(
        tmp_path, [(["Root"], "enter", "Generic"), (["Child view"], "leaf", None)]
    )
    requests, answers = decision(0, "ambiguous", "Generic", ["Child view"])
    document, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, answers, {}
    )
    assert document["views"]["Root"][0]["switch_to_view"] == {"status": "unresolved"}
    assert report["transitions"][0]["candidates"] == ["Child view"]
    _, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, {}, {"0": "Model call failed"}
    )
    assert report["transitions"][0]["status"] == "missing"
    assert report["summary"]["evidence_errors"] == 1


def evidence_fixture():
    return {
        "requested_target": "Generic view",
        "candidate_views": ["Child view"],
        "pages": [
            {
                "file": "source.json",
                "ParentView": ["Root"],
                "FuncDef": "Generic view is also called Child view.",
            }
        ],
    }, {
        "kind": "alias",
        "target": "Child view",
        "members": [],
        "evidence": [
            {
                "file": "source.json",
                "field": "FuncDef",
                "quote": "Generic view is also called Child view.",
            }
        ],
        "explanation": "The source explicitly identifies the alias.",
    }


def test_quotes_require_original_text_and_membership_alone_is_not_proof():
    request, answer = evidence_fixture()
    assert validate_evidence(answer, request) == answer
    answer["evidence"][0]["quote"] = "An invented sentence."
    with pytest.raises(ValueError, match="quotation"):
        validate_evidence(answer, request)
    answer["evidence"] = [
        {"file": "source.json", "field": "ParentView", "quote": "Root"}
    ]
    with pytest.raises(ValueError, match="prose"):
        validate_evidence(answer, request)


def test_alias_evidence_can_combine_entry_prose_and_a_child_page_scope():
    request, answer = evidence_fixture()
    request["pages"][0]["FuncDef"] = "The command enters the child configuration mode."
    request["pages"].append({"file": "child.json", "ParentView": ["Child view"]})
    answer["evidence"] = [
        {
            "file": "source.json",
            "field": "FuncDef",
            "quote": request["pages"][0]["FuncDef"],
        },
        {"file": "child.json", "field": "ParentView", "quote": "Child view"},
    ]
    assert validate_evidence(answer, request) == answer
    answer["evidence"][1]["quote"] = "Child"
    with pytest.raises(ValueError, match="complete ParentView"):
        validate_evidence(answer, request)


def test_related_examples_are_retrieved_before_short_unrelated_pages(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter service", "IPv4 service view"),
            (["Service view"], "setting", None),
            (["Service view"], "filter <id>", None),
        ],
    )
    page = source.corpus.pages["page_2.json"]
    page.data.update(
        Examples=[["[Device] enter service", "[Device-service] filter 1"]],
        UsageGuidelines="A longer description with useful source links. " * 10,
        related_topics=[{"target_files": ["page_0.json"]}],
    )
    request = EvidenceRequests(source, set()).build()["0"]
    pages = request["pages"]
    assert pages[1]["file"] == "page_2.json"
    assert pages[1]["Examples"] == page.data["Examples"]
    assert pages[1]["related_topics"] == page.data["related_topics"]
    answer = {
        "kind": "alias",
        "target": "Service view",
        "members": [],
        "explanation": "The entry is followed by a command in Service view.",
        "evidence": [
            {"file": "page_2.json", "field": "Examples", "quote": line}
            for line in page.data["Examples"][0]
        ],
    }
    assert validate_evidence(answer, request) == answer
    answer["evidence"][0]["quote"] = "\n".join(page.data["Examples"][0])
    with pytest.raises(ValueError, match="quotation"):
        validate_evidence(answer, request)


def test_aggregate_conflicting_transitions_keep_both_source_records(tmp_path):
    source = sample(
        tmp_path,
        [
            (["Root"], "enter", "Family"),
            (["First"], "nested", "First"),
            (["Second"], "nested", "Second"),
        ],
    )
    requests, answers = decision(0, "group", "Family", ["First", "Second"])
    document, report = DocumentationHierarchy(source, "Root", set()).recover(
        requests, answers, {}
    )
    assert report["origins"]["Family"] == [1, 2]
    assert len(document["views"]["Family"]) == 2
    assert report["conflicting_formats"] == [
        {"view": "Family", "format": "nested", "commands": [1, 2]}
    ]


def test_rejected_model_answers_are_saved_and_reused_before_validation(
    tmp_path, monkeypatch
):
    request, answer = evidence_fixture()
    answer["evidence"][0]["quote"] = "Not in the source."
    calls = []
    monkeypatch.setattr(
        Codex, "extract", lambda self, data: calls.append(data) or answer
    )
    agent = Codex("unused", "test")
    for _ in range(2):
        valid, errors = collect_evidence({"0": request}, agent, tmp_path, 1)
        assert valid == {} and "0" in errors
    assert len(calls) == 1
    assert read_json(tmp_path / "answers/0.json")["response"] == answer
    request["requested_target"] = "Changed view"
    collect_evidence({"0": request}, agent, tmp_path, 1)
    assert len(list((tmp_path / "history/0").glob("*.json"))) == 1


def test_source_mismatch_is_rejected(tmp_path):
    source = sample(tmp_path, [(["Root"], "one", None)])
    changed = deepcopy(source.document)
    changed["commands"][0]["format"] = "different"
    write_json(tmp_path / "input/documentation_flat.json", changed)
    with pytest.raises(ValueError, match="disagree"):
        AnnotatedCorpus(tmp_path / "input", source.corpus)


def test_pipeline_writes_catalog_and_report_without_touching_input(
    tmp_path, monkeypatch
):
    source = sample(
        tmp_path, [(["Root"], "enter", "Child view"), (["Child view"], "leaf", None)]
    )
    before = deepcopy(source.document)
    monkeypatch.setattr(
        Codex, "extract", lambda *_: pytest.fail("No evidence call needed")
    )
    report = recover_hierarchy(
        tmp_path / "input",
        tmp_path / "corpus",
        tmp_path / "out",
        Codex("unused", "test"),
        entry_view="Root",
    )
    assert report["summary"]["resolved"] == 1
    assert read_json(tmp_path / "out/documentation_grouped.json")["type"] == "grouped"
    assert read_json(tmp_path / "input/documentation_flat.json") == before
    with pytest.raises(ValueError, match="separate output"):
        recover_hierarchy(
            tmp_path / "input",
            tmp_path / "corpus",
            tmp_path / "input",
            Codex("unused", "test"),
            entry_view="Root",
        )
