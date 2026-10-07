"""Catalog source locations and target ASTs for offline matching."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from vrp_format_matcher.documents.catalog import DocumentSource
from vrp_format_matcher.documents.parameters import DocumentParameterRecognizer
from vrp_format_matcher.models import CommandLocation, FormatError
from vrp_parser_automaton.automata.model import PatternSource
from vrp_parser_automaton.parameters import default_parameter_registry
from vrp_parser_automaton.patterns import PatternParser
from vrp_parser_automaton.patterns.lexer import RecognizedParameter


@dataclass(frozen=True)
class CommandCatalog:
    """Flatten a v1 container while retaining every original record location."""

    info: dict[str, Any]
    commands: tuple[Mapping[str, Any], ...]
    locations: tuple[CommandLocation, ...]

    @classmethod
    def read(cls, source: Mapping[str, Any]) -> CommandCatalog:
        if not isinstance(source, Mapping):
            raise FormatError("catalog must be an object")
        if (
            type(source.get("schema_version")) is not int
            or source["schema_version"] != 1
        ):
            raise FormatError("catalog schema_version must be 1")
        if source.get("source") not in ("device", "documentation"):
            raise FormatError("catalog source must be device or documentation")
        for field in ("vendor", "device", "model_type"):
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                raise FormatError(f"catalog {field} must be a nonempty string")

        groups: Mapping[str | None, Any]
        if source.get("type") == "flat":
            if "views" in source or "entry_view" in source:
                raise FormatError("flat catalog cannot contain views or entry_view")
            groups = {None: source.get("commands")}
        elif source.get("type") == "grouped":
            if "commands" in source:
                raise FormatError("grouped catalog cannot contain commands")
            views = source.get("views")
            if not isinstance(views, Mapping) or not all(
                isinstance(name, str) and name.strip() for name in views
            ):
                raise FormatError("catalog views must map nonempty names to arrays")
            entry_view = source.get("entry_view")
            if not isinstance(entry_view, str) or entry_view not in views:
                raise FormatError("catalog entry_view must reference an existing view")
            groups = views
        else:
            raise FormatError("catalog type must be flat or grouped")

        commands: list[Mapping[str, Any]] = []
        locations = []
        for view, records in groups.items():
            if not isinstance(records, list):
                raise FormatError(f"catalog commands must be an array (view={view!r})")
            for index, record in enumerate(records):
                location = (
                    f"commands[{index}]"
                    if view is None
                    else f"views[{view!r}][{index}]"
                )
                if not isinstance(record, Mapping):
                    raise FormatError(f"{location} must be a command object")
                pattern = record.get("format")
                if (
                    not isinstance(pattern, str)
                    or not pattern.strip()
                    or any(char in pattern for char in "\r\n")
                ):
                    raise FormatError(
                        f"{location}.format must be a nonempty single line"
                    )
                if "id" in record:
                    raise FormatError(
                        f"{location}: v1 command IDs are generated internally"
                    )
                if (
                    source["source"] == "documentation"
                    and "parameter_types" not in record
                ):
                    raise FormatError(
                        f"{location}: documentation requires parameter_types"
                    )
                if source["source"] == "device" and "parameter_types" in record:
                    raise FormatError(f"{location}: device types belong in the format")
                if "switch_to_view" in record:
                    target = record["switch_to_view"]
                    if (
                        view is None
                        or target is not None
                        and target != {"status": "unresolved"}
                        and (not isinstance(target, str) or target not in groups)
                    ):
                        raise FormatError(
                            f"{location}: invalid switch_to_view reference"
                        )
                commands.append(record)
                locations.append(CommandLocation(view, index))
        if not commands:
            raise FormatError("catalog must contain at least one command")
        info = deepcopy(
            {k: v for k, v in source.items() if k not in ("commands", "views")}
        )
        return cls(info, tuple(commands), tuple(locations))


class TargetParameterRecognizer:
    def __init__(self) -> None:
        self._named = DocumentParameterRecognizer()
        self._typed = default_parameter_registry()

    def recognize(self, source: str, position: int) -> RecognizedParameter | None:
        return self._named.recognize(source, position) or self._typed.recognize(
            source, position
        )


@dataclass(frozen=True)
class TargetFormats:
    formats: Sequence[str]
    syntax: Literal["device", "document"] = "device"
    documents: Sequence[Mapping[str, Any]] | None = None

    def patterns(self) -> tuple[PatternSource, ...]:
        if isinstance(self.formats, (str, bytes)):
            raise FormatError("target formats must be an array of strings")
        if self.syntax not in {"device", "document"}:
            raise ValueError("target syntax must be 'device' or 'document'")
        parser = PatternParser(
            DocumentParameterRecognizer()
            if self.syntax == "document"
            else TargetParameterRecognizer()
        )
        patterns = []
        occurrences: dict[str, int] = defaultdict(int)
        for index, source in enumerate(self.formats):
            if not isinstance(source, str) or not source.strip():
                raise FormatError(f"invalid target format at index {index}")
            ast = (
                DocumentSource(self.documents[index], f"target:{index}").parsed().ast
                if self.documents is not None
                else parser.parse(source)
            )
            digest = sha256(source.encode("utf-8")).hexdigest()[:20]
            pattern_id = f"pattern:{digest}:{occurrences[source]}"
            occurrences[source] += 1
            patterns.append(PatternSource(pattern_id, index, source, ast))
        return tuple(patterns)
