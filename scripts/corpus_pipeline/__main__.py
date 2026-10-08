"""Extract annotations, recover documentation contexts or create mock formats."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .contract import RESPONSE_SCHEMA
from .extraction import Codex, assemble, extract_corpus
from .hierarchy.pipeline import recover_hierarchy
from .mock_device import build_mock
from .parameter_annotations import repair_types
from .source import Corpus, read_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    repair = subparsers.add_parser("repair-types")
    repair.add_argument("--input-dir", type=Path, required=True)
    repair.add_argument("--hierarchy-dir", type=Path, required=True)
    repair.add_argument("--corpus", type=Path, required=True)
    repair.add_argument("--output-dir", type=Path, required=True)
    hierarchy = subparsers.add_parser("recover-hierarchy")
    hierarchy.add_argument("--input-dir", type=Path, required=True)
    hierarchy.add_argument("--corpus", type=Path, required=True)
    hierarchy.add_argument("--output-dir", type=Path, required=True)
    hierarchy.add_argument("--entry-view", default="System view")
    hierarchy.add_argument("--global-view", action="append")
    hierarchy.add_argument("--model", required=True)
    hierarchy.add_argument("--codex", default="codex")
    hierarchy.add_argument("--workers", type=int, default=4)
    hierarchy.add_argument("--timeout", type=int, default=180)
    for name in ("extract", "mock-device"):
        command = subparsers.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--vendor", default="Huawei")
        command.add_argument("--device", default="Huawei VRP")
        command.add_argument("--model-type", required=True)
        command.add_argument("--software-version", required=True)
        command.add_argument("--entry-view", default="System view")
        if name == "mock-device":
            command.add_argument("--global-view", action="append")
        if name == "extract":
            command.add_argument("--codex", default="codex")
            command.add_argument("--model", required=True)
            command.add_argument("--workers", type=int, default=2)
            command.add_argument("--timeout", type=int, default=300)
            selection = command.add_mutually_exclusive_group()
            selection.add_argument("--pilot", type=int)
            selection.add_argument(
                "--page", action="append", help="Source JSON filename"
            )
            selection.add_argument(
                "--selection", type=Path, help="JSON array of source page filenames"
            )
    args = parser.parse_args()
    if args.command == "repair-types":
        report = repair_types(
            args.input_dir, args.hierarchy_dir, args.corpus, args.output_dir
        )
        print(
            json.dumps(
                {k: v for k, v in report.items() if not isinstance(v, list)}, indent=2
            )
        )
        return
    if args.command in {"extract", "recover-hierarchy"}:
        for name in ("workers", "timeout", "pilot"):
            value = getattr(args, name, None)
            if value is not None and value < 1:
                parser.error(f"--{name} must be positive")
        if shutil.which(args.codex) is None:
            parser.error("Codex executable not found; supply --codex /path/to/codex")
    if args.command == "recover-hierarchy":
        report = recover_hierarchy(
            args.input_dir.expanduser(),
            args.corpus.expanduser(),
            args.output_dir.expanduser(),
            Codex(args.codex, args.model, args.timeout),
            args.workers,
            args.entry_view,
            args.global_view,
        )
        print(json.dumps(report["summary"], indent=2))
        if report["summary"]["evidence_errors"]:
            raise SystemExit(1)
        return
    corpus = Corpus(args.corpus.expanduser())
    directory = args.output_dir.expanduser()
    header = {
        "schema_version": 1,
        "source": "documentation",
        "vendor": args.vendor,
        "device": args.device,
        "model_type": args.model_type,
        "software_version": args.software_version,
        "entry_view": args.entry_view,
    }
    if args.command == "mock-device":
        report = build_mock(
            corpus, header, directory, shared_views=tuple(args.global_view or ())
        )
    else:
        write_json(directory / "response.schema.json", RESPONSE_SCHEMA)
        selected = corpus.selected(args.pilot)
        pages = args.page
        if args.selection:
            pages = read_json(args.selection.expanduser())
            if (
                not isinstance(pages, list)
                or not pages
                or not all(isinstance(name, str) for name in pages)
            ):
                parser.error(
                    "--selection must contain a nonempty JSON array of filenames"
                )
        if pages:
            missing = set(pages) - corpus.pages.keys()
            if missing:
                parser.error(f"Unknown or excluded corpus pages: {sorted(missing)}")
            selected = [corpus.pages[name] for name in dict.fromkeys(pages)]
        agent = Codex(args.codex, args.model, args.timeout)
        results, errors = extract_corpus(
            corpus,
            selected,
            agent,
            directory,
            args.workers,
        )
        report = assemble(corpus, selected, results, errors, header, directory)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if not isinstance(value, (list, dict))
            },
            indent=2,
        )
    )
    if (
        report.get("pages_failed")
        or report.get("formats_failed")
        or report.get("page_errors")
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
