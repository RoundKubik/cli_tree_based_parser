"""Resolve full automaton candidates without hiding real ambiguity."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.diagnostics.validation import ValidationErrorFactory
from vrp_parser_automaton.results import MatchStatus, ParseError, PatternMatch

from .frontier import CandidateFrontier
from .matches import PatternMatchSet
from .state import Candidate


@dataclass(frozen=True, slots=True)
class ResolvedMatch:
    """Internal success consumed by the public line parser."""

    status: MatchStatus
    primary_match: PatternMatch
    alternative_matches: tuple[PatternMatch, ...]


class MatchResolver:
    """Choose the Pareto-best valid candidates and retain remaining ties."""

    def __init__(
        self,
        frontier: CandidateFrontier | None = None,
        matches: PatternMatchSet | None = None,
        validation_errors: ValidationErrorFactory | None = None,
    ) -> None:
        self._frontier = frontier or CandidateFrontier()
        self._matches = matches or PatternMatchSet()
        self._validation_errors = validation_errors or ValidationErrorFactory()

    def resolve(
        self,
        candidates: tuple[Candidate, ...],
        automaton: CommandAutomaton,
        *,
        span_offset: int,
    ) -> ResolvedMatch | ParseError:
        valid = tuple(item for item in candidates if not item.state.rejected)
        invalid = tuple(item for item in candidates if item.state.rejected)

        best_valid = self._frontier.select(valid)
        invalid_frontier = self._applicable_frontier(invalid)
        unblocked = self._unblocked(best_valid, invalid_frontier)

        if best_valid and not unblocked:
            return self._validation_errors.create(
                self._blockers(invalid_frontier, best_valid),
                automaton,
                span_offset=span_offset,
            )
        if not unblocked:
            return self._validation_errors.create(
                invalid_frontier or self._frontier.select(invalid),
                automaton,
                span_offset=span_offset,
            )

        matches = self._matches.create(
            unblocked,
            automaton,
            span_offset=span_offset,
        )
        return ResolvedMatch(
            status=self._status(matches),
            primary_match=matches[0],
            alternative_matches=matches[1:],
        )

    def _applicable_frontier(
        self,
        invalid: tuple[Candidate, ...],
    ) -> tuple[Candidate, ...]:
        applicable = tuple(
            item
            for item in invalid
            if item.state.rejected
            and all(rejection.applicable for rejection in item.state.rejected)
        )
        return self._frontier.select(applicable)

    def _unblocked(
        self,
        valid: tuple[Candidate, ...],
        invalid: tuple[Candidate, ...],
    ) -> tuple[Candidate, ...]:
        return tuple(
            candidate
            for candidate in valid
            if not any(
                self._frontier.dominates(
                    rejected.state.dispatch,
                    candidate.state.dispatch,
                )
                for rejected in invalid
            )
        )

    def _blockers(
        self,
        invalid: tuple[Candidate, ...],
        valid: tuple[Candidate, ...],
    ) -> tuple[Candidate, ...]:
        return tuple(
            rejected
            for rejected in invalid
            if any(
                self._frontier.dominates(
                    rejected.state.dispatch,
                    candidate.state.dispatch,
                )
                for candidate in valid
            )
        )

    def _status(self, matches: tuple[PatternMatch, ...]) -> MatchStatus:
        if len(matches) == 1:
            return MatchStatus.UNIQUE
        if self._matches.equivalent(matches):
            return MatchStatus.EQUIVALENT
        return MatchStatus.AMBIGUOUS
