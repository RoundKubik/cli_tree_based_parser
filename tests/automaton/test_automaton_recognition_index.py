"""Prefix selection and duplicate reuse preserve the complete parsing result."""

from copy import deepcopy

import pytest

from vrp_parser_automaton import CommandLineParser, ConfigurationParser
from vrp_parser_automaton.runtime import matcher
from vrp_parser_automaton.runtime.recognition import CommandRecognition
from vrp_parser_automaton.text import ascii_lower


class OriginalStarts:
    """The previous first-keyword selector, retained here as a behavioral oracle."""

    def __init__(self, automaton, excluded_starts):
        self.automaton = automaton
        self.excluded = excluded_starts

    def starts(self, command, diagnostics):
        first = command.token(0)
        literals = self.automaton.literal_starts.get(ascii_lower(first.raw), ())
        return tuple(
            start
            for start in dict.fromkeys((*literals, *self.automaton.parameter_starts))
            if start not in self.excluded
        )

    def expand(self, candidates):
        return candidates


@pytest.mark.parametrize("layout", ["flat", "system", "hierarchy"])
def test_full_results_match_unindexed_recognition(monkeypatch, layout):
    formats = [
        "set IP address INTEGER<1-9>",
        "set ip route INTEGER<1-9>",
        "set ip route INTEGER<1-9>",
        "set STRING<1-9> [ INTEGER<1-9> ]",
        "[ set ] ip [ route ] INTEGER<1-9>",
        "{ set ip | set address } INTEGER<1-9>",
        "set ip route INTEGER<1-9> &<1-3>",
        "set [ alpha INTEGER<1-9> | beta INTEGER<1-9> ] *",
        "set [ alpha INTEGER<1-9> | beta INTEGER<1-9> ] *",
        "set [ INTEGER<1-9> ] [ INTEGER<1-9> ]",
        "STRING<1-9> ip route INTEGER<1-9>",
        "other INTEGER<1-9>",
    ]
    lines = [
        "set ip route 3",
        "SET\tIP\troute 4",
        "set ip route 99",
        "set",
        "set ip",
        "set ip route",
        "set ip missing",
        "set unknown 4",
        "set ip route 2 3",
        "set beta 3 alpha 2",
        "set 3",
        "ip 2",
        "not known",
        "set beta 2 beta 3",
        "set ip route 3 extra",
        "set ip route invalid",
        "",
    ]
    if layout == "flat":
        catalog = {"commands": formats}
        mode = "system"
    else:
        catalog = {
            "type": "grouped",
            "entry_view": "root",
            "views": {
                "root": [{"format": "enter", "switch_to_view": "a"}],
                "a": formats,
                "b": formats,
            },
        }
        lines = ["enter", *(" " + line for line in lines), "other 4"]
        mode = layout
    content = "\n".join(lines)
    before = deepcopy(catalog)
    with monkeypatch.context() as patch:
        patch.setattr(matcher, "RecognitionIndex", OriginalStarts)
        expected = (
            ConfigurationParser(CommandLineParser(catalog, context_mode=mode))
            .parse(content)
            .to_dict()
        )
    actual = (
        ConfigurationParser(CommandLineParser(catalog, context_mode=mode))
        .parse(content)
        .to_dict()
    )
    assert actual == expected
    assert catalog == before


def test_many_prefixes_and_view_copies_execute_only_matching_programs(monkeypatch):
    formats = [f"ip setting option{i} INTEGER<1-9>" for i in range(1000)]
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "root",
            "views": {"root": ["enter"], **{str(i): formats for i in range(10)}},
        }
    )
    calls = []
    original = CommandRecognition.candidates

    def candidates(self):
        calls.append(len(self.starts))
        return original(self)

    monkeypatch.setattr(CommandRecognition, "candidates", candidates)
    result = parser.parse(" ip setting option987 5")
    assert calls == [1]
    assert len(result.matches) == 10
    assert len({m.pattern_id for m in result.matches}) == 10
    assert all(m.parameters[0].slot_id == "p:21" for m in result.matches)


def test_same_spelling_with_different_annotations_remains_distinct():
    def record(type_id):
        return {
            "format": "value <id>",
            "parameter_types": [{"parameter_name": "id", "parameter_type": type_id}],
        }

    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "root",
            "views": {
                "root": ["enter"],
                "a": [record("integer")],
                "b": [record("string")],
                "c": [record("integer")],
            },
        }
    )
    sources = parser.automaton.patterns
    assert sources[1].ast is sources[3].ast
    assert sources[1].ast is not sources[2].ast
    number = parser.parse(" value 5")
    assert [m.parameters[0].normalized for m in number.matches] == [5, "5", 5]
    assert [m.pattern_index for m in parser.parse(" value word").matches] == [2]


def test_repeated_vlan_pattern_runs_once_and_keeps_all_source_slots(monkeypatch):
    pattern = (
        "undo port trunk allow-pass vlan { "
        "{ INTEGER<1-4095> [ to INTEGER<1-4095> ] } &<1-10> | all }"
    )
    parser = CommandLineParser(
        {
            "type": "grouped",
            "entry_view": "root",
            "views": {"root": ["enter"], **{str(i): [pattern] for i in range(50)}},
        }
    )
    calls = []
    original = CommandRecognition.candidates

    def candidates(self):
        calls.append(len(self.starts))
        return original(self)

    monkeypatch.setattr(CommandRecognition, "candidates", candidates)
    result = parser.parse(" undo port trunk allow-pass vlan 10 to 20 30")
    assert calls == [1]
    assert result.view is None
    assert len(result.matches) == 50
    captures = result.primary_match.parameters
    assert [value.normalized for value in captures] == [10, 20, 30]
    assert captures[0].slot_id == captures[2].slot_id
    assert captures[0].iterations != captures[2].iterations
    assert all(match.parameters == captures for match in result.matches)
