"""Early pruning for alternatives that share the same continuation."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser.parameters import ParameterStatus

from .deduplication import WalkStateIdentity
from .frontier import DispatchDominance
from .state import WalkState


@dataclass(frozen=True, slots=True)
class AlternativeOutcome:
    """A selected group branch and its resulting state."""

    alternative: int
    state: WalkState


class AlternativeStateFrontier:
    """Prune dominated branches before a set or repeat multiplies them."""

    def __init__(
        self,
        dominance: DispatchDominance | None = None,
        identity: WalkStateIdentity | None = None,
    ) -> None:
        self._dominance = dominance or DispatchDominance()
        self._identity = identity or WalkStateIdentity()

    def select(
        self,
        outcomes: tuple[AlternativeOutcome, ...],
        base: WalkState,
    ) -> tuple[AlternativeOutcome, ...]:
        positions = sorted({item.state.position for item in outcomes})
        return tuple(
            item
            for position in positions
            for item in self._at_position(
                tuple(
                    outcome
                    for outcome in outcomes
                    if outcome.state.position == position
                ),
                base,
            )
        )

    def _at_position(
        self,
        outcomes: tuple[AlternativeOutcome, ...],
        base: WalkState,
    ) -> tuple[AlternativeOutcome, ...]:
        without_new_na = tuple(
            item
            for item in outcomes
            if not self._has_new_not_applicable(item.state, base)
        )
        eligible = without_new_na or outcomes
        frontier = tuple(
            item
            for item in eligible
            if not any(
                other is not item
                and self._dominance.dominates(
                    self._dispatch(other.state, base),
                    self._dispatch(item.state, base),
                )
                for other in eligible
            )
        )
        unique = self._unique(frontier)
        if without_new_na:
            return unique
        # Every route is already non-applicable.  One representative is enough
        # to produce a useful validation error and avoids exponential repeats.
        return unique[:1]

    def _unique(
        self,
        outcomes: tuple[AlternativeOutcome, ...],
    ) -> tuple[AlternativeOutcome, ...]:
        selected: list[AlternativeOutcome] = []
        keys: set[tuple[object, ...]] = set()
        for outcome in outcomes:
            key = self._identity.key(outcome.state)
            if key not in keys:
                keys.add(key)
                selected.append(outcome)
        return tuple(selected)

    @staticmethod
    def _dispatch(
        state: WalkState,
        base: WalkState,
    ) -> tuple[int, ...]:
        return state.dispatch[len(base.dispatch) :]

    @staticmethod
    def _has_new_not_applicable(
        state: WalkState,
        base: WalkState,
    ) -> bool:
        return any(
            item.result.status is ParameterStatus.NOT_APPLICABLE
            for item in state.rejected[len(base.rejected) :]
        )
