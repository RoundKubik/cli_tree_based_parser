from __future__ import annotations

import json
from pathlib import Path

from vrp_parser import CommandLineParser

DATA = Path(__file__).parents[1] / "data" / "commands.json"


def test_data_directory_contains_only_runtime_commands() -> None:
    files = sorted(path.name for path in DATA.parent.iterdir() if path.is_file())

    assert files == ["commands.json"]


def test_bundled_commands_are_unique_supported_and_compilable() -> None:
    document = json.loads(DATA.read_text(encoding="utf-8"))
    commands = document["commands"]

    assert commands[:2] == ["#", "TEXT<1-4096>"]
    assert len(commands) == len(set(commands))
    assert all("X.X.X.X" not in item for item in commands)
    assert all("X:X::X:X" not in item for item in commands)
    assert all(
        "TEXT<" not in item or item == "TEXT<1-4096>"
        for item in commands
    )
    assert all("}*" not in item and "]*" not in item for item in commands)

    parser = CommandLineParser(document)
    assert parser.command_count == len(commands)
