"""Format relationships, source-slot correspondences and analysis budgets."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser_automaton.automata.model import Instruction


class FormatError(ValueError):
    """Invalid source formats or analysis settings."""


class MappingLimitExceeded(FormatError):
    """A bounded analysis could not finish; this never means disjoint."""


@dataclass(frozen=True)
class PreparationProgress:
    devices_done: int
    devices_total: int
    pairs_prepared: int
    stage: str = "exact"


@dataclass(frozen=True)
class MappingLimits:
    automaton_states: int = 20_000
    comparison_states: int = 20_000
    product_states: int = 20_000
    analysis_steps: int = 200_000

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.automaton_states,
                self.comparison_states,
                self.product_states,
                self.analysis_steps,
            )
        ):
            raise FormatError("mapping limits must be positive integers")


class AnalysisBudget:
    """Bound actual fallback work, not only the number of stored DFA subsets."""

    def __init__(self, maximum: int) -> None:
        self._remaining = maximum

    def spend(self, amount: int = 1) -> None:
        self._remaining -= amount
        if self._remaining < 0:
            raise MappingLimitExceeded("language analysis operation limit exceeded")


@dataclass(frozen=True)
class CaptureTag:
    """One source slot and the repetition coordinates of its current occurrence."""

    slot_id: str
    name: str | None
    declaration: str
    type_id: str | None
    iterations: tuple[tuple[str, int], ...] = ()
    repeat_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Arc:
    target: int
    label: str | None = None  # None = epsilon; P = parameter; K:... = keyword
    document: CaptureTag | None = None
    device: CaptureTag | None = None


@dataclass(frozen=True)
class Automaton:
    start: int
    final: int
    edges: tuple[tuple[Arc, ...], ...]


@dataclass(frozen=True)
class SlotBinding:
    document: CaptureTag | None = None
    device: CaptureTag | None = None


@dataclass(frozen=True)
class RepeatBinding:
    path: str
    document: str | None = None
    device: str | None = None


@dataclass(frozen=True)
class PatternProgram:
    """A slice of the parser's own instructions, with parameter annotations."""

    root: int
    instructions: tuple[Instruction, ...]
    slots: tuple[SlotBinding, ...]
    repetitions: tuple[RepeatBinding, ...] = ()


@dataclass(frozen=True)
class Comparison:
    relation: str
    structurally_identical: bool = False


@dataclass(frozen=True)
class ParameterCorrespondence:
    document: CaptureTag
    device: CaptureTag


@dataclass(frozen=True)
class PreparedPair:
    document_id: str
    document_format: str
    pattern_id: str
    device_format: str
    status: str
    bindings: tuple[ParameterCorrespondence, ...]
    binding_mode: str
    structurally_identical: bool = False
    automaton: Automaton | None = None
    stage: str = "intersection"

    @property
    def comparison(self) -> Comparison:
        return Comparison(self.status, self.structurally_identical)


@dataclass(frozen=True)
class DeviceMatch:
    device_format: str
    status: str  # matched, partial, unmatched, unknown
    mappings: tuple[PreparedPair, ...] = ()
    stage: str | None = None


@dataclass(frozen=True)
class PreparedMapping:
    devices: dict[str, DeviceMatch]

    @property
    def pairs(self) -> tuple[PreparedPair, ...]:
        return tuple(
            pair for device in self.devices.values() for pair in device.mappings
        )
