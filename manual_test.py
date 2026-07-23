"""Minimal manual test harness for the Huawei VRP parser.

Run it from the project root without installing the package:

    python3 manual_test.py

To experiment, change PATTERN_DOCUMENT and the input passed in main(). The
project's complete command set is stored only in data/commands.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow this file to run directly without `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_parser import CommandLineParser, ConfigurationParser  # noqa: E402

PATTERN_DOCUMENT = {
    "commands": [
        "#",
        "TEXT<1-4096>",
        "description TEXT<1-4096>",
        "interface STRING<1-63>",
        "peer X.X.X.X",
        "peer X:X::X:X",
        "network X:X::X:X/M",
        "mode ENUM{fast,safe} [ optional ]",
        "interface { STRING<1-63> | ENUM{Vbdif,Vlanif} STRING<1-63> }",
        "preference INTEGER<1-15>",
        "show { up | down }",
        "show up",
        "value INTEGER<1-10>",
        "value HEX<1-A>",
        "features { alpha | beta | gamma } *",
        "access-operation { { create | read } * | * }",
    ]
}

config = """
interface Eth-Trunk1
    description
"""


def main() -> None:
    parser = CommandLineParser(PATTERN_DOCUMENT)
    configuration_parser = ConfigurationParser(parser)
    result = configuration_parser.parse(config)

    for line in result.lines:
        print(line)
        print()


if __name__ == "__main__":
    main()
