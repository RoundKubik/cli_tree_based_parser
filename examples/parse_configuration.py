"""Compile one flat/grouped catalog, parse a configuration and save its report."""

import argparse
import json
from pathlib import Path

from vrp_parser_automaton import CommandLineParser, ConfigurationParser


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--patterns", type=Path, required=True)
    arguments.add_argument("--config", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument(
        "--context-mode", choices=("system", "hierarchy"), default="system"
    )
    args = arguments.parse_args()

    line_parser = CommandLineParser.from_json_file(
        args.patterns, context_mode=args.context_mode
    )
    parser = ConfigurationParser(line_parser)
    report = parser.parse(args.config.read_text(encoding="utf-8"))

    args.output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
