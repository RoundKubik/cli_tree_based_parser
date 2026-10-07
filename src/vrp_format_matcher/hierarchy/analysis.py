"""Collect hierarchy evidence from already computed, scoped command matches."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from vrp_format_matcher.models import FormatError, PreparedMapping, PreparedPair

from .catalogs import MatchedCatalog
from .models import (
    CommandLink,
    DocumentTransition,
    HierarchyEvidence,
    PreparedHierarchy,
    ViewLink,
)
from .resolution import HierarchyResolver


@dataclass(frozen=True)
class HierarchyAnalysis:
    device_catalog: Mapping[str, Any]
    documentation_catalog: Mapping[str, Any]
    mapping: PreparedMapping
    complete_documentation: bool = False

    def resolve(self) -> PreparedHierarchy:
        evidence = self.collect()
        assert self.mapping.device_catalog is not None
        assert self.mapping.documentation_catalog is not None
        hierarchy = HierarchyResolver(
            evidence,
            self.mapping.device_catalog.info["entry_view"],
            self.mapping.documentation_catalog.info["entry_view"],
        ).resolve()
        declared = {}
        for identifier, location in self.mapping.device_catalog.entries.items():
            record = self.device_catalog["views"][location.view][location.index]
            transition = DocumentTransition.from_command(record)
            if transition.kind != "unknown":
                declared[identifier] = transition.target_view
        return replace(hierarchy, declared_transitions=declared)

    def collect(self) -> HierarchyEvidence:
        """Retain every full pair and its documented effect, including unknowns.

        View names are local references only. No comparison, predicate evaluation
        or view selection is performed here. Use the original matching catalogs.
        """
        device = MatchedCatalog.bind(self.device_catalog, self.mapping.device_catalog)
        documentation = MatchedCatalog.bind(
            self.documentation_catalog, self.mapping.documentation_catalog
        )
        if documentation.sources.info["source"] != "documentation":
            raise FormatError("documentation catalog source must be documentation")

        commands = []
        views: dict[tuple[str, str], list[CommandLink]] = defaultdict(list)
        for pattern_id, match in self.mapping.devices.items():
            device.command(pattern_id, match.device_format)
            for pair in match.mappings:
                if not self._full_match(pair):
                    continue
                record = documentation.command(pair.document_id, pair.document_format)
                left = device.sources.entries[pattern_id]
                right = documentation.sources.entries[pair.document_id]
                assert left.view is not None and right.view is not None
                transition = DocumentTransition.from_command(
                    record, complete=self.complete_documentation
                )
                link = CommandLink(pair, left, right, transition)
                commands.append(link)
                views[left.view, right.view].append(link)
        return HierarchyEvidence(
            tuple(commands),
            tuple(
                ViewLink(left, right, tuple(links))
                for (left, right), links in views.items()
            ),
        )

    @staticmethod
    def _full_match(pair: PreparedPair) -> bool:
        if pair.status not in {
            "equivalent",
            "document_subset",
            "device_subset",
            "overlap",
            "matched",
        }:
            return False
        if pair.stage in {"exact", "reordered"}:
            return pair.binding_mode == "structural"
        return (
            pair.stage == "intersection"
            and pair.binding_mode == "path_dependent"
            and pair.automaton is not None
        )
