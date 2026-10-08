"""Complete type lists without replacing unknown representations by guessed types."""

import re
from copy import deepcopy
from pathlib import Path

from vrp_format_matcher.documents.parameters import declared_types

from .mock_device import parameter_description
from .source import Corpus, pattern_parameters, read_json, write_json


def complete_types(page, pattern, annotations):
    """Fill omissions only from an explicit string declaration; retain other gaps."""
    by_name = declared_types({"parameter_types": annotations})
    changes = []
    names = dict.fromkeys(node.declaration.name for node in pattern_parameters(pattern))
    if extra := by_name.keys() - names.keys():
        raise ValueError(
            f"Type annotations reference absent parameters: {sorted(extra)}"
        )
    for name in names:
        if by_name.get(name) not in (None, "unknown"):
            continue
        description = parameter_description(page, name)
        # Quoted strings are still strings. Their runtime reader is a separate concern.
        declared_string = re.search(
            r"\b(?:value|name) is a string\b", description, re.IGNORECASE
        )
        type_id = "string" if declared_string else "unknown"
        if by_name.get(name) != type_id:
            changes.append(
                {
                    "parameter_name": name,
                    "before": by_name.get(name),
                    "after": type_id,
                    "evidence": description,
                }
            )
        by_name[name] = type_id
    return [
        {"parameter_name": name, "parameter_type": by_name[name]} for name in names
    ], changes


def repair_types(
    input_directory, hierarchy_directory, corpus_directory, output_directory
):
    """Write separate catalogs, preserving raw responses, formats and effects."""
    source = Path(input_directory)
    hierarchy = Path(hierarchy_directory)
    output = Path(output_directory)
    if output.resolve() in {source.resolve(), hierarchy.resolve()}:
        raise ValueError("Use a separate output directory for type repairs")
    flat = read_json(source / "documentation_flat.json")
    grouped = read_json(hierarchy / "documentation_grouped.json")
    extraction = read_json(source / "report.json")
    recovery = read_json(hierarchy / "hierarchy_report.json")
    corpus = Corpus(Path(corpus_directory))
    changes = []
    visited = set()
    for location in extraction["sources"]:
        page = corpus.pages[location["file"]]
        for offset, format_index in enumerate(location["format_indices"]):
            index = location["command_start"] + offset
            if index in visited:
                raise ValueError("Duplicate command in extraction source index")
            visited.add(index)
            command = flat["commands"][index]
            if command["format"] != page.formats[format_index]:
                raise ValueError("Type repair source format differs from annotation")
            annotations, edits = complete_types(
                page, command["format"], command["parameter_types"]
            )
            command["parameter_types"] = annotations
            changes.extend(
                {"command_index": index, "file": page.path.name, **edit}
                for edit in edits
            )
    if visited != set(range(len(flat["commands"]))):
        raise ValueError("Extraction sources must cover every command")
    if recovery["origins"].keys() != grouped["views"].keys():
        raise ValueError("Hierarchy origins must cover every group")
    for view, positions in recovery["origins"].items():
        for command, position in zip(grouped["views"][view], positions, strict=True):
            original = flat["commands"][position]
            if command["format"] != original["format"]:
                raise ValueError("Hierarchy origin differs from annotated format")
            command["parameter_types"] = deepcopy(original["parameter_types"])
    if recovery["shared_views"]:
        grouped["shared_views"] = recovery["shared_views"]
    unknown = [
        {"command_index": index, "parameters": names}
        for index, command in enumerate(flat["commands"])
        if (
            names := [
                p["parameter_name"]
                for p in command["parameter_types"]
                if p["parameter_type"] == "unknown"
            ]
        )
    ]
    report = {
        "formats": len(flat["commands"]),
        "formats_with_unknown_types": len(unknown),
        "changes": changes,
        "unknown_types": unknown,
    }
    write_json(output / "documentation_flat.json", flat)
    write_json(output / "documentation_grouped.json", grouped)
    write_json(output / "type_report.json", report)
    return report
