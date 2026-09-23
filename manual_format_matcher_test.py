"""Editable format-mapping examples. Run: python3.13 manual_metadata_test.py."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_format_matcher import FormatMatcher, PreparedMetadata  # noqa: E402
from vrp_parser_automaton import CommandLineParser, ParsedCommand  # noqa: E402


def parameter_rule(name: str) -> dict[str, Any]:
    return {
        "when": "always",
        "condition": "parameter_name",
        "parameter_name": name,
        "entity_type": "vlan",
    }


# Edit these documents, patterns and lines to explore your own examples.
CASES: dict[str, dict[str, Any]] = {
    "vlan": {
        "commands": [
            "port trunk allow-pass vlan { "
            "{ INTEGER<1-4095> [ to INTEGER<1-4095> ] } &<1-10> | all }",
        ],
        "documents": [
            {
                "id": "vlan-full",
                "format": "port trunk allow-pass vlan "
                "{ all | { <vlan-id1> [ to <vlan-id2> ] }&<1-10> }",
                "creates": [],
                "requires": [
                    parameter_rule("vlan-id1"),
                    parameter_rule("vlan-id2"),
                    {
                        "when": "always",
                        "condition": "command",
                        "context": "current",
                        "command": "port link-type trunk",
                    },
                ],
            },
            {
                "id": "vlan-list-only",
                "format": "port trunk allow-pass vlan "
                "{ <vlan-id1> [ to <vlan-id2> ] } &<1-10>",
                "requires": [parameter_rule("vlan-id1"), parameter_rule("vlan-id2")],
            },
        ],
        "lines": [
            "port trunk allow-pass vlan 10 to 20 30",
            "port trunk allow-pass vlan all",
            "port trunk allow-pass vlan 4096",
        ],
    },
    "conditional": {
        "commands": ["command INTEGER<1-100> [ to INTEGER<1-100> ]"],
        "documents": [
            {
                "format": "command { <a> | <b> to <c> }",
                "requires": [parameter_rule(name) for name in ("a", "b", "c")],
            }
        ],
        "lines": ["command 10", "command 10 to 20"],
    },
    "ambiguous": {
        "commands": ["command [ INTEGER<1-100> ] [ INTEGER<1-100> ]"],
        "documents": [
            {
                "format": "command [ <a> ] [ <b> ]",
                "requires": [parameter_rule("a")],
            }
        ],
        "lines": ["command 10", "command 10 20"],
    },
    "overlap": {
        "commands": ["command { left INTEGER<1-100> | shared INTEGER<1-100> }"],
        "documents": [
            {
                "format": "command { shared <id> | right <id> }",
                "requires": [parameter_rule("id")],
            }
        ],
        "lines": ["command shared 10", "command left 10"],
    },
    "prefix": {
        "commands": ["command INTEGER<1-100> device"],
        "documents": [
            {
                "format": "command <id> documentation",
                "requires": [parameter_rule("id")],
            }
        ],
        "lines": ["command 10 device"],
    },
    "set": {
        "commands": ["command { alpha INTEGER<1-100> | beta INTEGER<1-100> } *"],
        "documents": [
            {
                "format": "command { beta <b> | alpha <a> } *",
                "requires": [parameter_rule("a"), parameter_rule("b")],
            }
        ],
        "lines": ["command beta 20 alpha 10", "command alpha 10 alpha 20"],
    },
}


for case_name, opening, closing in (
    ("wide-set", "{", "}"),
    ("wide-optional-set", "[", "]"),
):
    CASES[case_name] = {
        "commands": [
            "command "
            + opening
            + " "
            + " | ".join(f"option{i} INTEGER<1-100>" for i in range(24))
            + " "
            + closing
            + " *"
        ],
        "documents": [
            {
                "format": "command "
                + opening
                + " "
                + " | ".join(f"option{i} <value{i}>" for i in reversed(range(24)))
                + " "
                + closing
                + " *",
                "requires": [parameter_rule(f"value{i}") for i in range(24)],
            }
        ],
        "lines": [
            "command option23 24 option0 1 option12 13",
            "command",
            "command option0 1 option0 2",
        ],
    }

CASES["large-repeat"] = {
    "commands": ["command INTEGER<1-100> &<1-100000>"],
    "documents": [
        {"format": "command <value> &<1-100000>", "requires": [parameter_rule("value")]}
    ],
    "lines": ["command 1 2 3"],
}


def describe(prepared: PreparedMetadata) -> None:
    for pair in prepared.pairs:
        comparison = pair.comparison
        print(f"\nDOCUMENT [{pair.document_id}]: {pair.document_format}")
        print(f"DEVICE: {pair.device_format}")
        print(f"RELATION: {comparison.relation}")
        print(f"IDENTICAL STRUCTURE: {comparison.structurally_identical}")
        for title, witness in (
            ("COMMON", comparison.common_example),
            ("DOCUMENT ONLY", comparison.document_only_example),
            ("DEVICE ONLY", comparison.device_only_example),
        ):
            if witness is not None:
                print(f"{title}: {' '.join(witness) or '<empty>'}")
        if comparison.relation == "prefix_only":
            print(f"SHARED PREFIX EXAMPLE: {' '.join(comparison.common_prefix)}")
        if comparison.reason:
            print(f"REASON: {comparison.reason}")
        print(f"STRATEGY: {pair.strategy}")
        nodes = []
        if pair.program is not None:
            print(f"PREPARED INSTRUCTIONS: {len(pair.program.instructions)}")
            nodes.extend(pair.program.slots)
        if pair.automaton is not None:
            print(f"PREPARED STATES: {len(pair.automaton.edges)}")
            nodes.extend(arc for arcs in pair.automaton.edges for arc in arcs)
        links = sorted(
            {
                (node.document.name or "", node.document.slot_id, node.device.slot_id)
                for node in nodes
                if node.document is not None and node.device is not None
            }
        )
        if links:
            print("POSSIBLE BINDINGS (guarded by complete paths):")
            for name, document_slot, device_slot in links:
                print(f"  {name} [{document_slot}] -> {device_slot}")


def argument_parser() -> argparse.ArgumentParser:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--case", choices=CASES, default="vlan")
    arguments.add_argument(
        "--patterns", type=Path, help="Device JSON with commands array"
    )
    arguments.add_argument(
        "--documents", type=Path, help="JSON array of document metadata"
    )
    arguments.add_argument(
        "--line", action="append", help="Repeat for multiple input lines"
    )
    arguments.add_argument("--save", type=Path, help="Save prepared JSON artifact")
    arguments.add_argument(
        "--load", type=Path, help="Run a saved artifact without preparation"
    )
    arguments.add_argument(
        "--json", action="store_true", help="Print full runtime details"
    )
    return arguments


def read_arguments(arguments: argparse.ArgumentParser) -> argparse.Namespace:
    args = arguments.parse_args()
    if args.load and args.documents:
        arguments.error("--documents cannot be combined with --load")
    if not args.load and bool(args.patterns) != bool(args.documents):
        arguments.error("supply both --patterns and --documents")
    return args


def prepare_session(
    args: argparse.Namespace,
    arguments: argparse.ArgumentParser,
) -> tuple[CommandLineParser, PreparedMetadata]:
    case = CASES[args.case]
    if args.load:
        prepared = PreparedMetadata.from_json(args.load.read_text(encoding="utf-8"))
        formats = {pair.pattern_id: pair.device_format for pair in prepared.pairs}
        if not formats:
            arguments.error("loaded artifact contains no device formats")
        parser = (
            CommandLineParser.from_json_file(args.patterns)
            if args.patterns
            else CommandLineParser({"commands": list(formats.values())})
        )
    else:
        parser = (
            CommandLineParser.from_json_file(args.patterns)
            if args.patterns
            else CommandLineParser({"commands": case["commands"]})
        )
        documents = (
            json.loads(args.documents.read_text(encoding="utf-8"))
            if args.documents
            else case["documents"]
        )
        prepared = FormatMatcher().compile(parser, documents)
    return parser, prepared


def show_line(
    parser: CommandLineParser,
    prepared: PreparedMetadata,
    raw: str,
    *,
    detailed: bool,
) -> None:
    print(f"\nINPUT: {raw}")
    line = parser.parse(raw)
    if not isinstance(line, ParsedCommand):
        print(json.dumps(asdict(line), ensure_ascii=False, default=str))
        return
    print(f"DEVICE PARSE: {line.status}")
    report = prepared.evaluate(line)
    if detailed:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
        return
    for application in report.applications:
        print(f"  [{application.document_id}] {application.status}")
        print(f"    BINDINGS: {application.binding_status}")
        if application.reason:
            print(f"    {application.reason}")
        for index, alternative in enumerate(application.alternatives):
            print(f"    BINDING ALTERNATIVE {index + 1}:")
            for binding in alternative.bindings:
                print(
                    f"      {binding.document.name} {binding.document.iterations}"
                    f" -> {binding.device.slot_id} {binding.device.iterations}"
                    f" = {binding.value.normalized!r}"
                )
        for rule in application.rules:
            print(f"    RULE {rule.rule_id}: {rule.status}")


def main() -> None:
    arguments = argument_parser()
    args = read_arguments(arguments)
    parser, prepared = prepare_session(args, arguments)
    describe(prepared)
    if args.save:
        args.save.write_text(prepared.to_json(), encoding="utf-8")
        print(f"\nSAVED: {args.save}")

    if args.line is not None:
        lines = args.line
    elif args.load or args.patterns:
        lines = []
    else:
        lines = CASES[args.case]["lines"]
    for raw in lines:
        show_line(parser, prepared, raw, detailed=args.json)


if __name__ == "__main__":
    main()
