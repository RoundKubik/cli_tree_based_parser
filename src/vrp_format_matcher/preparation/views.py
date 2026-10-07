"""Command search scopes for entry and recovered view correspondences."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .sources import CommandCatalog


@dataclass(frozen=True)
class ViewScope:
    """Global source indices; None permits global search, an empty set forbids it."""

    scopes: dict[int, frozenset[int]] = field(default_factory=dict)

    @classmethod
    def between(
        cls, device: CommandCatalog, documentation: CommandCatalog
    ) -> ViewScope:
        if device.info["type"] != "grouped" or documentation.info["type"] != "grouped":
            return cls()
        return cls.recovered(
            device,
            documentation,
            {device.info["entry_view"]: documentation.info["entry_view"]},
        )

    def documents_for(self, device_index: int) -> frozenset[int] | None:
        return self.scopes.get(device_index)

    @classmethod
    def recovered(
        cls,
        device: CommandCatalog,
        documentation: CommandCatalog,
        views: dict[str, str],
    ) -> ViewScope:
        by_view: dict[str, set[int]] = defaultdict(set)
        for index, location in enumerate(documentation.locations):
            assert location.view is not None
            by_view[location.view].add(index)
        allowed = {
            view: frozenset(by_view[reference]) for view, reference in views.items()
        }
        return cls(
            scopes={
                index: allowed[location.view]
                for index, location in enumerate(device.locations)
                if location.view in allowed
            }
        )
