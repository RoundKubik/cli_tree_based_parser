"""Merge execution work only when the remaining automaton and registers agree."""

from __future__ import annotations

from collections import defaultdict

from vrp_parser_automaton.parameters import ParameterStatus

from .execution import Configuration
from .state import Candidate, WalkState


def dominates(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    pairs = tuple(zip(left, right, strict=False))
    return (
        bool(pairs) and all(a <= b for a, b in pairs) and any(a < b for a, b in pairs)
    )


def not_applicable(state: WalkState) -> bool:
    return any(
        item.result.status is ParameterStatus.NOT_APPLICABLE for item in state.rejected
    )


def value_key(state: WalkState) -> tuple[object, ...]:
    """Do not require custom validator values to be hashable."""
    return (
        state.parts,
        state.dispatch,
        state.parameter_led,
        tuple(
            (
                item.declaration.source,
                item.token.start,
                item.token.end,
                item.token.raw,
                repr(item.normalized),
                item.slot_id,
                item.iterations,
            )
            for item in state.parameters
        ),
        tuple(
            (
                item.declaration.source,
                item.token.start,
                item.token.raw,
                item.result.status,
                repr(item.result.issue),
            )
            for item in state.rejected
        ),
    )


class ConfigurationFrontier:
    def __init__(self) -> None:
        self.buckets: dict[tuple[object, ...], list[Configuration]] = {}

    def key(self, current: Configuration) -> tuple[object, ...]:
        return (current.instruction, current.state.position, current.frames)

    def add(self, current: Configuration) -> bool:
        key = self.key(current)
        bucket = self.buckets.setdefault(key, [])
        for existing in bucket:
            if self._covers(existing.state, current.state):
                return False
        bucket[:] = [
            old for old in bucket if not self._covers(current.state, old.state)
        ]
        bucket.append(current)
        return True

    def active(self, current: Configuration) -> bool:
        return any(item is current for item in self.buckets[self.key(current)])

    def _covers(self, left: WalkState, right: WalkState) -> bool:
        left_na, right_na = not_applicable(left), not_applicable(right)
        if left_na != right_na:
            return right_na
        # Multi-token readers may reach this position using fewer transitions.
        # Appending the common suffix can change dominance for unequal lengths.
        if len(left.dispatch) != len(right.dispatch):
            return False
        if dominates(left.dispatch, right.dispatch):
            return True
        if dominates(right.dispatch, left.dispatch):
            return False
        if left_na and right_na:
            return left.source_order <= right.source_order
        # Equivalent observable captures need one deterministic trace, rather
        # than a separate history for every empty optional-group route.
        return (
            value_key(left) == value_key(right)
            and left.source_order <= right.source_order
        )


class DispatchDominance:
    def dominates(self, left: tuple[int, ...], right: tuple[int, ...]) -> bool:
        return dominates(left, right)


class CandidateFrontier:
    """Retain candidates whose dispatch vector is not dominated."""

    def __init__(self, dominance: DispatchDominance | None = None) -> None:
        self._dominance = dominance or DispatchDominance()

    def select(
        self,
        candidates: tuple[Candidate, ...],
    ) -> tuple[Candidate, ...]:
        buckets: dict[tuple[int, ...], list[Candidate]] = defaultdict(list)
        for candidate in candidates:
            buckets[candidate.state.dispatch].append(candidate)

        scores = tuple(buckets)
        accepted = {
            score
            for score in scores
            if not any(
                other != score and self.dominates(other, score) for other in scores
            )
        }
        return tuple(
            candidate
            for candidate in candidates
            if candidate.state.dispatch in accepted
        )

    def dominates(self, left: tuple[int, ...], right: tuple[int, ...]) -> bool:
        return self._dominance.dominates(left, right)
