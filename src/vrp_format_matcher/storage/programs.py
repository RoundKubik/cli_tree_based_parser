"""JSON representation of annotated parser instructions, without an AST parser."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from vrp_format_matcher.models import (
    CaptureTag,
    MetadataError,
    PatternProgram,
    RepeatBinding,
    SlotBinding,
)
from vrp_parser_automaton.automata.model import Instruction
from vrp_parser_automaton.patterns import Literal, Parameter, SourceSpan


@dataclass(frozen=True)
class CaptureStorage:
    def read(self, data: dict[str, Any] | None) -> CaptureTag | None:
        if data is None:
            return None
        return CaptureTag(
            data["slot_id"],
            data["name"],
            data["declaration"],
            data["type_id"],
            tuple((key, index) for key, index in data["iterations"]),
        )


class ProgramStorage:
    def write(self, program: PatternProgram | None) -> dict[str, Any] | None:
        if program is None:
            return None
        return {
            "root": program.root,
            "instructions": [self._write_node(node) for node in program.instructions],
            "slots": [asdict(slot) for slot in program.slots],
            "repetitions": [asdict(item) for item in program.repetitions],
        }

    def _write_node(self, node: Instruction) -> dict[str, Any]:
        atom = None
        if node.atom is not None:
            atom = {
                "kind": "literal" if isinstance(node.atom, Literal) else "parameter",
                "source": node.atom.value
                if isinstance(node.atom, Literal)
                else node.atom.source,
                "span": asdict(node.atom.span),
            }
        return {
            "kind": node.kind,
            "path": node.path,
            "target": node.target,
            "branches": node.branches,
            "minimum": node.minimum,
            "maximum": node.maximum,
            "pattern_index": node.pattern_index,
            "atom": atom,
        }

    def read(self, data: dict[str, Any] | None) -> PatternProgram | None:
        if data is None:
            return None
        tags = CaptureStorage()
        program = PatternProgram(
            data["root"],
            tuple(self._read_node(node) for node in data["instructions"]),
            tuple(
                SlotBinding(tags.read(slot["document"]), tags.read(slot["device"]))
                for slot in data["slots"]
            ),
            tuple(RepeatBinding(**item) for item in data["repetitions"]),
        )
        self._validate(program)
        return program

    def _read_node(self, data: dict[str, Any]) -> Instruction:
        atom: Literal | Parameter | None = None
        encoded = data["atom"]
        if encoded is not None:
            span = SourceSpan(**encoded["span"])
            if encoded["kind"] == "literal":
                atom = Literal(encoded["source"], span)
            elif encoded["kind"] == "parameter":
                # Runtime reads already validated captures; no validator is stored.
                atom = Parameter(None, encoded["source"], span)
            else:
                raise MetadataError("invalid serialized atom")
        return Instruction(
            data["kind"],
            data["path"],
            data["target"],
            tuple(data["branches"]),
            atom,
            data["minimum"],
            data["maximum"],
            data["pattern_index"],
        )

    def _validate(self, program: PatternProgram) -> None:
        count = len(program.instructions)
        if (
            not isinstance(program.root, int)
            or not 0 <= program.root < count
            or len(program.slots) != count
        ):
            raise MetadataError("invalid program root or slot count")
        supported = {
            "atom",
            "accept",
            "choice",
            "optional",
            "choice_close",
            "set_enter",
            "set_next",
            "set_commit",
            "repeat_enter",
            "repeat_next",
            "repeat_commit",
        }
        for node, slot in zip(program.instructions, program.slots, strict=True):
            if node.kind not in supported:
                raise MetadataError("unknown program instruction")
            indices = (
                *node.branches,
                *((node.target,) if node.kind != "accept" else ()),
            )
            if not all(
                isinstance(index, int) and 0 <= index < count for index in indices
            ):
                raise MetadataError("program references an invalid instruction")
            if node.kind == "atom" and node.atom is None:
                raise MetadataError("atom instruction has no atom")
            if isinstance(node.atom, Parameter) and (
                slot.document is None or slot.device is None
            ):
                raise MetadataError("parameter instruction is missing its bindings")
            if node.kind == "repeat_next" and (
                len(node.branches) != 1 or not 0 <= node.minimum <= node.maximum
            ):
                raise MetadataError("invalid repetition instruction")
