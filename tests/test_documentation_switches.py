"""Standalone recovery uses source examples, opaque blocks and scoped syntax."""

import json
from copy import deepcopy

import pytest

from vrp_documentation_hierarchy import recover_hierarchy
from vrp_documentation_hierarchy.__main__ import main


def fixture(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = {
        "schema_version": 1,
        "source": "documentation",
        "type": "grouped",
        "vendor": "Huawei",
        "device": "Huawei VRP",
        "model_type": "Test",
        "entry_view": "System view",
        "views": {"System view": []},
    }

    def add(name, views, pattern, examples=(), **fields):
        file = name + ".json"
        (corpus / file).write_text(json.dumps({
            "CLIs": [pattern], "ParentView": views,
            "Examples": list(examples),
        }))
        row = {
            "format": pattern,
            "documentation_source": {"file": file, "format_index": 0},
            **fields,
        }
        for view in views:
            document["views"].setdefault(view, []).append(deepcopy(row))
        return row

    return corpus, document, add


def command(document, view, file):
    return next(row for row in document["views"][view]
                if row["documentation_source"]["file"] == file + ".json")


@pytest.mark.parametrize("invocation", [
    "interface 100GE 1/0/1", "interface 100GE1/0/1", "interface Vlanif1",
])
def test_interface_targets_are_sets_and_ip_pool_stays_separate(tmp_path, invocation):
    corpus, document, add = fixture(tmp_path)
    add("interface", ["System view"],
        "interface { <interface-name> | <interface-type> <interface-number> }")
    add("template_interface", ["Command template view"], "interface <name>")
    add("acl", ["System view"], "acl ip-pool <name>")
    ip = "ip address <address> <mask>"
    add("ip_interface", ["100GE interface view", "VLANIF interface view"], ip, [[
        "<HUAWEI> system-view", f"[~HUAWEI] {invocation}",
        "[~HUAWEI-interface] undo portswitch",
        "[*HUAWEI-interface] ip address 10.0.0.1 24",
    ]])
    add("ip_pool", ["ACL address pool view"], ip, [[
        "<HUAWEI> system-view", "[~HUAWEI] acl ip-pool test",
        "[*HUAWEI-acl-pool-test] ip address 10.0.0.0 24",
    ]])
    original = deepcopy(document)
    result = recover_hierarchy(document, corpus)
    assert document == original
    assert command(result.catalog, "System view", "interface")["switch_to_view"] == [
        "100GE interface view", "VLANIF interface view",
    ]
    assert command(result.catalog, "System view", "acl")["switch_to_view"] == [
        "ACL address pool view",
    ]
    assert "switch_to_view" not in command(
        result.catalog, "Command template view", "template_interface"
    )
    assert result.report["summary"]["observations"] == 2
    assert result.report["unresolved"] == []
    again = recover_hierarchy(result.catalog, corpus)
    assert again.catalog == result.catalog
    assert again.report["changes"] == []


def test_nested_switches_are_retried_and_quit_restores_the_source(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("peer", ["BGP VPN view"], "peer <ip> as-number <asn>", [[
        "<HUAWEI> system-view",
        "[~HUAWEI] ip vpn-instance unrelated",
        "[*HUAWEI-vpn-instance-unrelated] quit",
        "[*HUAWEI] bgp 100",
        "[*HUAWEI-bgp] router-id 1.1.1.1",
        "[*HUAWEI-bgp] vpn-instance blue",
        "[*HUAWEI-bgp-blue] peer 10.0.0.1 as-number 100",
    ]])
    add("vpn", ["BGP view"], "vpn-instance <name>", [[
        "<HUAWEI> system-view", "[~HUAWEI] bgp 100",
        "[*HUAWEI-bgp] vpn-instance blue",
    ]])
    add("bgp", ["System view"], "bgp <asn>")
    add("ip_vpn", ["System view"], "ip vpn-instance <name>")
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "bgp")["switch_to_view"] == [
        "BGP view",
    ]
    assert command(result.catalog, "BGP view", "vpn")["switch_to_view"] == [
        "BGP VPN view",
    ]
    assert "switch_to_view" not in command(result.catalog, "System view", "ip_vpn")
    assert result.report["summary"]["passes"] >= 3
    assert result.report["unresolved"] == []


def test_all_syntax_matches_are_kept_instead_of_preferring_literal(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("generic", ["System view"], "open <name>")
    add("literal", ["System view"], "open foo")
    add("child", ["Child"], "child", [[
        "<HUAWEI> system-view", "[~HUAWEI] open foo", "[*HUAWEI-foo] child",
    ]])
    result = recover_hierarchy(document, corpus)
    for file in ("generic", "literal"):
        assert command(result.catalog, "System view", file)["switch_to_view"] == [
            "Child",
        ]
    assert result.report["unresolved"] == []
    assert result.report["summary"]["multiple_parent_observations"] == 1
    assert len(result.report["evidence"]) == 2
    assert all(e["matched_parent_formats"] == 2 for e in result.report["evidence"])


def test_global_child_does_not_supply_a_target(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    add("global", ["All views"], "common", [[
        "<HUAWEI> system-view", "[~HUAWEI] enter x", "[*HUAWEI-x] common",
    ]])
    document["global_view"] = "All views"
    result = recover_hierarchy(document, corpus)
    assert result.catalog == document
    assert result.report["summary"]["observations"] == 0


def test_unknown_start_is_not_assumed_to_be_system(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    add("child", ["Child"], "child", [[
        "[~HUAWEI-bgp] enter x", "[*HUAWEI-bgp-x] child",
    ]])
    result = recover_hierarchy(document, corpus)
    assert result.catalog == document
    assert result.report["unresolved"][0]["reason"] == "source_context_unknown"


def test_inconsistent_prompt_after_quit_is_reported_without_false_parent(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    add("quit", ["All views"], "quit")
    add("child", ["Child"], "child", [[
        "<HUAWEI> system-view", "[~HUAWEI] enter first",
        "[*HUAWEI-first] quit", "[*HUAWEI-first] enter second",
        "[*HUAWEI-second] child",
    ]])
    result = recover_hierarchy(document, corpus)
    assert result.catalog == document
    assert result.report["structure_issues"][0]["reason"] == (
        "unexpected_prompt_after_exit"
    )
    assert result.report["unresolved"] == []
    assert result.report["skipped_examples"][0]["example_index"] == 0


def test_return_reentry_and_sysname_do_not_create_extra_blocks(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    add("sysname", ["System view"], "sysname <name>")
    add("child", ["Child"], "child", [[
        "<HUAWEI> system-view", "[~HUAWEI] enter first",
        "[*HUAWEI-first] return", "<HUAWEI> system-view",
        "[~HUAWEI] sysname Router-1", "[*Router-1] enter second",
        "[*Router-1-second] child",
    ]])
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "enter")["switch_to_view"] == [
        "Child",
    ]
    assert "switch_to_view" not in command(result.catalog, "System view", "sysname")
    assert not result.report["unresolved"]


def test_indented_examples_and_existing_target_union(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>", switch_to_view=["First"])
    document["views"]["First"] = []
    add("child", ["Second"], "child", [["enter x", " child"]])
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "enter")["switch_to_view"] == [
        "First", "Second",
    ]


def test_legacy_targets_are_normalized_and_unknown_names_are_reported(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("known", ["System view"], "known", switch_to_view="Child")
    add("unknown", ["System view"], "unknown", switch_to_view="Missing")
    add("child", ["Child"], "child")
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "known")["switch_to_view"] == [
        "Child",
    ]
    assert command(result.catalog, "System view", "unknown")["switch_to_view"] == {
        "status": "unresolved",
    }
    assert result.report["seed_issues"][0]["target"] == "Missing"


@pytest.mark.parametrize("bad_ref", [
    None, {},
    {"file": "enter.json", "format_index": "0"},
    {"file": "enter.json", "format_index": True},
    {"file": "enter.json", "format_index": -1},
    {"file": "enter.json", "format_index": 2},
])
def test_invalid_source_links_fail_before_recovery(tmp_path, bad_ref):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    document["views"]["System view"][0]["documentation_source"] = bad_ref
    with pytest.raises(ValueError):
        recover_hierarchy(document, corpus)


def test_source_format_must_equal_the_referenced_cli(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    document["views"]["System view"][0]["format"] = "different"
    with pytest.raises(ValueError, match="Source format differs"):
        recover_hierarchy(document, corpus)


def test_cli_writes_catalog_and_evidence_without_editing_input(tmp_path, capsys):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter <name>")
    add("child", ["Child"], "child", [["enter x", " child"]])
    input_path, output_path = tmp_path / "input.json", tmp_path / "output.json"
    input_path.write_text(json.dumps(document))
    original = input_path.read_bytes()
    assert main([
        "--catalog", str(input_path), "--corpus", str(corpus),
        "--output", str(output_path),
    ]) == 0
    assert input_path.read_bytes() == original
    output = json.loads(output_path.read_text())
    report = json.loads(output_path.with_suffix(".report.json").read_text())
    assert command(output, "System view", "enter")["switch_to_view"] == ["Child"]
    assert report["evidence"][0]["file"] == "child.json"
    assert json.loads(capsys.readouterr().out)["parents_with_evidence"] == 1


def test_cli_rejects_output_overwriting_input(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["--catalog", str(tmp_path / "same.json"), "--corpus", str(tmp_path),
              "--output", str(tmp_path / "same.json")])
    assert error.value.code == 2


def test_scope_expansion_adds_targets_to_new_matching_parents(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("leaf", ["Leaf"], "leaf", [[
        "<HUAWEI> system-view", "[~HUAWEI] root",
        "[*HUAWEI-root] branch", "[*HUAWEI-root-branch] leaf",
    ]])
    add("root", ["System view"], "root", switch_to_view=["A"])
    add("branch_a", ["A"], "branch")
    add("branch_b", ["B"], "branch", [[
        "<HUAWEI> system-view", "[~HUAWEI] root", "[*HUAWEI-root] branch",
    ]])
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "root")["switch_to_view"] == [
        "A", "B",
    ]
    assert command(result.catalog, "A", "branch_a")["switch_to_view"] == ["Leaf"]
    assert command(result.catalog, "B", "branch_b")["switch_to_view"] == ["Leaf"]
    assert result.report["summary"]["multiple_parent_observations"] == 1
    assert result.report["unresolved"] == []
    records = [e for e in result.report["evidence"] if e["file"] == "leaf.json"]
    assert {e["parent_view"] for e in records} == {"A", "B"}
    assert {e["matched_parent_formats"] for e in records} == {2}
    assert {e["first_pass"] for e in records} == {1, 2}


def test_bare_prompt_preserves_the_block_and_nonzero_cli_index(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("enter", ["System view"], "enter")
    add("child", ["Child"], "child", [[
        "<HUAWEI> system-view", "[~HUAWEI] enter",
        "[*HUAWEI-child]", "[*HUAWEI-child] child",
    ]])
    path = corpus / "child.json"
    page = json.loads(path.read_text())
    page["CLIs"].insert(0, "undo child")
    path.write_text(json.dumps(page))
    document["views"]["Child"][0]["documentation_source"]["format_index"] = 1
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "enter")["switch_to_view"] == [
        "Child",
    ]
    assert result.report["evidence"][0]["child_documentation_source"] == {
        "file": "child.json", "format_index": 1,
    }


def test_all_matching_acl_formats_receive_child_views_without_number_ranges(tmp_path):
    corpus, document, add = fixture(tmp_path)
    kinds = ["basic", "user", "advance", "link", "arp"]
    for kind in kinds:
        add(f"acl_{kind}", ["System view"], f"acl [ number ] <{kind}-acl-number>")
    add("basic_rule", ["Basic ACL view"], "rule <action>", [[
        "<HUAWEI> system-view", "[~HUAWEI] acl number 2999",
        "[*HUAWEI-acl-2999] rule deny",
    ]])
    add("advanced_rule", ["Advanced ACL view"], "rule tcp <action>", [[
        "<HUAWEI> system-view", "[~HUAWEI] acl number 3999",
        "[*HUAWEI-acl-3999] rule tcp permit",
    ]])
    result = recover_hierarchy(document, corpus)
    for kind in kinds:
        assert command(result.catalog, "System view", f"acl_{kind}")[
            "switch_to_view"
        ] == ["Advanced ACL view", "Basic ACL view"]
    assert result.report["unresolved"] == []
    assert len(result.report["evidence"]) == 10
    assert result.report["summary"]["multiple_parent_observations"] == 2
    assert all(e["matched_parent_formats"] == 5 for e in result.report["evidence"])


def test_bad_example_is_fully_skipped_but_other_examples_on_page_are_kept(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("bad_parent", ["System view"], "bad")
    add("good_parent", ["System view"], "good")
    add("child", ["Child"], "child", [
        [
            "<HUAWEI> system-view", "[~HUAWEI] bad", "[*HUAWEI-bad] child",
            "[*HUAWEI-bad] quit", "[*HUAWEI-bad] bad",
        ],
        [
            "<HUAWEI> system-view", "[~HUAWEI] good", "[*HUAWEI-good] child",
        ],
    ])
    result = recover_hierarchy(document, corpus)
    assert "switch_to_view" not in command(result.catalog, "System view", "bad_parent")
    assert command(result.catalog, "System view", "good_parent")[
        "switch_to_view"
    ] == ["Child"]
    assert result.report["summary"]["skipped_examples"] == 1
    assert result.report["skipped_examples"][0]["file"] == "child.json"
    assert result.report["skipped_examples"][0]["example_index"] == 0
    assert len(result.report["evidence"]) == 1
    assert result.report["evidence"][0]["example_index"] == 1


@pytest.mark.parametrize("child_page_first", [True, False])
def test_ipv6_family_switch_is_recovered_on_the_next_pass(tmp_path, child_page_first):
    corpus, document, add = fixture(tmp_path)
    add("bgp", ["System view"], "bgp <asn>")

    def family():
        add("family", ["BGP view"], "ipv6-family unicast", [[
            "<HUAWEI> system-view", "[~HUAWEI] bgp 100",
            "[*HUAWEI-bgp] ipv6-family unicast",
        ]])

    def child():
        add("peer", ["BGP IPv6 view"], "peer <ip> enable", [[
            "<HUAWEI> system-view", "[~HUAWEI] bgp 100",
            "[*HUAWEI-bgp] ipv6-family unicast",
            "[*HUAWEI-bgp-ipv6] peer 2001:db8::1 enable",
        ]])

    for create_page in ((child, family) if child_page_first else (family, child)):
        create_page()
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "System view", "bgp")["switch_to_view"] == [
        "BGP view",
    ]
    assert command(result.catalog, "BGP view", "family")["switch_to_view"] == [
        "BGP IPv6 view",
    ]
    passes = {e["file"]: e["first_pass"] for e in result.report["evidence"]}
    assert passes == {"family.json": 1, "peer.json": 2}
    assert result.report["summary"]["passes"] == 3  # Includes the final stable pass.
    assert result.report["unresolved"] == []


def test_multiple_ancestor_formats_union_their_known_target_views(tmp_path):
    corpus, document, add = fixture(tmp_path)
    add("root_generic", ["System view"], "root <name>", switch_to_view=["A"])
    add("root_literal", ["System view"], "root main", switch_to_view=["B"])
    add("branch_a", ["A"], "branch")
    add("branch_b", ["B"], "branch")
    add("leaf", ["Leaf"], "leaf", [[
        "<HUAWEI> system-view", "[~HUAWEI] root main",
        "[*HUAWEI-root] branch", "[*HUAWEI-root-branch] leaf",
    ]])
    result = recover_hierarchy(document, corpus)
    assert command(result.catalog, "A", "branch_a")["switch_to_view"] == ["Leaf"]
    assert command(result.catalog, "B", "branch_b")["switch_to_view"] == ["Leaf"]
    assert result.report["unresolved"] == []
