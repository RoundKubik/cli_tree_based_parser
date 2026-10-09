"""Match command catalogs offline and save parameter correspondences as JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from vrp_format_matcher import (
    FormatError,
    FormatMatcher,
    PreparationProgress,
    PreparedMapping,
)


def describe(prepared: PreparedMapping) -> None:
    for pattern_id, device in prepared.devices.items():
        print(f"\nDEVICE [{pattern_id}]: {device.device_format}")
        print(f"STATUS: {device.status}; STAGE: {device.stage}")
        for pair in device.mappings:
            print(f"  DOCUMENT [{pair.document_id}]: {pair.document_format}")
            print(f"  RELATION: {pair.status}")
            print(f"  STAGE: {pair.stage}")
            print(f"  BINDING MODE: {pair.binding_mode}")
            if pair.bindings:
                print("  PARAMETER MAPPING:")
                for binding in pair.bindings:
                    doc, target = binding.document, binding.device
                    print(
                        f"    {target.declaration} [{target.slot_id}] -> "
                        f"{doc.name} [{doc.slot_id}]"
                    )


def compile_inputs(args: argparse.Namespace) -> PreparedMapping:
    targets = json.loads(args.patterns.read_text(encoding="utf-8"))
    documents = json.loads(args.documents.read_text(encoding="utf-8"))
    if not isinstance(targets, dict):
        raise FormatError("target input must be a catalog or an object with commands")
    if "type" not in targets and not isinstance(targets.get("commands"), list):
        raise FormatError("legacy target input must contain a commands array")
    matcher = FormatMatcher(context_mode=args.context_mode)
    if isinstance(documents, dict):
        if args.target_syntax is not None:
            raise FormatError(
                "v1 catalogs select syntax from source; omit --target-syntax"
            )
        if args.save_catalog:
            prepared = matcher.prepare_catalogs(
                targets, documents, on_progress=show_progress
            )
            result = prepared.mapping
            args.save_catalog.write_text(
                json.dumps(prepared.catalog, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"RUNTIME CATALOG: {prepared.catalog['type']}")
            if args.context_mode == "hierarchy":
                print(f"UNRESOLVED COMMANDS: {len(prepared.unresolved)}")
            print(f"SAVED CATALOG: {args.save_catalog}")
        else:
            result = matcher.compile_catalogs(
                targets, documents, on_progress=show_progress
            )
    elif isinstance(documents, list):
        if args.save_catalog:
            raise FormatError("--save-catalog requires two v1 catalogs")
        if "type" in targets:
            raise FormatError(
                "supply both inputs as v1 catalogs, or both as legacy inputs"
            )
        result = matcher.compile_formats(
            targets["commands"],
            documents,
            target_syntax=args.target_syntax or "device",
            on_progress=show_progress,
        )
    else:
        raise FormatError("documentation input must be a catalog or a format array")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(prog="vrp-format-matcher", description=__doc__)
    arguments.add_argument(
        "--patterns",
        type=Path,
        required=True,
        help="Device/documentation catalog or legacy commands JSON",
    )
    arguments.add_argument(
        "--documents",
        type=Path,
        required=True,
        help="Documentation catalog or legacy format array",
    )
    arguments.add_argument(
        "--target-syntax",
        choices=("device", "document"),
        help="Legacy inputs only; v1 catalogs select syntax from source",
    )
    arguments.add_argument("--summary", action="store_true")
    arguments.add_argument(
        "--context-mode", choices=("system", "hierarchy"), default="system"
    )
    arguments.add_argument("--save", type=Path, help="Write plain result JSON")
    arguments.add_argument(
        "--save-catalog",
        type=Path,
        help="Save the runtime catalog; "
        "hierarchy recovery requires --context-mode hierarchy",
    )
    args = arguments.parse_args(argv)
    try:
        result = compile_inputs(args)
        if args.save:
            args.save.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.summary:
        describe(result)
    print("DEVICES:", dict(Counter(d.status for d in result.devices.values())))
    print("STAGES:", dict(Counter(d.stage for d in result.devices.values())))
    print("PAIRS:", dict(Counter(p.status for p in result.pairs)))
    print("PARAMETER LINKS:", sum(len(p.bindings) for p in result.pairs))
    if result.hierarchy is not None:
        hierarchy = result.hierarchy
        print(
            "HIERARCHY TARGETS:",
            dict(Counter(target.status for target in hierarchy.targets.values())),
        )
        print(
            "DOCUMENTED EFFECTS:",
            dict(
                Counter(
                    link.transition.kind for link in hierarchy.evidence.command_links
                )
            ),
        )
        if not args.summary:
            for name, target in hierarchy.targets.items():
                print(
                    f"  {name}: {target.status}; device_view={target.device_view!r}; "
                    f"candidates={[view.device_view for view in target.candidates]}"
                )
    if args.save:
        print(f"SAVED: {args.save}")
    return 0


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
