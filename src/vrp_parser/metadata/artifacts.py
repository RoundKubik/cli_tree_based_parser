"""Versioned JSON storage for prepared data, independent of compilation/runtime."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .models import (
    Arc,
    Automaton,
    CaptureTag,
    Comparison,
    Instruction,
    MappingLimits,
    MetadataError,
    PatternProgram,
    PreparedPair,
    ProgramBranch,
)


@dataclass(frozen=True)
class MetadataArtifact:
    pairs: tuple[PreparedPair, ...]
    limits: MappingLimits

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "limits": asdict(self.limits),
            "pairs": [asdict(pair) for pair in self.pairs],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ArtifactReader:
    source: str

    def read(self) -> MetadataArtifact:
        try:
            data = json.loads(self.source)
            if not isinstance(data, dict) or data.get("version") not in {1, 2}:
                raise MetadataError("unsupported prepared metadata version")
            return MetadataArtifact(
                pairs=tuple(self._pair(item) for item in data["pairs"]),
                limits=MappingLimits(**data["limits"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise MetadataError(f"invalid prepared metadata: {error}") from error

    def _pair(self, data: dict[str, Any]) -> PreparedPair:
        return PreparedPair(
            document_id=data["document_id"],
            document_format=data["document_format"],
            pattern_id=data["pattern_id"],
            device_format=data["device_format"],
            comparison=self._comparison(data["comparison"]),
            creates=tuple(data["creates"]),
            requires=tuple(data["requires"]),
            automaton=self._automaton(data["automaton"]),
            program=self._program(data.get("program")),
            strategy=data.get("strategy", "automaton"),
        )

    def _comparison(self, data: dict[str, Any]) -> Comparison:
        values = dict(data)
        for key in (
            "common_example",
            "document_only_example",
            "device_only_example",
            "common_prefix",
        ):
            if values[key] is not None:
                values[key] = tuple(values[key])
        return Comparison(**values)

    def _automaton(self, data: dict[str, Any] | None) -> Automaton | None:
        if data is None:
            return None
        machine = Automaton(
            start=data["start"],
            final=data["final"],
            edges=tuple(
                tuple(self._arc(arc) for arc in arcs) for arcs in data["edges"]
            ),
        )
        self._validate_states(machine)
        return machine

    def _arc(self, data: dict[str, Any]) -> Arc:
        arc = Arc(
            target=data["target"],
            label=data["label"],
            document=self._tag(data["document"]),
            device=self._tag(data["device"]),
        )
        if arc.label == "P" and (arc.document is None or arc.device is None):
            raise MetadataError("parameter arc is missing its bindings")
        if (
            arc.label is not None
            and arc.label != "P"
            and not arc.label.startswith("K:")
        ):
            raise MetadataError("invalid automaton label")
        return arc

    def _tag(self, data: dict[str, Any] | None) -> CaptureTag | None:
        if data is None:
            return None
        return CaptureTag(
            slot_id=data["slot_id"],
            name=data["name"],
            declaration=data["declaration"],
            type_id=data["type_id"],
            iterations=tuple((key, index) for key, index in data["iterations"]),
        )

    def _validate_states(self, machine: Automaton) -> None:
        indices = (
            machine.start,
            machine.final,
            *(arc.target for arcs in machine.edges for arc in arcs),
        )
        if not all(
            isinstance(index, int) and 0 <= index < len(machine.edges)
            for index in indices
        ):
            raise MetadataError("automaton references an invalid state")

    def _program(self, data: dict[str, Any] | None) -> PatternProgram | None:
        if data is None:
            return None
        instructions = tuple(
            Instruction(
                kind=node["kind"],
                children=tuple(node["children"]),
                branches=tuple(ProgramBranch(**branch) for branch in node["branches"]),
                label=node["label"],
                document=self._tag(node["document"]),
                device=self._tag(node["device"]),
                minimum=node["minimum"],
                maximum=node["maximum"],
                document_repeat=node["document_repeat"],
                device_repeat=node["device_repeat"],
            )
            for node in data["instructions"]
        )
        program = PatternProgram(data["root"], instructions)
        indices = (
            program.root,
            *(child for node in instructions for child in node.children),
            *(branch.target for node in instructions for branch in node.branches),
        )
        if not all(
            isinstance(index, int) and 0 <= index < len(instructions)
            for index in indices
        ):
            raise MetadataError("program references an invalid instruction")
        for node in instructions:
            if node.kind not in {"atom", "sequence", "choice", "set", "repeat"}:
                raise MetadataError("unknown program instruction")
            if node.kind == "atom":
                if node.label is None:
                    raise MetadataError("atom is missing its label")
                self._arc(
                    {
                        "target": 0,
                        "label": node.label,
                        "document": asdict(node.document) if node.document else None,
                        "device": asdict(node.device) if node.device else None,
                    }
                )
            if node.kind == "repeat" and (
                len(node.children) != 1 or not 0 <= node.minimum <= node.maximum
            ):
                raise MetadataError("invalid repetition instruction")
            if node.kind == "set" and any(
                branch.document_bit <= 0
                or branch.device_bit <= 0
                or branch.document_bit.bit_count() != 1
                or branch.device_bit.bit_count() != 1
                for branch in node.branches
            ):
                raise MetadataError("invalid set selection bit")
        return program
