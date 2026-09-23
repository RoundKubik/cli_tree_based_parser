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
    "wide-set": {
        "commands": [
            "command { option0 INTEGER<1-100> | option1 "
            "INTEGER<1-100> | option2 INTEGER<1-100> | "
            "option3 INTEGER<1-100> | option4 "
            "INTEGER<1-100> | option5 INTEGER<1-100> | "
            "option6 INTEGER<1-100> | option7 "
            "INTEGER<1-100> | option8 INTEGER<1-100> | "
            "option9 INTEGER<1-100> | option10 "
            "INTEGER<1-100> | option11 INTEGER<1-100> | "
            "option12 INTEGER<1-100> | option13 "
            "INTEGER<1-100> | option14 INTEGER<1-100> | "
            "option15 INTEGER<1-100> | option16 "
            "INTEGER<1-100> | option17 INTEGER<1-100> | "
            "option18 INTEGER<1-100> | option19 "
            "INTEGER<1-100> | option20 INTEGER<1-100> | "
            "option21 INTEGER<1-100> | option22 "
            "INTEGER<1-100> | option23 INTEGER<1-100> } *"
        ],
        "documents": [
            {
                "format": "command { option23 <value23> | "
                "option22 <value22> | option21 "
                "<value21> | option20 <value20> | "
                "option19 <value19> | option18 "
                "<value18> | option17 <value17> | "
                "option16 <value16> | option15 "
                "<value15> | option14 <value14> | "
                "option13 <value13> | option12 "
                "<value12> | option11 <value11> | "
                "option10 <value10> | option9 "
                "<value9> | option8 <value8> | "
                "option7 <value7> | option6 "
                "<value6> | option5 <value5> | "
                "option4 <value4> | option3 "
                "<value3> | option2 <value2> | "
                "option1 <value1> | option0 "
                "<value0> } *"
            }
        ],
    },
    "wide-optional-set": {
        "commands": [
            "command [ option0 INTEGER<1-100> | "
            "option1 INTEGER<1-100> | option2 "
            "INTEGER<1-100> | option3 "
            "INTEGER<1-100> | option4 "
            "INTEGER<1-100> | option5 "
            "INTEGER<1-100> | option6 "
            "INTEGER<1-100> | option7 "
            "INTEGER<1-100> | option8 "
            "INTEGER<1-100> | option9 "
            "INTEGER<1-100> | option10 "
            "INTEGER<1-100> | option11 "
            "INTEGER<1-100> | option12 "
            "INTEGER<1-100> | option13 "
            "INTEGER<1-100> | option14 "
            "INTEGER<1-100> | option15 "
            "INTEGER<1-100> | option16 "
            "INTEGER<1-100> | option17 "
            "INTEGER<1-100> | option18 "
            "INTEGER<1-100> | option19 "
            "INTEGER<1-100> | option20 "
            "INTEGER<1-100> | option21 "
            "INTEGER<1-100> | option22 "
            "INTEGER<1-100> | option23 "
            "INTEGER<1-100> ] *"
        ],
        "documents": [
            {
                "format": "command [ option23 "
                "<value23> | option22 "
                "<value22> | option21 "
                "<value21> | option20 "
                "<value20> | option19 "
                "<value19> | option18 "
                "<value18> | option17 "
                "<value17> | option16 "
                "<value16> | option15 "
                "<value15> | option14 "
                "<value14> | option13 "
                "<value13> | option12 "
                "<value12> | option11 "
                "<value11> | option10 "
                "<value10> | option9 "
                "<value9> | option8 "
                "<value8> | option7 "
                "<value7> | option6 "
                "<value6> | option5 "
                "<value5> | option4 "
                "<value4> | option3 "
                "<value3> | option2 "
                "<value2> | option1 "
                "<value1> | option0 "
                "<value0> ] *"
            }
        ],
    },
    "large-repeat": {
        "commands": ["command INTEGER<1-100> &<1-100000>"],
        "documents": [{"format": "command <value> &<1-100000>"}],
    },
}


def describe(prepared: PreparedMapping) -> None:
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
        print(f"BINDING MODE: {pair.binding_mode}")
        if pair.bindings:
            print("PARAMETER MAPPING:")
            for binding in pair.bindings:
                doc, device = binding.document, binding.device
                print(
                    f"  {doc.name} [{doc.slot_id}] -> "
                    f"{device.declaration} [{device.slot_id}]"
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
    arguments.add_argument("--mode", choices=("best", "all"), default="best")
    arguments.add_argument(
        "--exhaustive", action="store_true", help="All pairs; small diagnostics only"
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
        mode=args.mode,
        exhaustive=args.exhaustive or not args.patterns,
        on_progress=show_progress if args.patterns else None,
    )
    if not args.summary:
        describe(result)
    print("DOCUMENTS:", dict(Counter(d.status for d in result.documents)))
    print("PAIRS:", dict(Counter(p.status for p in result.pairs)))
    print("PARAMETER LINKS:", sum(len(p.bindings) for p in result.pairs))
    if args.save:
        args.save.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"SAVED: {args.save}")


def show_progress(progress: PreparationProgress) -> None:
    if (
        progress.documents_done % 100 == 0
        or progress.documents_done == progress.documents_total
    ):
        print(
            f"Documents: {progress.documents_done}/{progress.documents_total}; "
            f"pairs: {progress.pairs_prepared}",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    main()
