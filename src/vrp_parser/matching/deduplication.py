"""Stable duplicate removal for traversal values with arbitrary normalization."""

from __future__ import annotations

from .state import Candidate, WalkState


class WalkStateIdentity:
    """Build a hashable identity without constraining plugin normalized values."""

    def key(self, state: WalkState) -> tuple[object, ...]:
        return (
            state.position,
            state.parts,
            tuple(
                (
                    item.declaration.source,
                    item.token.raw,
                    repr(item.normalized),
                )
                for item in state.parameters
            ),
            tuple(
                (
                    item.declaration.source,
                    item.token.raw,
                    item.result.status,
                    repr(item.result.issue),
                )
                for item in state.rejected
            ),
            state.dispatch,
            state.source_order,
            state.trace,
        )


class WalkStateSet:
    """Keep the first state for each stable identity."""

    def __init__(self, identity: WalkStateIdentity | None = None) -> None:
        self._identity = identity or WalkStateIdentity()

    def unique(self, states: tuple[WalkState, ...]) -> tuple[WalkState, ...]:
        unique: list[WalkState] = []
        keys: set[tuple[object, ...]] = set()
        for state in states:
            key = self._identity.key(state)
            if key not in keys:
                keys.add(key)
                unique.append(state)
        return tuple(unique)


class CandidateSet:
    """Keep duplicate graph traversals out of final resolution."""

    def __init__(self, identity: WalkStateIdentity | None = None) -> None:
        self._identity = identity or WalkStateIdentity()

    def unique(self, candidates: list[Candidate]) -> tuple[Candidate, ...]:
        unique: list[Candidate] = []
        keys: set[tuple[object, ...]] = set()
        for candidate in candidates:
            key = (candidate.route_id, *self._identity.key(candidate.state))
            if key not in keys:
                keys.add(key)
                unique.append(candidate)
        return tuple(unique)
