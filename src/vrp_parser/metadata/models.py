"""Values shared by preparation and runtime metadata evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from vrp_parser.results import ParameterValue


class MetadataError(ValueError):
    """Invalid metadata, incompatible artifacts, or invalid predicates."""


class MappingLimitExceeded(MetadataError):
    """A bounded analysis could not finish; this never means disjoint."""


@dataclass(frozen=True)
class MappingLimits:
    automaton_states: int = 20_000
    comparison_states: int = 20_000
    product_states: int = 20_000
    runtime_configurations: int = 20_000

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.automaton_states,
                self.comparison_states,
                self.product_states,
                self.runtime_configurations,
            )
        ):
            raise MetadataError("mapping limits must be positive integers")


@dataclass(frozen=True)
class CaptureTag:
    """One source slot and the repetition coordinates of its current occurrence."""

    slot_id: str
    name: str | None
    declaration: str
    type_id: str | None
    iterations: tuple[tuple[str, int], ...] = ()


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
class ProgramBranch:
    target: int
    document_bit: int = 0
    device_bit: int = 0


@dataclass(frozen=True)
class Instruction:
    """A compact expression; sets and repeats are never unrolled here."""

    kind: str
    children: tuple[int, ...] = ()
    branches: tuple[ProgramBranch, ...] = ()
    label: str | None = None
    document: CaptureTag | None = None
    device: CaptureTag | None = None
    minimum: int = 0
    maximum: int = 0
    document_repeat: str | None = None
    device_repeat: str | None = None


@dataclass(frozen=True)
class PatternProgram:
    root: int
    instructions: tuple[Instruction, ...]


@dataclass(frozen=True)
class Comparison:
    relation: str
    structurally_identical: bool = False
    common_example: tuple[str, ...] | None = None
    document_only_example: tuple[str, ...] | None = None
    device_only_example: tuple[str, ...] | None = None
    common_prefix: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class PreparedPair:
    document_id: str
    document_format: str
    pattern_id: str
    device_format: str
    comparison: Comparison
    # Rules are owned JSON copies; callers should treat artifacts as read-only.
    creates: tuple[dict[str, Any], ...]
    requires: tuple[dict[str, Any], ...]
    automaton: Automaton | None
    program: PatternProgram | None = None
    strategy: str = "automaton"

    @property
    def recognizer(self) -> Automaton | PatternProgram | None:
        return self.program if self.program is not None else self.automaton


@dataclass(frozen=True)
class ParameterBinding:
    document: CaptureTag
    device: CaptureTag
    value: ParameterValue


@dataclass(frozen=True)
class MetadataEffect:
    rule_id: str
    kind: str
    metadata: dict[str, Any]
    target: ParameterValue | None = None

    def identity(self) -> str:
        """Compare observable effects, including arbitrary normalized values."""
        return json.dumps(
            asdict(self), sort_keys=True, ensure_ascii=False, default=repr
        )


@dataclass(frozen=True)
class BindingAlternative:
    bindings: tuple[ParameterBinding, ...]
    effects: tuple[MetadataEffect, ...]


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    status: str  # active, inactive, ambiguous
    # Nonempty only when every interpretation agrees on the complete effect set.
    effects: tuple[MetadataEffect, ...]


@dataclass(frozen=True)
class MetadataApplication:
    document_id: str
    pattern_id: str
    variation_id: str
    status: str  # applied, inactive, ambiguous, not_applicable, unknown
    binding_status: str = "unavailable"  # unique, ambiguous, unavailable
    alternatives: tuple[BindingAlternative, ...] = ()
    rules: tuple[RuleEvaluation, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class MetadataReport:
    applications: tuple[MetadataApplication, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
