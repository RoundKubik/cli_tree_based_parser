"""Legacy commands and v1 catalogs, flattened once with their original locations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vrp_parser_automaton.automata.model import CommandAutomaton
from vrp_parser_automaton.errors import PatternDocumentError
from vrp_parser_automaton.patterns import Group, Node, Parameter, Repeat
from vrp_parser_automaton.patterns import Sequence as PatternSequence


@dataclass(frozen=True)
class CatalogCommand:
    format: str
    view: str | None
    index: int
    record: Mapping[str, Any]


@dataclass(frozen=True)
class PatternCatalog:
    commands: tuple[CatalogCommand, ...]
    views: tuple[str, ...] = ()
    entry_view: str | None = None
    documentation: bool = False

    @classmethod
    def read(cls, document: Mapping[str, Any]) -> PatternCatalog:
        if not isinstance(document, Mapping):
            raise PatternDocumentError("pattern document must be an object")
        if "type" not in document:
            values = document.get("commands")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise PatternDocumentError(
                    "pattern document requires 'commands' array of strings"
                )
            records = []
            for i, value in enumerate(values):
                if not isinstance(value, str) or not value.strip():
                    raise PatternDocumentError(
                        f"command at index {i} must be a nonempty string"
                    )
                records.append(CatalogCommand(value, None, i, {"format": value}))
            if not records:
                raise PatternDocumentError("'commands' cannot be empty")
            return cls(tuple(records))

        if (
            type(document.get("schema_version")) is not int
            or document["schema_version"] != 1
        ):
            raise PatternDocumentError("catalog schema_version must be 1")
        if document.get("source") not in ("device", "documentation"):
            raise PatternDocumentError("catalog source must be device or documentation")
        for key in ("vendor", "device", "model_type"):
            if not isinstance(document.get(key), str) or not document[key].strip():
                raise PatternDocumentError(f"catalog {key} must be a nonempty string")
        groups: Mapping[str | None, Any]
        if document["type"] == "flat":
            if "views" in document or "entry_view" in document:
                raise PatternDocumentError(
                    "flat catalog cannot contain views or entry_view"
                )
            groups = {None: document.get("commands")}
        elif document["type"] == "grouped":
            groups = document.get("views", {})
            if (
                not isinstance(groups, Mapping)
                or not groups
                or "commands" in document
                or not all(isinstance(view, str) and view.strip() for view in groups)
            ):
                raise PatternDocumentError("grouped catalog requires named views")
            if (
                not isinstance(document.get("entry_view"), str)
                or document["entry_view"] not in groups
            ):
                raise PatternDocumentError("entry_view must reference an existing view")
        else:
            raise PatternDocumentError("catalog type must be flat or grouped")

        records = []
        documentation = document["source"] == "documentation"
        for view, entries in groups.items():
            if not isinstance(entries, list):
                raise PatternDocumentError("catalog commands must be arrays")
            for index, record in enumerate(entries):
                if not isinstance(record, Mapping):
                    raise PatternDocumentError("catalog commands must be objects")
                pattern = record.get("format")
                if (
                    not isinstance(pattern, str)
                    or not pattern.strip()
                    or any(c in pattern for c in "\r\n")
                ):
                    raise PatternDocumentError("format must be a nonempty single line")
                if "id" in record:
                    raise PatternDocumentError(
                        "v1 command IDs are generated internally"
                    )
                if documentation != ("parameter_types" in record):
                    raise PatternDocumentError(
                        "parameter_types belongs to documentation and is required there"
                    )
                if "switch_to_view" in record:
                    target = record["switch_to_view"]
                    if (
                        view is None
                        or target is not None
                        and (not isinstance(target, str) or target not in groups)
                    ):
                        raise PatternDocumentError("invalid switch_to_view reference")
                records.append(CatalogCommand(pattern, view, index, record))
        if not records:
            raise PatternDocumentError("catalog must contain at least one command")
        return cls(
            tuple(records),
            tuple(v for v in groups if v is not None),
            document.get("entry_view"),
            documentation,
        )

    def validate_parameters(self, graph: CommandAutomaton) -> None:
        if not self.documentation:
            return
        for source, command in zip(graph.patterns, self.commands, strict=True):
            records = command.record["parameter_types"]
            if not isinstance(records, list):
                raise PatternDocumentError("parameter_types must be an array")
            names = set()
            for record in records:
                if not isinstance(record, Mapping):
                    raise PatternDocumentError(
                        "parameter_types entries must be objects"
                    )
                name, kind = record.get("parameter_name"), record.get("parameter_type")
                if (
                    not isinstance(name, str)
                    or name in names
                    or not isinstance(kind, str)
                    or kind not in {"string", "integer", "ipv4-address", "ipv6-address"}
                ):
                    raise PatternDocumentError("invalid or duplicate parameter type")
                names.add(name)
            if names != self._names(source.ast):
                raise PatternDocumentError(
                    "parameter_types must cover format parameters exactly"
                )

    @classmethod
    def _names(cls, node: Node | PatternSequence) -> set[str]:
        if isinstance(node, Parameter):
            return {node.source[1:-1]}
        if isinstance(node, Repeat):
            return cls._names(node.atom)
        children = (
            node.items
            if isinstance(node, PatternSequence)
            else node.alternatives
            if isinstance(node, Group)
            else ()
        )
        return set().union(*(cls._names(child) for child in children))
