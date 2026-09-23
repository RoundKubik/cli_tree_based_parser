"""Editable format-mapping examples. Run: python3.13 manual_format_matcher_test.py."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_format_matcher import (  # noqa: E402
    FormatMatcher,
    PreparationProgress,
    PreparedMapping,
)

# Editable format-only examples.
CASES: dict[str, dict[str, Any]] = {
    "vlan": {
        "commands": [
            "port trunk allow-pass vlan { { INTEGER<1-4095> [ "
            "to INTEGER<1-4095> ] } &<1-10> | all }"
        ],
        "documents": [
            {
                "id": "vlan-full",
                "format": "port trunk allow-pass vlan { all | { "
                "<vlan-id1> [ to <vlan-id2> ] }&<1-10> "
                "}",
            },
            {
                "id": "vlan-list-only",
                "format": "port trunk allow-pass vlan { "
                "<vlan-id1> [ to <vlan-id2> ] } "
                "&<1-10>",
            },
        ],
    },
    "conditional": {
        "commands": ["command INTEGER<1-100> [ to INTEGER<1-100> ]"],
        "documents": [{"format": "command { <a> | <b> to <c> }"}],
    },
    "ambiguous": {
        "commands": ["command [ INTEGER<1-100> ] [ INTEGER<1-100> ]"],
        "documents": [{"format": "command [ <a> ] [ <b> ]"}],
    },
    "overlap": {
        "commands": ["command { left INTEGER<1-100> | shared INTEGER<1-100> }"],
        "documents": [{"format": "command { shared <id> | right <id> }"}],
    },
    "prefix": {
        "commands": ["command INTEGER<1-100> device"],
        "documents": [{"format": "command <id> documentation"}],
    },
    "set": {
        "commands": ["command { alpha INTEGER<1-100> | beta INTEGER<1-100> } *"],
        "documents": [{"format": "command { beta <b> | alpha <a> } *"}],
    },
    "stages": {
        "commands": [
            "exact INTEGER<1-100>",
            "interface { STRING<1-20> STRING<1-20> | STRING<1-20> }",
            "vlan { all | INTEGER<1-100> }",
            "prefix INTEGER<1-100> device INTEGER<1-100>",
        ],
        "documents": [
            {"id": "exact", "format": "exact <value>"},
            {"id": "reordered", "format": "interface { <name> | <type> <name> }"},
            {"id": "intersection", "format": "vlan <id>"},
            {"id": "prefix", "format": "prefix <first> documentation <tail>"},
        ],
    },
    "large-repeat": {
        "commands": ["command INTEGER<1-100> &<1-100000>"],
        "documents": [{"format": "command <value> &<1-100000>"}],
    },
}


# Identical wide sets demonstrate that source alignment never expands masks.
for case_name, opening, closing in (
    ("wide-set", "{", "}"),
    ("wide-optional-set", "[", "]"),
):
    device_branches = " | ".join(f"option{i} INTEGER<1-100>" for i in range(24))
    document_branches = " | ".join(f"option{i} <value{i}>" for i in reversed(range(24)))
    CASES[case_name] = {
        "commands": [f"command {opening} {device_branches} {closing} *"],
        "documents": [{"format": f"command {opening} {document_branches} {closing} *"}],
    }


def describe(prepared: PreparedMapping) -> None:
    for pattern_id, device in prepared.devices.items():
        print(f"\nDEVICE [{pattern_id}]: {device.device_format}")
        print(f"STATUS: {device.status}; STAGE: {device.stage}")
        for pair in device.mappings:
            print(f"  DOCUMENT [{pair.document_id}]: {pair.document_format}")
            print(f"  RELATION: {pair.status}")
            print(f"  BINDING MODE: {pair.binding_mode}")
            if pair.bindings:
                print("  PARAMETER MAPPING:")
                for binding in pair.bindings:
                    doc, target = binding.document, binding.device
                    print(
                        f"    {target.declaration} [{target.slot_id}] -> "
                        f"{doc.name} [{doc.slot_id}]"
                    )


def main() -> None:
    from collections import Counter

    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--case", choices=CASES, default="vlan")
    arguments.add_argument("--patterns", type=Path, help="JSON with commands array")
    arguments.add_argument(
        "--documents", type=Path, help="JSON array of id/format objects"
    )
    arguments.add_argument(
        "--target-syntax", choices=("device", "document"), default="device"
    )
    arguments.add_argument("--summary", action="store_true")
    arguments.add_argument("--save", type=Path, help="Write plain result JSON")
    args = arguments.parse_args()
    if bool(args.patterns) != bool(args.documents):
        arguments.error("supply both --patterns and --documents")
    case = CASES[args.case]
    targets = (
        json.loads(args.patterns.read_text(encoding="utf-8"))["commands"]
        if args.patterns
        else case["commands"]
    )
    documents = (
        json.loads(args.documents.read_text(encoding="utf-8"))
        if args.documents
        else case["documents"]
    )
    result = FormatMatcher().compile_formats(
        targets,
        documents,
        target_syntax=args.target_syntax,
        on_progress=show_progress if args.patterns else None,
    )
    if not args.summary:
        describe(result)
    print("DEVICES:", dict(Counter(d.status for d in result.devices.values())))
    print("STAGES:", dict(Counter(d.stage for d in result.devices.values())))
    print("PAIRS:", dict(Counter(p.status for p in result.pairs)))
    print("PARAMETER LINKS:", sum(len(p.bindings) for p in result.pairs))
    if args.save:
        args.save.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"SAVED: {args.save}")


def show_progress(progress: PreparationProgress) -> None:
    if (
        progress.devices_done % 100 == 0
        or progress.devices_done == progress.devices_total
    ):
        print(
            f"{progress.stage}: devices: "
            f"{progress.devices_done}/{progress.devices_total}; "
            f"pairs: {progress.pairs_prepared}",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    main()
