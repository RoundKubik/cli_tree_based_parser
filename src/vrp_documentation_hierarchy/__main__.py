"""Standalone documentation recovery CLI; no parser/matcher CLI modifications."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .recovery import recover_hierarchy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover documentation switch_to_view sets from examples"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report_path = args.report or args.output.with_suffix(".report.json")
    if len({p.resolve() for p in (args.catalog, args.output, report_path)}) != 3:
        parser.error("catalog, output and report must be different files")
    try:
        document = json.loads(args.catalog.read_text(encoding="utf-8"))
        result = recover_hierarchy(document, args.corpus)
        for path, data in ((args.output, result.catalog), (report_path, result.report)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (ValueError, OSError) as error:
        print(f"Recovery failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
