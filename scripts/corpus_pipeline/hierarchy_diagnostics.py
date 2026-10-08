"""Explain unresolved offline targets using the coverage proofs already computed."""

from collections import Counter

from vrp_format_matcher.documents.parameters import declared_types

from .source import pattern_parameters


def hierarchy_diagnostics(prepared, documentation):
    hierarchy = prepared.mapping.hierarchy
    if hierarchy is None:
        return {"targets": {}, "unknown_parameter_types": []}
    targets = {}
    for name, target in hierarchy.targets.items():
        groups = {status: [] for status in ("covered", "partial", "unknown")}
        for candidate in target.candidates:
            if candidate.coverage in groups:
                groups[candidate.coverage].append(candidate.device_view)
        for status in groups:
            groups[status] = sorted(set(groups[status]))
        resolution = (
            "resolved"
            if target.status == "resolved"
            else "ambiguous"
            if len(groups["covered"]) > 1
            else "unknown"
            if groups["unknown"]
            else "partial"
            if groups["partial"]
            else "missing"
        )
        targets[name] = {"status": resolution, **groups}
        if target.device_view is not None:
            targets[name]["device_view"] = target.device_view
    unknown = {}
    unresolved_switches = {}
    for view, commands in documentation.get("views", {}).items():
        for index, command in enumerate(commands):
            types = declared_types(command)
            names = tuple(
                dict.fromkeys(
                    node.declaration.name
                    for node in pattern_parameters(command["format"])
                    if types.get(node.declaration.name, "unknown") == "unknown"
                )
            )
            if names:
                record = unknown.setdefault(
                    (command["format"], names),
                    {
                        "format": command["format"],
                        "parameters": list(names),
                        "sources": [],
                    },
                )
                record["sources"].append({"view": view, "index": index})
            if command.get("switch_to_view") == {"status": "unresolved"}:
                unresolved_switches.setdefault(command["format"], []).append(
                    {"view": view, "index": index}
                )
    return {
        "target_statuses": dict(Counter(t["status"] for t in targets.values())),
        "targets": targets,
        "unknown_parameter_types": list(unknown.values()),
        "unresolved_documentation_switches": unresolved_switches,
        "note": (
            "Covered means the device view covers the local documentation sample. "
            "Partial evidence and type/budget uncertainty do not establish a "
            "transition. An unresolved documentation switch has no inferred "
            "device target."
        ),
    }
