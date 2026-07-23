"""Pareto comparison for parameter dispatch vectors."""

from __future__ import annotations

from collections import defaultdict

from .state import Candidate


class DispatchDominance:
    """Compare two paths without imposing an arbitrary lexicographic winner."""

    def dominates(self, left: tuple[int, ...], right: tuple[int, ...]) -> bool:
        compared = False
        strictly_narrower = False
        for first, second in zip(left, right, strict=False):
            compared = True
            if first > second:
                return False
            strictly_narrower = strictly_narrower or first < second
        return compared and strictly_narrower


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
                other != score and self.dominates(other, score)
                for other in scores
            )
        }
        return tuple(
            candidate
            for candidate in candidates
            if candidate.state.dispatch in accepted
        )

    def dominates(self, left: tuple[int, ...], right: tuple[int, ...]) -> bool:
        return self._dominance.dominates(left, right)
