"""One recognition run owns its worklist; transitions own language semantics."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.diagnostics.progress import MatchDiagnostics
from vrp_parser_automaton.parameters import ParameterTypeRegistry
from vrp_parser_automaton.patterns import Literal, Parameter
from vrp_parser_automaton.text import CommandText, ascii_lower

from .atoms import LiteralTransition, ParameterTransition
from .execution import Configuration, ControlFlow
from .state import Candidate, WalkState
from .worklist import PendingConfigurations


@dataclass(frozen=True)
class CommandRecognition:
    automaton: CommandAutomaton
    parameter_types: ParameterTypeRegistry
    command: CommandText
    diagnostics: MatchDiagnostics

    def candidates(self) -> tuple[Candidate, ...]:
        pending = PendingConfigurations()
        for start in self._starts():
            pending.offer(Configuration(start, WalkState()))
        candidates: list[Candidate] = []
        while pending.remaining():
            consuming = []
            for current in pending.at_next_position():
                node = self.automaton.states[current.instruction]
                if node.kind == "atom":
                    consuming.append(current)
                elif node.kind == "accept":
                    self._accept(current, node.pattern_index, candidates)
                else:
                    for following in ControlFlow().follow(node, current):
                        pending.offer(following)
            # Resolve all epsilon paths before executing the next input atom.
            for current in consuming:
                if pending.active(current):
                    for following in self._consume(current):
                        pending.offer(following)
        return tuple(candidates)

    def _starts(self) -> tuple[int, ...]:
        first = self.command.token(0)
        assert first is not None
        literals = self.automaton.literal_starts.get(ascii_lower(first.raw), ())
        return tuple(dict.fromkeys((*literals, *self.automaton.parameter_starts)))

    def _accept(
        self, current: Configuration, pattern_index: int, candidates: list[Candidate]
    ) -> None:
        position = current.state.position
        if self.command.at_end(position):
            candidates.append(Candidate(pattern_index, current.state))
        else:
            self.diagnostics.record(
                self.command.skip_space(position),
                "end of command",
                parameter_led=current.state.parameter_led,
            )

    def _consume(self, current: Configuration) -> tuple[Configuration, ...]:
        node = self.automaton.states[current.instruction]
        atom = node.atom
        if isinstance(atom, Literal):
            results = LiteralTransition().match(
                atom, current.state, self.command, self.diagnostics
            )
        else:
            assert isinstance(atom, Parameter)
            results = ParameterTransition(self.parameter_types).match(
                atom,
                current.state,
                self.command,
                self.diagnostics,
                path=current.path(node.path),
            )
        return tuple(current.at(node.target, result) for result in results)
