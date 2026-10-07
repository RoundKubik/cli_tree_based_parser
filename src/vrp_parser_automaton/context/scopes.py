"""Check an offline scope against a runtime trace, without reparsing its values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vrp_parser_automaton.errors import PatternDocumentError
from vrp_parser_automaton.results import ParameterValue, PatternMatch
from vrp_parser_automaton.text import ascii_lower


@dataclass(frozen=True)
class ScopeArc:
    target: int
    label: str | None
    slot_id: str | None = None
    iterations: tuple[tuple[str, int], ...] = ()


class MappingScope:
    def __init__(self, source: Mapping[str, Any]) -> None:
        try:
            edges = source["edges"]
            if not isinstance(edges, list) or not edges:
                raise ValueError("edges")
            self.start, self.final = source["start"], source["final"]
            for state in (self.start, self.final):
                if type(state) is not int or not 0 <= state < len(edges):
                    raise ValueError("state")
            rows = []
            for row in edges:
                arcs = []
                for arc in row:
                    target, label = arc["target"], arc["label"]
                    if type(target) is not int or not 0 <= target < len(edges):
                        raise ValueError("target")
                    if (
                        label is not None
                        and label != "P"
                        and not (isinstance(label, str) and label.startswith("K:"))
                    ):
                        raise ValueError("label")
                    capture = arc["device"] if label == "P" else {}
                    slot = capture.get("slot_id")
                    iterations = tuple(
                        tuple(pair) for pair in capture.get("iterations", ())
                    )
                    if label == "P" and not isinstance(slot, str):
                        raise ValueError("slot")
                    if any(
                        len(pair) != 2
                        or not isinstance(pair[0], str)
                        or type(pair[1]) is not int
                        for pair in iterations
                    ):
                        raise ValueError("iterations")
                    arcs.append(ScopeArc(target, label, slot, iterations))
                rows.append(tuple(arcs))
            self._edges = tuple(rows)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise PatternDocumentError("invalid mapping scope graph") from error

    def accepts(self, raw: str, match: PatternMatch) -> bool:
        symbols: list[tuple[str, ParameterValue | None]] = []
        offset = 0
        for captured in match.parameters:
            symbols.extend(
                ("K:" + ascii_lower(word), None)
                for word in raw[offset : captured.span.start].split()
            )
            symbols.append(("P", captured))
            offset = captured.span.end
        symbols.extend(
            ("K:" + ascii_lower(word), None) for word in raw[offset:].split()
        )
        active = self._closure({self.start})
        for label, parameter in symbols:
            active = self._closure(
                {
                    arc.target
                    for state in active
                    for arc in self._edges[state]
                    if arc.label == label
                    and (
                        parameter is None
                        or (
                            arc.slot_id == parameter.slot_id
                            and arc.iterations == parameter.iterations
                        )
                    )
                }
            )
            if not active:
                return False
        return self.final in active

    def _closure(self, states: set[int]) -> set[int]:
        pending = list(states)
        for state in pending:
            for arc in self._edges[state]:
                if arc.label is None and arc.target not in states:
                    states.add(arc.target)
                    pending.append(arc.target)
        return states
