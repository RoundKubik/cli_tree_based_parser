"""Command-line entry points for checking patterns and parsing a file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vrp_parser_automaton import (
    CommandLineParser,
    ConfigurationParser,
    PatternCompilationError,
    PatternDocumentError,
)


class JsonOutput:
    """Render stable UTF-8 JSON to stdout."""

    def write(self, value: Any) -> None:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vrp-parser-automaton")
    subcommands = parser.add_subparsers(dest="action", required=True)

    check = subcommands.add_parser("check-patterns")
    check.add_argument("patterns", type=Path)

    parse = subcommands.add_parser("parse")
    parse.add_argument("--patterns", type=Path, required=True)
    parse.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output = JsonOutput()
    try:
        line_parser = CommandLineParser.from_json_file(arguments.patterns)
        if arguments.action == "check-patterns":
            output.write(
                {
                    "status": "ok",
                    "commands": line_parser.command_count,
                }
            )
            return 0

        report = ConfigurationParser(line_parser).parse(
            arguments.config.read_text(encoding="utf-8")
        )
        output.write(report.to_dict())
        return 1 if report.has_errors else 0
    except (
        OSError,
        PatternCompilationError,
        PatternDocumentError,
    ) as error:
        output.write({"status": "error", "message": str(error)})
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
