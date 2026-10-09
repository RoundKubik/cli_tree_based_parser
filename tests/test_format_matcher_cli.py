"""The public CLI rejects invalid inputs without writing a mapping."""

import json

import pytest

from vrp_format_matcher.cli import main


@pytest.mark.parametrize(
    ("patterns", "documents", "message"),
    [
        ("{", "[]", "Expecting property name"),
        ("[]", "[]", "target input must be a catalog"),
        ("{}", "[]", "commands array"),
        ('{"commands": ["c"]}', "null", "documentation input must be a catalog"),
        (
            '{"commands": ["c INTEGER<1-9>"]}',
            '[{"format": "c ["}]',
            "group alternatives cannot be empty",
        ),
    ],
)
def test_invalid_input_returns_two_without_creating_output(
    tmp_path, capsys, patterns, documents, message
):
    target = tmp_path / "device.json"
    docs = tmp_path / "docs.json"
    output = tmp_path / "mapping.json"
    target.write_text(patterns)
    docs.write_text(documents)
    assert (
        main(
            ["--patterns", str(target), "--documents", str(docs), "--save", str(output)]
        )
        == 2
    )
    assert message in capsys.readouterr().err
    assert not output.exists()


def test_file_error_returns_two(tmp_path, capsys):
    assert (
        main(
            ["--patterns", str(tmp_path / "missing.json"), "--documents", "unused.json"]
        )
        == 2
    )
    assert "missing.json" in capsys.readouterr().err


def test_completed_unmatched_run_is_successful_and_writes_pretty_json(tmp_path, capsys):
    target = tmp_path / "device.json"
    docs = tmp_path / "docs.json"
    output = tmp_path / "mapping.json"
    target.write_text(json.dumps({"commands": ["alpha"]}))
    docs.write_text(json.dumps([{"format": "beta"}]))
    assert (
        main(
            [
                "--patterns",
                str(target),
                "--documents",
                str(docs),
                "--save",
                str(output),
                "--summary",
            ]
        )
        == 0
    )
    saved = output.read_text()
    data = json.loads(saved)
    assert saved == json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    assert next(iter(data["devices"].values()))["status"] == "unmatched"
    assert "PARAMETER MAPPING:" not in capsys.readouterr().out
