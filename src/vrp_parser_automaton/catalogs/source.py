"""Read one runtime pattern document, with optional prepared view transitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from vrp_parser_automaton.errors import PatternDocumentError


@dataclass(frozen=True)
class CatalogCommand:
    format: str
    view: str | None
    child_view: str | None
    parameter_types: Mapping[str, str] = field(default_factory=dict)


def parameter_annotations(value: Any) -> dict[str, str]:
    """Validate the optional per-command type list without requiring full coverage."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PatternDocumentError("parameter_types must be an array")
    result: dict[str, str] = {}
    for annotation in value:
        if not isinstance(annotation, Mapping):
            raise PatternDocumentError("parameter_types entries must be objects")
        name = annotation.get("parameter_name")
        type_id = annotation.get("parameter_type")
        if not isinstance(name, str) or not name.strip():
            raise PatternDocumentError("parameter_name must be a nonempty string")
        if not isinstance(type_id, str) or not type_id.strip():
            raise PatternDocumentError("parameter_type must be a nonempty string")
        if name in result:
            raise PatternDocumentError(f"Duplicate parameter type annotation: {name!r}")
        result[name] = type_id
    return result


@dataclass(frozen=True)
class PatternCatalog:
    commands: tuple[CatalogCommand, ...]
    views: tuple[str, ...] = ()
    entry_view: str | None = None

    @classmethod
    def read(cls, document: Mapping[str, Any]) -> PatternCatalog:
        if not isinstance(document, Mapping):
            raise PatternDocumentError("pattern document must be an object")
        layout = document.get("type", "flat")
        groups: Mapping[str | None, Any]
        if layout == "flat":
            if "views" in document or "entry_view" in document:
                raise PatternDocumentError(
                    "flat document cannot contain views or entry_view"
                )
            groups = {None: document.get("commands")}
            entry = None
        elif layout == "grouped":
            groups = document.get("views", {})
            entry = document.get("entry_view")
            if (
                not isinstance(groups, Mapping)
                or not all(isinstance(view, str) and view.strip() for view in groups)
                or not isinstance(entry, str)
                or entry not in groups
                or "commands" in document
            ):
                raise PatternDocumentError(
                    "grouped document requires views and entry_view"
                )
        else:
            raise PatternDocumentError("pattern document type must be flat or grouped")

        commands = []
        for view, records in groups.items():
            if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
                raise PatternDocumentError("commands must be an array")
            for record in records:
                pattern = (
                    record.get("format") if isinstance(record, Mapping) else record
                )
                if not isinstance(pattern, str) or not pattern.strip():
                    raise PatternDocumentError(
                        "command format must be a nonempty string"
                    )
                target = (
                    record.get("switch_to_view")
                    if isinstance(record, Mapping)
                    else None
                )
                if target is None:
                    child_view = view
                elif view is not None and target == {"status": "unresolved"}:
                    child_view = None
                elif view is not None and isinstance(target, str) and target in groups:
                    child_view = target
                else:
                    raise PatternDocumentError(
                        "switch_to_view must reference an existing view, be null "
                        'or be {"status": "unresolved"}'
                    )
                annotations = parameter_annotations(
                    record.get("parameter_types", ())
                    if isinstance(record, Mapping)
                    else ()
                )
                commands.append(CatalogCommand(pattern, view, child_view, annotations))
        if not commands:
            raise PatternDocumentError("commands cannot be empty")
        return cls(
            tuple(commands), tuple(view for view in groups if view is not None), entry
        )
