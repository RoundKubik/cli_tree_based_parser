"""Reproduce corpus parsing and matching checks without changing source catalogs."""

import argparse
import json
import resource
import signal
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path

from vrp_format_matcher import FormatMatcher
from vrp_format_matcher.documents.catalog import DocumentSource
from vrp_format_matcher.preparation.sources import CommandCatalog
from vrp_parser_automaton import CommandLineParser, ConfigurationParser, ParsedCommand

from .hierarchy_diagnostics import hierarchy_diagnostics
from .source import read_json, write_json


def compatible_documentation(document):
    """Keep the matcher contract; record every excluded source occurrence."""
    selected = deepcopy(document)
    exclusions, origins = [], {}
    for view, commands in document["views"].items():
        selected["views"][view] = []
        origins[view] = []
        for index, command in enumerate(commands):
            try:
                DocumentSource(command, "check").parsed()
            except ValueError as error:
                exclusions.append({"view": view, "index": index, "error": str(error)})
            else:
                origins[view].append(index)
                selected["views"][view].append(deepcopy(command))
    return selected, {"excluded": exclusions, "origins": origins}


def parse_catalog(args):
    document = read_json(args.catalog)
    start = time.monotonic()
    parser = CommandLineParser(document, context_mode=args.context_mode)
    compilation = time.monotonic() - start
    print(
        f"Compiled {parser.command_count} format occurrences in {compilation:.3f}s",
        flush=True,
    )
    engine = ConfigurationParser(parser, contextual=False if args.flat else None)
    results = []
    for case in read_json(args.cases):
        start = time.monotonic()
        parsed = engine.parse("\n".join(case["lines"]))
        commands = [line for line in parsed.lines if isinstance(line, ParsedCommand)]
        mismatches = []
        if document["source"] == "documentation" and not args.flat:
            for line in parsed.lines:
                expected = case.get("views", {}).get(str(line.line_number), ...)
                if (
                    args.context_mode == "system"
                    and expected is not ...
                    and expected != document.get("entry_view")
                ):
                    expected = None
                if expected is not ... and getattr(line, "view", None) != expected:
                    mismatches.append(
                        {
                            "line": line.line_number,
                            "expected": expected,
                            "actual": getattr(line, "view", None),
                        }
                    )
        write_json(args.output_dir / f"{case['name']}.json", parsed.to_dict())
        results.append(
            {
                "case": case["name"],
                "seconds": time.monotonic() - start,
                "recognized": len(commands),
                "errors": parsed.summary.errors,
                "unresolved": parsed.summary.unresolved,
                "ambiguous": parsed.summary.ambiguous,
                "known_context": sum(c.view is not None for c in commands),
                "context_issues": dict(
                    Counter(c.context_issue.code for c in commands if c.context_issue)
                ),
                "parameters_without_slots": sum(
                    not p.slot_id
                    for c in commands
                    for m in c.matches
                    for p in m.parameters
                ),
                "unexpected_views": mismatches,
            }
        )
    return {
        "catalog": str(args.catalog),
        "format_occurrences": parser.command_count,
        "flat": args.flat,
        "context_mode": args.context_mode,
        "compile_seconds": compilation,
        "cases": results,
    }


def match_catalogs(args):
    document = read_json(args.documentation)
    device = read_json(args.device)
    # Validate the original contract before performing any large AST compilation.
    commands = CommandCatalog.read(
        document, validate_transitions=args.context_mode == "hierarchy"
    ).commands
    errors = []
    for index, command in enumerate(commands):
        try:
            DocumentSource(command, str(index)).parsed()
        except ValueError as error:
            errors.append({"index": index, "error": str(error)})
    write_json(args.output_dir / "input_validation.json", {"errors": errors})
    if errors and not args.compatible_only:
        return {"status": "invalid_documentation", "invalid_occurrences": len(errors)}
    if args.compatible_only:
        document, projection = compatible_documentation(document)
        write_json(args.output_dir / "documentation_compatible.json", document)
        write_json(args.output_dir / "documentation_selection.json", projection)
    last_update = 0.0

    def progress(event):
        nonlocal last_update
        now = time.monotonic()
        if now - last_update > 10 or event.devices_done == event.devices_total:
            print(
                f"{event.stage}: {event.devices_done}/{event.devices_total}, "
                f"{event.pairs_prepared} pairs",
                flush=True,
            )
            last_update = now

    start = time.monotonic()
    prepared = FormatMatcher(context_mode=args.context_mode).prepare_catalogs(
        device, document, on_progress=progress
    )
    matching_seconds = time.monotonic() - start
    mapping = prepared.mapping
    if mapping.hierarchy is not None:
        write_json(
            args.output_dir / "hierarchy_report.json",
            hierarchy_diagnostics(prepared, document),
        )
    statuses = Counter(d.status for d in mapping.devices.values())
    stages, relations = Counter(), Counter()
    bindings = 0
    for device_result in mapping.devices.values():
        for pair in device_result.mappings:
            stages[pair.stage] += 1
            relations[pair.status] += 1
            bindings += len(pair.bindings)
    write_json(args.output_dir / "runtime_catalog.json", prepared.catalog)
    # Keep every binding and applicability graph in an indented JSON document.
    write_json(args.output_dir / "mapping.json", mapping.to_dict())
    hierarchy = mapping.hierarchy
    return {
        "status": "completed",
        "context_mode": args.context_mode,
        "matching_seconds": matching_seconds,
        "compatible_subset": args.compatible_only,
        "invalid_documentation_occurrences": len(errors),
        "device_occurrences": len(mapping.devices),
        "device_statuses": dict(statuses),
        "pair_stages": dict(stages),
        "pair_relations": dict(relations),
        "bindings": bindings,
        "unresolved_transitions": len(prepared.unresolved),
        "resolved_views": hierarchy.resolved_views if hierarchy else {},
        "resolved_targets": {
            name: target.device_view
            for name, target in hierarchy.targets.items()
            if target.status == "resolved"
        }
        if hierarchy
        else {},
        "mapping_bytes": (args.output_dir / "mapping.json").stat().st_size,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["parser", "matcher"])
    parser.add_argument("--catalog", type=Path)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).with_name("samples") / "configuration_cases.json",
    )
    parser.add_argument("--flat", action="store_true")
    parser.add_argument(
        "--context-mode", choices=("system", "hierarchy"), default="system"
    )
    parser.add_argument("--device", type=Path)
    parser.add_argument("--documentation", type=Path)
    parser.add_argument("--compatible-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--memory-mb", type=int, default=1536)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if min(args.memory_mb, args.seconds) <= 0:
        parser.error("Resource limits must be positive")
    if args.mode == "parser" and args.catalog is None:
        parser.error("parser requires --catalog")
    if args.mode == "matcher" and (args.device is None or args.documentation is None):
        parser.error("matcher requires --device and --documentation")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    resource.setrlimit(resource.RLIMIT_AS, (args.memory_mb * 1024**2,) * 2)

    def expired(*_):
        raise TimeoutError("Experiment time limit reached; the result is incomplete")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.seconds)
    start = time.monotonic()
    try:
        report = parse_catalog(args) if args.mode == "parser" else match_catalogs(args)
    except (ValueError, MemoryError, TimeoutError) as error:
        report = {"status": "failed", "error": type(error).__name__ + ": " + str(error)}
    finally:
        signal.alarm(0)
    report.update(
        seconds=time.monotonic() - start,
        peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    )
    write_json(args.output_dir / "report.json", report)
    print(json.dumps(report, indent=2))
    if report.get("status") in {"failed", "invalid_documentation"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
