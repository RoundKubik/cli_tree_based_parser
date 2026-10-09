"""Look up semantic candidates after parsing; never select or apply their effects.

Run with the unchanged target catalog, its offline mapping and source documentation.
Without --mapping, --patterns must be the documentation catalog itself.
Conditional mappings remain explicit candidates until their saved graph is checked.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand


def record_at(document, location):
    commands = (
        document["commands"]
        if location["view"] is None
        else document["views"][location["view"]]
    )
    return commands[location["index"]]


def applicable_scope(line, mapping, location):
    """Reject candidates from the opposite scope, including older global mappings."""
    catalogs = mapping["catalogs"]
    target, docs = catalogs["device"], catalogs["documentation"]
    if target["type"] != "grouped" or docs["type"] != "grouped":
        return True
    view = location["view"]
    if view == docs.get("global_view"):
        return True
    return (view != docs["entry_view"]) == bool(line.indent)


def mapped_candidates(line, mapping, documentation):
    """Preserve each parse and documentation alternative as a separate candidate."""
    result = []
    for match in line.matches:
        device = mapping["devices"][match.pattern_id]
        if device["device_format"] != match.original_pattern:
            raise ValueError("The mapping does not belong to this runtime catalog")
        values = defaultdict(list)
        for value in match.parameters:
            values[value.slot_id].append(value)
        for pair in device["mappings"]:
            doc = mapping["documents"][pair["document_id"]]
            if not applicable_scope(line, mapping, doc["source"]):
                continue
            source = record_at(documentation, doc["source"])
            if source["format"] != doc["document_format"]:
                raise ValueError("The documentation has changed since matching")
            candidate = {
                "pattern_id": match.pattern_id,
                "variation_id": match.variation_id,
                "document_id": pair["document_id"],
                "source": doc["source"],
                "binding_mode": pair["binding_mode"],
                "status": pair["status"],
                "creates": source.get("creates", []),
                "requires": source.get("requires", []),
            }
            if pair["binding_mode"] == "structural":
                parameters = []
                for binding in pair["bindings"]:
                    slot = doc["slots"][binding["document"]]
                    device_slot = device["slots"][binding["device"]]
                    for value in values[binding["device"]]:
                        coordinates = dict(value.iterations)
                        iterations = [
                            [doc_repeat, coordinates[device_repeat]]
                            for device_repeat, doc_repeat in zip(
                                device_slot["repeat_ids"],
                                slot["repeat_ids"],
                                strict=True,
                            )
                        ]
                        parameters.append(
                            {
                                "parameter_name": slot["name"],
                                "slot_id": binding["document"],
                                "device_slot_id": value.slot_id,
                                "raw": value.raw,
                                "value": value.normalized,
                                "iterations": iterations,
                            }
                        )
                candidate["parameters"] = parameters
            else:
                # Slot presence alone cannot establish applicability to this line.
                candidate["needs_trace"] = True
                if "automaton_id" in pair:
                    candidate["automaton_id"] = pair["automaton_id"]
            result.append(candidate)
    return result


def direct_candidates(line, documentation):
    """Use source indices when the parser already consumes the documentation."""
    locations = (
        [{"view": None, "index": i} for i, _ in enumerate(documentation["commands"])]
        if documentation.get("type", "flat") == "flat"
        else [
            {"view": view, "index": i}
            for view, commands in documentation["views"].items()
            for i, _ in enumerate(commands)
        ]
    )
    result = []
    for match in line.matches:
        location = locations[match.pattern_index]
        source = record_at(documentation, location)
        if source["format"] != match.original_pattern:
            raise ValueError("The documentation does not belong to this parser")
        result.append(
            {
                "pattern_id": match.pattern_id,
                "variation_id": match.variation_id,
                "source": location,
                "parameters": [
                    {"parameter_name": p.declaration[1:-1], **asdict(p)}
                    for p in match.parameters
                    if p.declaration.startswith("<") and p.declaration.endswith(">")
                ],
                "creates": source.get("creates", []),
                "requires": source.get("requires", []),
            }
        )
    return result


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--patterns", type=Path, required=True)
    arguments.add_argument("--config", type=Path, required=True)
    arguments.add_argument("--mapping", type=Path)
    arguments.add_argument("--documentation", type=Path)
    args = arguments.parse_args()
    if args.mapping and args.documentation is None:
        arguments.error("--mapping requires --documentation")
    if not args.mapping and args.documentation:
        arguments.error("Without --mapping, --patterns is the documentation")

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    document = read(args.patterns)
    mapping = read(args.mapping) if args.mapping else None
    documentation = read(args.documentation) if args.documentation else document
    parser = ConfigurationParser(CommandLineParser(document))
    report = parser.parse(args.config.read_text(encoding="utf-8"))
    output = []
    for line in report.lines:
        if isinstance(line, ParsedCommand):
            output.append(
                {
                    "line_number": line.line_number,
                    "candidates": mapped_candidates(line, mapping, documentation)
                    if mapping is not None
                    else direct_candidates(line, documentation),
                }
            )
        elif line.kind == "error":
            output.append(
                {"line_number": line.line_number, "error": asdict(line.error)}
            )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
