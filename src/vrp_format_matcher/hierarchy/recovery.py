"""Recover only single, whole-format transitions for a prepared runtime catalog."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from vrp_format_matcher.models import PreparedMapping, PreparedPair

from .catalogs import MatchedCatalog
from .coverage import ViewCoverage
from .languages import FormatCoverage
from .models import DocumentTransition


@dataclass(frozen=True)
class RecoveredViews:
    views: dict[str, str]
    transitions: dict[str, str | None]
    unresolved: tuple[str, ...]


class HierarchyRecovery:
    """The reference hierarchy is complete; omitted reference switches mean stay.

    This is a structural reconstruction under the full-coverage criterion, not
    proof of device behaviour. Partial bindings never establish a transition.
    Explicit target transitions remain authoritative, including unmatched ones.
    """

    def __init__(
        self,
        device: MatchedCatalog,
        documentation: MatchedCatalog,
        mapping: PreparedMapping,
        languages: FormatCoverage,
    ) -> None:
        assert mapping.hierarchy is not None
        self._device = device
        self._documentation = documentation
        self._mapping = mapping
        self._languages = languages
        self._hierarchy = mapping.hierarchy
        self._declared = dict(mapping.hierarchy.declared_transitions)
        if device.sources.info["source"] == "documentation":
            # Both documentation inputs are prepared hierarchies in this API.
            self._declared = {}
            for identifier, record in device.records.items():
                transition = DocumentTransition.from_command(record, complete=True)
                if transition.kind != "unknown":
                    self._declared[identifier] = transition.target_view
        self._outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for identifier, target in self._declared.items():
            source = device.sources.entries[identifier].view
            assert source is not None
            if target is not None:
                self._outgoing[source].append((identifier, target))
        self._pairs: dict[tuple[str, str], list[PreparedPair]] = defaultdict(list)
        for identifier, match in mapping.devices.items():
            for pair in match.mappings:
                view = documentation.sources.entries[pair.document_id].view
                assert view is not None
                self._pairs[identifier, view].append(pair)

    def recover(self) -> RecoveredViews:
        views = ViewCoverage(
            self._device, self._documentation, self._mapping, self._languages
        ).unique_pairs()
        root = self._hierarchy.device_entry_view
        reference_root = self._hierarchy.documentation_entry_view
        # An entry view is an explicit anchor, even if its inventory differs.
        views = {
            left: right
            for left, right in views.items()
            if left != root and right != reference_root
        }
        anchored, rejected = self._follow_declared({root: reference_root})
        views = {v: d for v, d in views.items() if v not in rejected}
        views.update(anchored)
        views, _ = self._follow_declared(views, frozenset(anchored))

        by_document: dict[str, list[str]] = defaultdict(list)
        for left, right in views.items():
            by_document[right].append(left)
        transitions = dict(self._declared)
        unresolved = []
        for identifier, location in self._device.sources.entries.items():
            if identifier in transitions:
                continue
            reference = views.get(location.view or "")
            if reference is not None:
                effects = self._effects(identifier, reference)
                if len(effects) == 1:
                    target = next(iter(effects))
                    if target is None or target == reference:
                        transitions[identifier] = None
                        continue
                    candidates = by_document.get(target, [])
                    if len(candidates) == 1:
                        transitions[identifier] = candidates[0]
                        continue
            unresolved.append(identifier)
        return RecoveredViews(views, transitions, tuple(unresolved))

    def _effects(self, identifier: str, view: str) -> set[str | None]:
        pairs = tuple(
            pair
            for pair in self._pairs.get((identifier, view), ())
            if pair.stage != "prefix"
        )
        if not pairs or not self._languages.known(identifier):
            return set()
        effects = set()
        for pair in pairs:
            if not self._languages.known(pair.document_id):
                return set()
            transition = DocumentTransition.from_command(
                self._documentation.records[pair.document_id], complete=True
            )
            if transition.kind == "unknown":
                return set()
            target = transition.target_view
            effects.add(None if target == view else target)
        if len(effects) != 1 or any(
            pair.status in {"equivalent", "device_subset"} for pair in pairs
        ):
            return effects
        complete = self._languages.covers(
            identifier, (pair.document_id for pair in pairs)
        )
        return effects if complete is True else set()

    def _follow_declared(
        self, initial: dict[str, str], anchored: frozenset[str] = frozenset()
    ) -> tuple[dict[str, str], set[str]]:
        """Propagate established edges to a fixed point, including cycles.

        Conflicting paths invalidate a correspondence instead of selecting the
        first path. Restarting then removes correspondences derived from it.
        """
        blocked: set[str] = set()
        root = self._hierarchy.device_entry_view
        while True:
            views = {v: d for v, d in initial.items() if v not in blocked}
            pending = list(views)
            conflicts = set()
            for source in pending:
                for identifier, target in self._outgoing.get(source, ()):
                    if target in blocked:
                        continue
                    effects = self._effects(identifier, views[source])
                    if len(effects) != 1:
                        continue
                    documented = next(iter(effects))
                    if documented is None:
                        if target != source:
                            conflicts.add(target)
                        continue
                    if target in views and views[target] != documented:
                        conflicts.add(source if target in anchored else target)
                    elif target not in views:
                        views[target] = documented
                        pending.append(target)
            conflicts.discard(root)
            if not conflicts:
                return views, blocked
            blocked.update(conflicts)
