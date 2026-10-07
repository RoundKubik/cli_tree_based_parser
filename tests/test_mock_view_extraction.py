"""Documentation hierarchy keeps literal targets and checks example context."""

from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from pathlib import Path

EXTRACTOR = runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "data/mocks/cloudengine_150/refresh_switches.py"
    )
)


class MockViewExtractionTest(unittest.TestCase):
    def test_generic_acl_target_is_not_specialized(self) -> None:
        function = "The acl number command creates an ACL and displays the ACL view."
        for parameter in ("basic-acl-number", "advance-acl-number"):
            with self.subTest(parameter=parameter):
                evidence = EXTRACTOR["switch_evidence"](
                    f"acl [ number ] <{parameter}>", function
                )
                self.assertEqual(evidence["view"], "ACL view")
                self.assertEqual(evidence["text"], function)

    def test_exact_grpc_name_is_preserved(self) -> None:
        evidence = EXTRACTOR["switch_evidence"](
            "grpc server",
            "The grpc server command displays the gRPC IPv4 server view.",
        )
        self.assertEqual(evidence["view"], "gRPC IPv4 server view")
        self.assertEqual(
            EXTRACTOR["view_key"](evidence["view"]), "grpc ipv4 server view"
        )

    def test_repeated_reference_to_same_target_is_unambiguous(self) -> None:
        evidence = EXTRACTOR["switch_evidence"](
            "bgp <as-number>",
            "The bgp command enables BGP and displays the BGP view "
            "or displays the BGP view directly.",
        )
        self.assertEqual(evidence["view"], "BGP view")

    def test_unsupported_or_ambiguous_statements_stay_unknown(self) -> None:
        bodies = (
            "creates an ACL.",
            "creates and runs an OSPF process.",
            "displays the BGP-IPv4 unicast address family.",
            "does not enter the ACL view.",
            "may enter the ACL view.",
            "enters the ACL view if the ACL exists.",
            "enters the ACL view when the ACL exists.",
            "enters the Basic ACL view or the Advanced ACL view.",
            "enters the Basic ACL view or enters the Advanced ACL view.",
            "displays information in the ACL view.",
            "displays configuration for the ACL view.",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(
                    EXTRACTOR["switch_evidence"](
                        "acl <number>", f"The acl command {body}"
                    )
                )

    def test_other_commands_and_undo_do_not_inherit_transition(self) -> None:
        function = (
            "The acl command displays the ACL view.\n"
            "The undo acl command deletes an ACL."
        )
        for pattern in ("undo acl <number>", "bgp <as-number>"):
            self.assertIsNone(EXTRACTOR["switch_evidence"](pattern, function))

    def test_specific_command_description_overrides_generic_subject(self) -> None:
        evidence = EXTRACTOR["switch_evidence"](
            "grpc server ipv6",
            "The grpc server command displays the gRPC IPv4 server view.\n"
            "The grpc server ipv6 command displays the gRPC IPv6 server view.",
        )
        self.assertEqual(evidence["view"], "gRPC IPv6 server view")

    def test_refresh_removes_guesses_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            corpus.mkdir()
            formats = ["acl [ number ] <advance-acl-number>", "undo acl <number>"]
            page = {
                "CLIs": formats,
                "ParentView": ["System view"],
                "FuncDef": (
                    "The acl number command creates an ACL and displays the ACL view."
                ),
            }
            (corpus / "acl.json").write_text(json.dumps(page), encoding="utf-8")
            commands = [
                {
                    "format": pattern,
                    "switch_to_view": "Advanced ACL view" if i == 0 else None,
                    "metadata": {"corpus_file": "acl.json", "format_index": i},
                }
                for i, pattern in enumerate(formats)
            ]
            grouped = {
                "type": "grouped",
                "entry_view": "System view",
                "metadata": {},
                "views": {"System view": commands},
            }
            for name, view, pattern, example in (
                ("basic", "Basic ACL view", "rule <id>", "rule 10"),
                (
                    "advanced", "Advanced ACL view",
                    "description <text>", "description test",
                ),
            ):
                child_page = {
                    "CLIs": [pattern],
                    "ParentView": [view],
                    "FuncDef": "Sets a property.",
                    "Examples": [[
                        "<HUAWEI> system-view",
                        "[~HUAWEI] acl number 2999",
                        f"[*HUAWEI-acl] {example}",
                    ]],
                }
                filename = f"{name}.json"
                (corpus / filename).write_text(json.dumps(child_page), encoding="utf-8")
                grouped["views"][view] = [{
                    "format": pattern,
                    "metadata": {"corpus_file": filename, "format_index": 0},
                }]
            flat = {"type": "flat", "metadata": {}, "commands": commands}
            for name, data in (("grouped", grouped), ("flat", flat)):
                (root / f"documentation_{name}.json").write_text(
                    json.dumps(data), encoding="utf-8"
                )
            report = EXTRACTOR["refresh"](corpus, root)
            self.assertEqual(report["switches"], 1)
            path = root / "documentation_grouped.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["entry_view"], "system view")
            self.assertEqual(len(result["views"]["acl view"]), 2)
            self.assertEqual(set(result["views"]), {"system view", "acl view"})
            self.assertEqual(
                result["views"]["acl view"][1]["metadata"]["parent_view"],
                "Advanced ACL view",
            )
            entries = result["views"]["system view"]
            self.assertEqual(entries[0]["switch_to_view"], "acl view")
            self.assertNotIn("switch_to_view", entries[1])
            before = path.read_bytes()
            EXTRACTOR["refresh"](corpus, root)
            self.assertEqual(path.read_bytes(), before)
            flat_result = json.loads((root / "documentation_flat.json").read_text())
            self.assertTrue(
                all("switch_to_view" not in c for c in flat_result["commands"])
            )

    def test_examples_restore_transition_missing_from_funcdef(self) -> None:
        commands = [{
            "format": "service <name>",
            "metadata": {"parent_view": "System view"},
        }]
        pages = {"setting.json": {
            "ParentView": ["Service view"],
            "CLIs": ["setting <value>"],
            "Examples": [[
                "<HUAWEI> system-view",
                "[~HUAWEI] service example",
                "[*HUAWEI-service-example] setting value",
            ]],
        }}
        observed = EXTRACTOR["example_switches"](commands, pages, "system view")
        self.assertEqual(set(observed[0]), {"service view"})
        self.assertEqual(observed[0]["service view"]["field"], "Examples")

    def test_same_command_in_unrelated_view_is_not_an_entry(self) -> None:
        commands = [{
            "format": "acl <number>",
            "metadata": {"parent_view": view},
        } for view in ("System view", "GRPC server view")]
        pages = {"rule.json": {
            "ParentView": ["Basic ACL view"],
            "CLIs": ["rule <id>"],
            "Examples": [[
                "<HUAWEI> system-view",
                "[~HUAWEI] acl 2999",
                "[*HUAWEI-acl-2999] rule 10",
            ]],
        }}
        observed = EXTRACTOR["example_switches"](commands, pages, "system view")
        self.assertEqual(set(observed), {0})

    def test_commit_marker_and_unrelated_examples_do_not_prove_a_switch(self) -> None:
        commands = [{
            "format": "setting <value>",
            "metadata": {"parent_view": "System view"},
        }]
        for parent_views, last in (
            (["System view"], "[*HUAWEI] setting value"),
            (["Target view"], "[*HUAWEI-new] unrelated value"),
            (["First view", "Second view"], "[*HUAWEI-new] setting value"),
        ):
            with self.subTest(parent_views=parent_views, last=last):
                pages = {"setting.json": {
                    "ParentView": parent_views,
                    "CLIs": ["setting <value>"],
                    "Examples": [[
                        "<HUAWEI> system-view",
                        "[~HUAWEI] setting value",
                        last,
                    ]],
                }}
                self.assertEqual(
                    EXTRACTOR["example_switches"](commands, pages, "system view"), {}
                )

    def test_missing_and_unreachable_views_are_rejected(self) -> None:
        for views in (
            {"system view": [{"switch_to_view": "missing view"}]},
            {"system view": [{"switch_to_view": "empty view"}], "empty view": []},
            {"system view": [], "orphan view": [{"format": "setting"}]},
        ):
            with self.subTest(views=views):
                with self.assertRaises(ValueError):
                    EXTRACTOR["validate_hierarchy"](views, "system view")

    def test_checked_in_hierarchy_has_populated_reachable_views(self) -> None:
        root = Path(__file__).resolve().parents[1] / "data/mocks/cloudengine_150"
        grouped = json.loads((root / "documentation_grouped.json").read_text())
        EXTRACTOR["validate_hierarchy"](grouped["views"], grouped["entry_view"])
        self.assertTrue(all(grouped["views"].values()))
        self.assertNotIn("advanced acl view", grouped["views"])
        self.assertEqual(
            {c["metadata"]["parent_view"] for c in grouped["views"]["acl view"]},
            {"Basic ACL view", "Advanced ACL view"},
        )
        device = json.loads((root / "device_grouped.json").read_text())
        self.assertIn("acl4-basic", device["views"])
        self.assertIn("acl4-advance", device["views"])
