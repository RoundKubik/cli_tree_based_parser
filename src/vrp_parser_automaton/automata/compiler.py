"""Parse a source catalogue, then compile its ASTs into one automaton."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from vrp_parser_automaton.parameters import ParameterTypeRegistry

from .building import AutomatonBuilder
from .model import CommandAutomaton
from .sources import PatternSources


@dataclass(frozen=True)
class PatternCompiler:
    parameter_types: ParameterTypeRegistry

    def compile(
        self,
        commands: tuple[str, ...],
        *,
        annotations: tuple[Mapping[str, str], ...] = (),
    ) -> CommandAutomaton:
        sources = PatternSources(commands, self.parameter_types, annotations).parsed()
        return AutomatonBuilder().build(sources)
