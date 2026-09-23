"""Plain JSON data: source slots once, bindings by ID, shared scope graphs."""

from __future__ import annotations

from typing import Any

from vrp_format_matcher.models import (
    Arc,
    Automaton,
    CaptureTag,
    DeviceMatch,
    PreparedMapping,
    PreparedPair,
)


class MappingData:
    """Normalize the completed result in memory, without writing or reading files."""

    def __init__(self, mapping: PreparedMapping) -> None:
        self._mapping = mapping
        self._documents: dict[str, Any] = {}
        self._automata = ScopeGraphs()

    def to_dict(self) -> dict[str, Any]:
        devices = {
            pattern_id: self._device(device)
            for pattern_id, device in self._mapping.devices.items()
        }
        return {
            "devices": devices,
            "documents": self._documents,
            "automata": self._automata.records,
        }

    def _device(self, device: DeviceMatch) -> dict[str, Any]:
        slots = {}
        mappings = []
        for pair in device.mappings:
            document = self._documents.setdefault(
                pair.document_id,
                {"document_format": pair.document_format, "slots": {}},
            )
            for binding in pair.bindings:
                slots[binding.device.slot_id] = self._slot(binding.device)
                document["slots"][binding.document.slot_id] = self._slot(
                    binding.document
                )
            mappings.append(self._pair(pair))
        return {
            "device_format": device.device_format,
            "status": device.status,
            "stage": device.stage,
            "slots": slots,
            "mappings": mappings,
        }

    def _pair(self, pair: PreparedPair) -> dict[str, Any]:
        result = {
            "document_id": pair.document_id,
            "status": pair.status,
            "stage": pair.stage,
            "binding_mode": pair.binding_mode,
            "bindings": [
                {"document": b.document.slot_id, "device": b.device.slot_id}
                for b in pair.bindings
            ],
        }
        if pair.automaton is not None:
            result["automaton_id"] = self._automata.reference(pair.automaton)
        return result

    @staticmethod
    def _slot(tag: CaptureTag) -> dict[str, Any]:
        return {
            "name": tag.name,
            "declaration": tag.declaration,
            "type_id": tag.type_id,
            "repeat_ids": list(tag.repeat_ids),
        }


class ScopeGraphs:
    """Share equal annotated graphs; slot metadata belongs to each source format.

    Equality includes every edge, source slot and repetition coordinate. Names
    and declarations are resolved in the referencing pair's slot tables.
    """

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}
        self._ids: dict[tuple[object, ...], str] = {}

    def reference(self, machine: Automaton) -> str:
        key = (
            machine.start,
            machine.final,
            tuple(
                tuple(
                    (
                        arc.target,
                        arc.label,
                        self._capture_key(arc.document),
                        self._capture_key(arc.device),
                    )
                    for arc in edges
                )
                for edges in machine.edges
            ),
        )
        if key not in self._ids:
            identifier = f"a:{len(self.records)}"
            self._ids[key] = identifier
            self.records[identifier] = {
                "start": machine.start,
                "final": machine.final,
                "edges": [[self._arc(arc) for arc in edges] for edges in machine.edges],
            }
        return self._ids[key]

    def _arc(self, arc: Arc) -> dict[str, Any]:
        result: dict[str, Any] = {"target": arc.target, "label": arc.label}
        if arc.document is not None:
            result["document"] = self._capture(arc.document)
        if arc.device is not None:
            result["device"] = self._capture(arc.device)
        return result

    @staticmethod
    def _capture_key(tag: CaptureTag | None) -> object:
        return (tag.slot_id, tag.iterations) if tag is not None else None

    @staticmethod
    def _capture(tag: CaptureTag) -> dict[str, Any]:
        return {
            "slot_id": tag.slot_id,
            "iterations": [list(coordinates) for coordinates in tag.iterations],
        }
