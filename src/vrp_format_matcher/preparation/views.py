"""The entry views are the only established context pair at this stage."""

from __future__ import annotations

from dataclasses import dataclass

from .sources import CommandCatalog


@dataclass(frozen=True)
class EntryViewScope:
    """Global source indices; None permits global search, an empty set forbids it."""

    device_indices: frozenset[int] = frozenset()
    document_indices: frozenset[int] = frozenset()

    @classmethod
    def between(
        cls, device: CommandCatalog, documentation: CommandCatalog
    ) -> EntryViewScope:
        if device.info["type"] != "grouped" or documentation.info["type"] != "grouped":
            return cls()
        return cls(
            frozenset(
                index
                for index, location in enumerate(device.locations)
                if location.view == device.info["entry_view"]
            ),
            frozenset(
                index
                for index, location in enumerate(documentation.locations)
                if location.view == documentation.info["entry_view"]
            ),
        )

    def documents_for(self, device_index: int) -> frozenset[int] | None:
        if device_index in self.device_indices:
            return self.document_indices
        return None
