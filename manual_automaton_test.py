"""Run the standalone automaton parser against examples or a device catalogue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_parser_automaton import CommandLineParser, ConfigurationParser  # noqa: E402

STATIC_ROUTE = (
    "ip route-static X.X.X.X { X.X.X.X | INTEGER<0-32> } X.X.X.X "
    "[ recursive-lookup host-route [ arp-vlink-only ] ] "
    "[ preference INTEGER<1-255> | tag INTEGER<1-4294967295> ] * "
    "[ [ bfd enable | track { bfd-session STRING<1-15> | "
    "nqa STRING<1-32> STRING<1-32> } ] [ inherit-cost ] | permanent ] "
    "[ arp-detect { STRING<1-64> | ENUM{Vlanif,GigabitEthernet} STRING<1-32> } ] "
    "[ inter-protocol-ecmp ] [ description TEXT<1-80> ]"
)

EXAMPLES = {
    "route-static": (
        STATIC_ROUTE,
        (
            "ip route-static 10.0.0.0 24 192.0.2.1",
            "ip route-static 10.0.0.0 24 192.0.2.1 "
            "tag 7 preference 10 permanent description primary route",
            "ip route-static 10.0.0.0 24 192.0.2.1 bfd enable inherit-cost",
            "ip route-static 10.0.0.0 24 192.0.2.1 tag 1 tag 2",
        ),
    ),
    "wide-set": (
        "command [ " + " | ".join(f"k{i} INTEGER<1-100>" for i in range(24)) + " ] *",
        ("command k23 24 k0 1 k12 13", "command", "command k0 1 k0 2"),
    ),
    "large-repeat": (
        "command INTEGER<1-100> &<1-100000>",
        ("command 1 2 3", "command 101"),
    ),
    "optional-chain": (
        "command " + " ".join(f"[ k{i} ]" for i in range(40)) + " end",
        ("command end", "command k0 k15 k39 end", "command k39 k0 end"),
    ),
}


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--case", choices=EXAMPLES, default="route-static")
    arguments.add_argument(
        "--patterns", type=Path, help="JSON file with a commands array"
    )
    arguments.add_argument(
        "--line", action="append", help="Concrete command; repeat for several commands"
    )
    args = arguments.parse_args()
    pattern, examples = EXAMPLES[args.case]
    if args.patterns:
        parser = CommandLineParser.from_json_file(args.patterns)
    else:
        parser = CommandLineParser({"commands": [pattern]})
    lines = args.line or examples
    print(f"PATTERNS: {parser.command_count}")
    print(f"AUTOMATON STATES: {len(parser.automaton.states)}")
    report = ConfigurationParser(parser).parse("\n".join(lines))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
