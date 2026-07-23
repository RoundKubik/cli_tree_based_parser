from __future__ import annotations

import json
from pathlib import Path

import pytest

from vrp_parser.cli import main


def _stdout_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def test_check_patterns_prints_compiled_command_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patterns = tmp_path / "patterns.json"
    patterns.write_text(
        '{"commands": ["display clock", "#"]}',
        encoding="utf-8",
    )

    exit_code = main(["check-patterns", str(patterns)])

    assert exit_code == 0
    assert _stdout_json(capsys) == {"status": "ok", "commands": 2}


def test_parse_prints_json_report_and_returns_zero_without_line_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patterns = tmp_path / "patterns.json"
    config = tmp_path / "config.cfg"
    patterns.write_text(
        '{"commands": ["interface STRING<1-63>"]}',
        encoding="utf-8",
    )
    config.write_text("interface Vlanif100\n", encoding="utf-8")

    exit_code = main(
        [
            "parse",
            "--patterns",
            str(patterns),
            "--config",
            str(config),
        ]
    )
    payload = _stdout_json(capsys)

    assert exit_code == 0
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["commands"] == 1
    lines = payload["lines"]
    assert isinstance(lines, list)
    primary = lines[0]["primary_match"]
    assert primary["original_pattern"] == "interface STRING<1-63>"


def test_parse_returns_one_when_configuration_contains_an_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patterns = tmp_path / "patterns.json"
    config = tmp_path / "config.cfg"
    patterns.write_text(
        '{"commands": ["display clock"]}',
        encoding="utf-8",
    )
    config.write_text("unknown command\n", encoding="utf-8")

    exit_code = main(
        [
            "parse",
            "--patterns",
            str(patterns),
            "--config",
            str(config),
        ]
    )
    payload = _stdout_json(capsys)

    assert exit_code == 1
    lines = payload["lines"]
    assert isinstance(lines, list)
    assert lines[0]["error"]["code"] == "unknown_command"


def test_invalid_pattern_document_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    patterns = tmp_path / "patterns.json"
    patterns.write_text('{"commands": ["[ broken"]}', encoding="utf-8")

    exit_code = main(["check-patterns", str(patterns)])
    payload = _stdout_json(capsys)

    assert exit_code == 2
    assert payload["status"] == "error"
