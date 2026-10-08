"""Build synthetic typed formats from documentation, never real device claims."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from vrp_parser_automaton.parameters.builtins import default_parameter_registry
from vrp_parser_automaton.patterns import PatternParser

from .source import Corpus, Page, pattern_parameters, write_json

# Deliberately broad MOCK ranges: these are not extracted device constraints.
PLACEHOLDERS = {
    "integer": "INTEGER<0-4294967295>",
    "string": "STRING<1-65535>",
    "text": "TEXT<1-65535>",
    "passwordex": "PASSWORDEX<1-65535>",
    "hex": "HEX<0-FFFFFFFF>",
    "mac": "H-H-H",
    "ipv4-address": "X.X.X.X",
    "ipv6-address": "X:X::X:X",
    "ipv6-prefix": "X:X::X:X/M",
    "date-slash": "YYYY/MM/DD",
    "date-iso": "YYYY-MM-DD",
    "month-day": "MM-DD",
    "date-us": "MM-DD-YYYY",
    "datetime-slash": "YYYY/MM/DD,HH:MM:SS",
    "time-seconds": "HH:MM:SS",
    "time": "<hh:mm>",
}


def parameter_description(page: Page, name: str) -> str:
    rows = []
    for item in page.data.get("ParaDef", []):
        tokens = re.findall(
            r"[A-Za-z0-9_][A-Za-z0-9_.:/-]*", item.get("Parameters", "")
        )
        if name in tokens:
            rows.append(item.get("Info", ""))
    return "\n".join(rows)


def mock_placeholder(description: str) -> tuple[str | None, bool]:
    """Recognize documented representations; unknowns retain their source names."""
    text = description.casefold()
    if "dotted notation" in text and re.search(r"\b(?:as number|as-number)\b", text):
        return None, True
    if re.search(r"\b(?:number|address)\s*:\s*\d+-(?:byte|bit)\b", text):
        # Component bounds describe a compound token, not one integer/address.
        # No compound validator is inferred for this synthetic fixture.
        return None, True
    if (
        "x:x::x:x/m" in text
        or re.search(r"ipv6 address\s*/\s*(?:ipv6 address\s*)?prefix", text)
        or re.search(
            r"\bspecifies (?:the |an? )?ipv6 (?:source |destination )?"
            r"address with a prefix\b",
            text,
        )
    ):
        return PLACEHOLDERS["ipv6-prefix"], False
    if re.search(r"\bthe value is a string\b", text):
        # A string identifier may mention an address that its entity is bound to.
        return PLACEHOLDERS["string"], False
    if re.search(r"\bmac address\b|h-h-h", text):
        return PLACEHOLDERS["mac"], False
    if (
        "ipv6" in text
        and re.search(r"\b(prefix length|length|integer)\b", text) is None
    ):
        kind = (
            "ipv6-prefix"
            if "prefix" in text or "x:x::x:x/m" in text
            else "ipv6-address"
        )
        return PLACEHOLDERS[kind], False
    if "dotted decimal" in text or "dotted-decimal" in text or "x.x.x.x" in text:
        return PLACEHOLDERS["ipv4-address"], False
    if "hexadecimal" in text and "string" not in text:
        return PLACEHOLDERS["hex"], False
    if re.search(r"\binteger\b", text):
        bounds = re.search(r"integer ranging from (-?\d+) to (-?\d+)\b", text)
        if bounds and int(bounds[1]) <= int(bounds[2]):
            return f"INTEGER<{bounds[1]}-{bounds[2]}>", False
        return PLACEHOLDERS["integer"], False
    if "ipv4 address" in text or "ipv4-address" in text:
        return PLACEHOLDERS["ipv4-address"], False
    if "string" in text:
        return PLACEHOLDERS["string"], False
    return None, True


def convert_format(page: Page, pattern: str) -> tuple[str, list[str]]:
    unknown: list[str] = []
    converted = pattern
    for node in reversed(pattern_parameters(pattern)):
        name = node.declaration.name
        placeholder, fallback = mock_placeholder(parameter_description(page, name))
        if fallback:
            unknown.append(name)
            placeholder = converted[node.span.start : node.span.end]
        assert placeholder is not None
        converted = (
            converted[: node.span.start] + placeholder + converted[node.span.end :]
        )
    # Validate grammar and every placeholder with the actual device recognizers.
    PatternParser(default_parameter_registry()).parse(converted)
    return converted, sorted(set(unknown))


def build_mock(
    corpus: Corpus,
    header: dict[str, Any],
    directory: Path,
    *,
    shared_views: tuple[str, ...] = (),
) -> dict[str, Any]:
    views: dict[str, list[dict[str, str]]] = {}
    commands: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for page in corpus.pages.values():
        start = len(commands)
        included_indices: list[int] = []
        for index, pattern in page.included_formats.items():
            counts["formats_total"] += 1
            try:
                converted, unknown = convert_format(page, pattern)
            except ValueError as error:
                errors.append(
                    {"file": page.path.name, "format_index": index, "error": str(error)}
                )
                continue
            command = {"format": converted}
            commands.append(command)
            included_indices.append(index)
            for view in page.views:
                views.setdefault(view, []).append(command)
            if unknown:
                fallbacks.append(
                    {
                        "file": page.path.name,
                        "format_index": index,
                        "parameters": unknown,
                    }
                )
                counts["formats_with_unknown_type"] += 1
        sources.append(
            {
                **page.source,
                "command_start": start,
                "command_count": len(commands) - start,
                "format_indices": included_indices,
            }
        )
    if header["entry_view"] not in views:
        raise ValueError("The mock entry view is absent from the corpus")
    if any(view not in views or view == header["entry_view"] for view in shared_views):
        raise ValueError("Shared mock scopes must exist and differ from the entry view")
    common = {key: value for key, value in header.items() if key != "entry_view"}
    common["source"] = "device"
    write_json(
        directory / "device_flat.json",
        {
            **common,
            "type": "flat",
            "commands": commands,
        },
    )
    write_json(
        directory / "device_grouped.json",
        {
            **common,
            "type": "grouped",
            "entry_view": header["entry_view"],
            **(
                {"shared_views": list(dict.fromkeys(shared_views))}
                if shared_views
                else {}
            ),
            "views": views,
        },
    )
    report = {
        "synthetic": True,
        "note": "Documentation view names; synthetic placeholders and default ranges. "
        "No device hierarchy or behavior is established by this fixture.",
        "pages": len(corpus.pages),
        "display_pages_skipped": corpus.display_pages_skipped,
        "display_formats_skipped": corpus.display_formats_skipped,
        "formats_total": counts["formats_total"],
        "formats_converted": len(commands),
        "formats_failed": len(errors),
        "formats_with_unknown_type": counts["formats_with_unknown_type"],
        "errors": errors,
        "page_errors": corpus.errors,
        "unknown_types": fallbacks,
        "sources": sources,
    }
    write_json(directory / "report.json", report)
    return report
