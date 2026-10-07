"""Resolve established targets and retain all other view options without scoring."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import HierarchyEvidence, PreparedHierarchy, ViewLink, ViewTarget


@dataclass(frozen=True)
class HierarchyResolver:
    evidence: HierarchyEvidence
    device_entry_view: str
    documentation_entry_view: str

    def resolve(self) -> PreparedHierarchy:
        """Index once by documentation view; reuse options across entry commands.

        Only the entry pair is established by these inputs. Command matches
        supply positive evidence, never an exhaustive list of possible views.
        """
        by_document: dict[str, list[ViewLink]] = defaultdict(list)
        for link in self.evidence.view_links:
            by_document[link.documentation_view].append(link)

        requested = dict.fromkeys(
            [self.documentation_entry_view]
            + [
                link.transition.target_view
                for link in self.evidence.command_links
                if link.transition.target_view is not None
            ]
        )
        targets = {}
        for view in requested:
            candidates = tuple(by_document.get(view, ()))
            targets[view] = (
                ViewTarget("resolved", candidates, self.device_entry_view)
                if view == self.documentation_entry_view
                else ViewTarget("unresolved", candidates)
            )
        return PreparedHierarchy(
            self.device_entry_view,
            self.documentation_entry_view,
            self.evidence,
            targets,
        )
