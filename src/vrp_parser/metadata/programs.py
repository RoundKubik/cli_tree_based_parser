"""Compile compact expression programs, optionally pairing equivalent ASTs."""

from __future__ import annotations

from dataclasses import dataclass

from vrp_parser.graph.model import ascii_lower
from vrp_parser.parameters import ParameterDeclaration
from vrp_parser.patterns import Group, GroupMode, Literal, Node, Parameter, Repeat
from vrp_parser.patterns import Sequence as PatternSequence

from .models import (
    CaptureTag,
    Instruction,
    MappingLimitExceeded,
    PatternProgram,
    ProgramBranch,
)
from .patterns import NamedParameter

type Expression = Node | PatternSequence


def canonical_key(expression: Expression) -> tuple[object, ...]:
    """Preserve multiplicity and cardinality, ignoring alternative order/types."""
    if isinstance(expression, PatternSequence):
        return ("sequence", tuple(canonical_key(item) for item in expression.items))
    if isinstance(expression, Literal):
        return ("literal", ascii_lower(expression.value))
    if isinstance(expression, Parameter):
        return ("parameter",)
    if isinstance(expression, Repeat):
        return (
            "repeat",
            expression.minimum,
            expression.maximum,
            canonical_key(expression.atom),
        )
    return (
        expression.mode.value,
        tuple(
            sorted(
                (canonical_key(branch) for branch in expression.alternatives), key=repr
            )
        ),
    )


@dataclass(frozen=True)
class ProgramCompiler:
    maximum_instructions: int

    def single(self, ast: PatternSequence, *, document: bool) -> PatternProgram:
        builder = _ProgramBuilder(self.maximum_instructions)
        root = builder.expression(ast if document else None, None if document else ast)
        return PatternProgram(root, tuple(builder.instructions))

    def paired(
        self, document: PatternSequence, device: PatternSequence
    ) -> PatternProgram:
        """Call only after equality of canonical keys has been established."""
        builder = _ProgramBuilder(self.maximum_instructions)
        root = builder.expression(document, device)
        return PatternProgram(root, tuple(builder.instructions))


class _ProgramBuilder:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.instructions: list[Instruction] = []
        self._indices: dict[tuple[int, int], int] = {}
        self._keys: dict[int, tuple[object, ...]] = {}

    def _key(self, expression: Expression) -> tuple[object, ...]:
        if id(expression) not in self._keys:
            self._keys[id(expression)] = canonical_key(expression)
        return self._keys[id(expression)]

    def expression(self, document: Expression | None, device: Expression | None) -> int:
        identity = (id(document), id(device))
        if identity in self._indices:
            return self._indices[identity]
        if len(self.instructions) >= self.maximum:
            raise MappingLimitExceeded("compact program instruction limit exceeded")
        index = len(self.instructions)
        self._indices[identity] = index
        self.instructions.append(Instruction("pending"))
        self.instructions[index] = self._instruction(document, device)
        return index

    def _instruction(
        self, document: Expression | None, device: Expression | None
    ) -> Instruction:
        expression = document if document is not None else device
        assert expression is not None
        if isinstance(expression, PatternSequence):
            left_items = (
                document.items
                if isinstance(document, PatternSequence)
                else (None,) * len(expression.items)
            )
            right_items = (
                device.items
                if isinstance(device, PatternSequence)
                else (None,) * len(expression.items)
            )
            return Instruction(
                "sequence",
                children=tuple(
                    self.expression(first, second)
                    for first, second in zip(left_items, right_items, strict=True)
                ),
            )
        if isinstance(expression, Literal):
            return Instruction("atom", label="K:" + ascii_lower(expression.value))
        if isinstance(expression, Parameter):
            return Instruction(
                "atom",
                label="P",
                document=self._tag(document),
                device=self._tag(device),
            )
        if isinstance(expression, Repeat):
            left_atom = document.atom if isinstance(document, Repeat) else None
            right_atom = device.atom if isinstance(device, Repeat) else None
            return Instruction(
                "repeat",
                children=(self.expression(left_atom, right_atom),),
                minimum=expression.minimum,
                maximum=expression.maximum,
                document_repeat=f"r:{document.span.start}"
                if document is not None
                else None,
                device_repeat=f"r:{device.span.start}" if device is not None else None,
            )
        is_set = expression.mode in {GroupMode.REQUIRED_SET, GroupMode.OPTIONAL_SET}
        optional = expression.mode in {GroupMode.OPTIONAL_ONE, GroupMode.OPTIONAL_SET}
        left_branches = (
            document.alternatives if isinstance(document, Group) else (None,)
        )
        right_branches = device.alternatives if isinstance(device, Group) else (None,)
        branches = []
        for left_index, left in enumerate(left_branches):
            for right_index, right in enumerate(right_branches):
                if (
                    left is not None
                    and right is not None
                    and self._key(left) != self._key(right)
                ):
                    continue
                branches.append(
                    ProgramBranch(
                        target=self.expression(left, right),
                        document_bit=1 << left_index
                        if left is not None and is_set
                        else 0,
                        device_bit=1 << right_index
                        if right is not None and is_set
                        else 0,
                    )
                )
        return Instruction(
            "set" if is_set else "choice",
            branches=tuple(branches),
            minimum=0 if optional else 1,
        )

    def _tag(self, expression: Expression | None) -> CaptureTag | None:
        if expression is None:
            return None
        assert isinstance(expression, Parameter)
        declaration = expression.declaration
        return CaptureTag(
            slot_id=f"p:{expression.span.start}",
            name=declaration.name if isinstance(declaration, NamedParameter) else None,
            declaration=expression.source,
            type_id=declaration.type_id
            if isinstance(declaration, ParameterDeclaration)
            else None,
        )


class ProgramWords:
    """Compute shortest witnesses compositionally, including consuming-only bodies."""

    def __init__(self, program: PatternProgram, maximum_length: int) -> None:
        self.program = program
        self.maximum_length = maximum_length
        self._cache: dict[
            int, tuple[tuple[str, ...] | None, tuple[str, ...] | None]
        ] = {}

    def shortest(self) -> tuple[str, ...] | None:
        return self._words(self.program.root)[0]

    def _minimum(self, words: list[tuple[str, ...] | None]) -> tuple[str, ...] | None:
        valid = [word for word in words if word is not None]
        return min(valid, key=lambda word: (len(word), word)) if valid else None

    def _join(self, words: list[tuple[str, ...] | None]) -> tuple[str, ...] | None:
        if any(word is None for word in words):
            return None
        if sum(len(word) for word in words if word is not None) > self.maximum_length:
            raise MappingLimitExceeded("structural witness length limit exceeded")
        return tuple(symbol for word in words if word is not None for symbol in word)

    def _words(
        self, index: int
    ) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
        if index in self._cache:
            return self._cache[index]
        node = self.program.instructions[index]
        shortest: tuple[str, ...] | None = None
        consuming: tuple[str, ...] | None = None
        if node.kind == "atom":
            assert node.label is not None
            shortest = consuming = (node.label,)
        elif node.kind == "sequence":
            children = [self._words(child) for child in node.children]
            shortest = self._join([word for word, _ in children])
            consuming = self._minimum(
                [
                    self._join(
                        [
                            pair[1] if i == selected else pair[0]
                            for i, pair in enumerate(children)
                        ]
                    )
                    for selected in range(len(children))
                ]
            )
        elif node.kind == "choice":
            children = [self._words(branch.target) for branch in node.branches]
            shortest = (
                ()
                if node.minimum == 0
                else self._minimum([word for word, _ in children])
            )
            consuming = self._minimum([word for _, word in children])
        elif node.kind == "set":
            consuming = self._minimum(
                [self._words(branch.target)[1] for branch in node.branches]
            )
            shortest = () if node.minimum == 0 else consuming
        elif node.kind == "repeat":
            word = self._words(node.children[0])[1]
            if word is not None and node.maximum > 0:
                count = max(1, node.minimum)
                if len(word) * count > self.maximum_length:
                    raise MappingLimitExceeded(
                        "structural witness length limit exceeded"
                    )
                consuming = word * count
            shortest = () if node.minimum == 0 else consuming
        self._cache[index] = (shortest, consuming)
        return shortest, consuming
