"""A separate runtime catalog and its offline mapping, with stable format IDs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from vrp_format_matcher.hierarchy.recovery import RecoveredViews
from vrp_format_matcher.models import PreparedMapping


@dataclass(frozen=True)
class PreparedCatalog:
    catalog: dict[str, Any]
    mapping: PreparedMapping
    unresolved: tuple[str, ...] = ()

    @classmethod
    def recovered(
        cls,
        source: Mapping[str, Any],
        mapping: PreparedMapping,
        recovery: RecoveredViews,
    ) -> PreparedCatalog:
        assert mapping.device_catalog is not None
        catalog = deepcopy(dict(source))
        for identifier, location in mapping.device_catalog.entries.items():
            record = catalog["views"][location.view][location.index]
            if identifier not in recovery.transitions:
                record["switch_to_view"] = {"status": "unresolved"}
            else:
                target = recovery.transitions[identifier]
                if target is None:
                    record.pop("switch_to_view", None)
                else:
                    record["switch_to_view"] = target
        # Do not reorder or merge records: duplicate ordinals are part of pattern_id.
        return cls(catalog, mapping, recovery.unresolved)
