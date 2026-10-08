"""Agent response schema and checks that require the original command format."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from vrp_format_matcher.comparison.parameter_types import DOCUMENT_PARAMETER_TYPES

from .selectors import ParameterCombination
from .source import Corpus, Page, pattern_parameters

# One contract for extraction and matching; unknown never proves hierarchy.
PARAMETER_TYPES = tuple(sorted(DOCUMENT_PARAMETER_TYPES))


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def array_schema(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


TEXT = {"type": "string", "minLength": 1}
ENTITY = {"kind": {"const": "entity"}, "entity_type": TEXT}
RULE = {
    "anyOf": [
        object_schema({**ENTITY, "parameter_name": TEXT}),
        object_schema(
            {
                **ENTITY,
                "parameter_comb": {
                    **array_schema(TEXT),
                    "minItems": 2,
                },
            }
        ),
        object_schema(ENTITY),
        object_schema(
            {
                "kind": {"const": "command"},
                "name": TEXT,
                "format": TEXT,
                "context": {"enum": ["system", "current", "unresolved"]},
            }
        ),
    ]
}
RESPONSE_SCHEMA = object_schema(
    {
        "commands": array_schema(
            object_schema(
                {
                    "format_index": {"type": "integer", "minimum": 0},
                    "description": TEXT,
                    "switch_to_view": {"type": ["string", "null"], "minLength": 1},
                    "switch_evidence": {"type": ["string", "null"], "minLength": 1},
                    "parameter_types": array_schema(
                        object_schema(
                            {
                                "parameter_name": TEXT,
                                "parameter_type": {"enum": list(PARAMETER_TYPES)},
                            }
                        )
                    ),
                    "creates": array_schema(RULE),
                    "requires": array_schema(RULE),
                    "issues": array_schema(
                        object_schema(
                            {
                                "field": {
                                    "enum": [
                                        "switch_to_view",
                                        "parameter_types",
                                        "creates",
                                        "requires",
                                    ]
                                },
                                "message": TEXT,
                            }
                        )
                    ),
                }
            )
        )
    }
)


def validate_response(
    response: Any,
    page: Page,
    corpus: Corpus,
) -> list[dict[str, Any]]:
    Draft202012Validator(RESPONSE_SCHEMA).validate(response)
    records = response["commands"]
    indices = [record["format_index"] for record in records]
    if sorted(indices) != list(page.included_formats):
        raise ValueError("Return each original format_index exactly once")
    records = sorted(records, key=lambda item: item["format_index"])
    source_text = "\n".join(str(value) for value in page.data.values())
    for record in records:
        pattern = page.formats[record["format_index"]]
        parameters = pattern_parameters(pattern)
        names = {node.declaration.name for node in parameters}
        annotations = [item["parameter_name"] for item in record["parameter_types"]]
        if len(set(annotations)) != len(annotations) or set(annotations) - names:
            raise ValueError(f"Invalid parameter_types names in {pattern!r}")
        issues = {item["field"] for item in record["issues"]}
        missing = sorted(names - set(annotations))
        unknown = [
            item["parameter_name"]
            for item in record["parameter_types"]
            if item["parameter_type"] == "unknown"
        ]
        if (missing or unknown) and "parameter_types" not in issues:
            raise ValueError(
                "Missing parameter type information at "
                f"format_index={record['format_index']}: "
                f"{sorted(set(missing + unknown))}. Annotate each name or explain it "
                "in a parameter_types issue."
            )
        target = record["switch_to_view"]
        quote = record["switch_evidence"]
        if target is not None and (
            not quote
            or quote not in source_text
            or target.casefold() not in quote.casefold()
        ):
            raise ValueError(
                f"Transition target {target!r} needs a verbatim source quote "
                f"containing that view name; received {quote!r}. If no such "
                "evidence exists, return null with a switch_to_view issue."
            )
        if "switch_to_view" in issues and target is not None:
            raise ValueError("An uncertain transition must not assert a target")
        for field in ("creates", "requires"):
            for rule in record[field]:
                if rule["kind"] == "command":
                    if rule["format"] not in corpus.command_formats.get(
                        rule["name"], set()
                    ):
                        raise ValueError(
                            "A command dependency must name an original format: "
                            f"name={rule['name']!r}, format={rule['format']!r}. "
                            "Copy name and format from the same related_commands entry."
                        )
                    continue
                selected = rule.get(
                    "parameter_comb",
                    [rule["parameter_name"]] if "parameter_name" in rule else [],
                )
                if set(selected) - names:
                    raise ValueError(f"Unknown semantic parameter in {pattern!r}")
                if len(set(selected)) != len(selected):
                    raise ValueError("parameter_comb must not repeat parameter names")
                if (
                    len(selected) > 1
                    and not ParameterCombination(pattern, tuple(selected)).exists()
                ):
                    raise ValueError(
                        "parameter_comb must name adjacent placeholders separated "
                        "only by whitespace in the original format: "
                        f"{selected} at format_index={record['format_index']}. "
                        "Keywords, brackets and alternatives cannot be crossed."
                    )
    return records
