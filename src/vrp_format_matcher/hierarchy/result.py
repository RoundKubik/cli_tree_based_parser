"""Compact hierarchy JSON referencing existing mappings and shared view evidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from vrp_format_matcher.models import PreparedMapping


class HierarchyData:
    def __init__(self, mapping: PreparedMapping) -> None:
        assert mapping.hierarchy is not None
        self._hierarchy = mapping.hierarchy
        # Object identity avoids hashing or copying the scope graphs of pairs.
        self._positions = {
            id(pair): index
            for device in mapping.devices.values()
            for index, pair in enumerate(device.mappings)
        }

    def to_dict(self) -> dict[str, Any]:
        evidence = self._hierarchy.evidence
        view_ids = {id(link): index for index, link in enumerate(evidence.view_links)}
        views = []
        for view in evidence.view_links:
            mappings: dict[str, list[int]] = defaultdict(list)
            for link in view.commands:
                mappings[link.pair.pattern_id].append(self._positions[id(link.pair)])
            views.append(
                {
                    "device_view": view.device_view,
                    "documentation_view": view.documentation_view,
                    "mappings": dict(mappings),
                    **(
                        {"coverage": view.coverage} if view.coverage is not None else {}
                    ),
                }
            )

        targets = {}
        for name, target in self._hierarchy.targets.items():
            record: dict[str, Any] = {
                "status": target.status,
                "candidates": [view_ids[id(view)] for view in target.candidates],
            }
            if target.device_view is not None:
                record["device_view"] = target.device_view
            targets[name] = record

        transitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in evidence.command_links:
            effect: dict[str, Any] = {
                "mapping_index": self._positions[id(link.pair)],
                "kind": link.transition.kind,
            }
            if link.transition.target_view is not None:
                effect["target"] = link.transition.target_view
            transitions[link.pair.pattern_id].append(effect)

        result = {
            "entry_views": {
                "device": self._hierarchy.device_entry_view,
                "documentation": self._hierarchy.documentation_entry_view,
            },
            "view_links": views,
            "targets": targets,
            "transitions": dict(transitions),
        }
        if self._hierarchy.declared_transitions:
            result["declared_transitions"] = dict(self._hierarchy.declared_transitions)
        if self._hierarchy.resolved_views:
            result["resolved_views"] = dict(self._hierarchy.resolved_views)
        return result
