"""Rebuild the documentation fixture hierarchy from text and command examples."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

from vrp_parser_automaton import CommandLineParser
from vrp_parser_automaton.errors import PatternCompilationError


def view_key(name: str) -> str:
    """Normalize spelling only; never infer a more specific view."""
    return " ".join(name.split()).lower()


def switch_evidence(pattern: str, function: str) -> dict[str, str] | None:
    """Recognize a conservative subset of explicit command/view sentences.

    Unsupported wording stays unknown. A command's name may omit parameter
    placeholders, e.g. 'acl number' for 'acl [ number ] <acl-number>'.
    """
    literals = re.sub(r"<[^<>]+>", " ", pattern)
    command = " ".join(re.findall(r"[\w.-]+", literals)).lower()
    statements = []
    for match in re.finditer(
        r"\bThe\s+(?P<command>[\w -]+?)\s+command\s+"
        r"(?P<body>[^.\n]*)(?:\.|$)",
        function,
        re.IGNORECASE | re.MULTILINE,
    ):
        subject = view_key(match["command"])
        if command == subject or command.startswith(subject + " "):
            statements.append((subject, match))
    if not statements:
        return None

    # Prefer a description explicitly naming the more specific command form.
    specificity = max(len(subject.split()) for subject, _ in statements)
    excluded_words = (
        "and|or|in|on|at|of|for|from|to|with|without|about|the|a|an|"
        "current|previous|parent|specified|corresponding|information|"
        "configuration|displays?|enters?|opens?|creates?|view"
    )
    noun = rf"(?!(?:{excluded_words})\b)[\w-]+"
    target_pattern = re.compile(
        rf"\b(?:displays?|enters?|opens?|creates?)\s+(?:the\s+|an?\s+)?"
        rf"(?P<view>(?:{noun}\s+){{1,8}}view)\b",
        re.IGNORECASE,
    )
    targets: dict[str, dict[str, str]] = {}
    for subject, statement in statements:
        if len(subject.split()) != specificity:
            continue
        body = statement["body"]
        if re.search(
            r"\b(?:not|never|cannot|can't|doesn't|without|if|when|unless|"
            r"can|may|might|could|only)\b",
            body,
            re.IGNORECASE,
        ):
            return None
        mentions = list(target_pattern.finditer(body))
        if mentions and len(re.findall(r"\bview\b", body, re.IGNORECASE)) != len(
            mentions
        ):
            # E.g. 'enters the Basic ACL view or the Advanced ACL view'.
            return None
        for mention in mentions:
            raw_view = mention["view"]
            targets.setdefault(
                view_key(raw_view),
                {"field": "FuncDef", "text": statement[0], "view": raw_view},
            )
    return next(iter(targets.values())) if len(targets) == 1 else None


class ExampleFormats:
    """Match example syntax with wildcard parameters, independently of types."""

    def __init__(self, commands: list[str]) -> None:
        self._parser = CommandLineParser(
            {
                "commands": [
                    re.sub(r"(?<!&)<[^<>]+>", "STRING<1-65535>", command)
                    for command in commands
                ]
            }
        )

    def matching(self, command: str) -> set[int]:
        if not command.strip():
            return set()
        result = self._parser.parse(command)
        if not result.parsed:
            return set()
        return {match.pattern_index for match in result.matches}


def prompt_command(line: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"\s*(<[^>]+>|\[[^]]+\])\s*(.*?)\s*", line)
    if match is None:
        return None
    # The commit marker changes independently of the current view.
    prompt = re.sub(r"^\[[*~]", "[", match[1])
    return prompt, match[2]


def example_switches(
    commands: list[dict[str, Any]], pages: dict[str, Any], entry_view: str
) -> dict[int, dict[str, dict[str, str]]]:
    """Link a changed prompt to the documented view of its final command.

    Only single-ParentView pages are anchors. The final example command must
    match that page's syntax. An entry command must match a selected format in
    a known source view; identical syntax in an unrelated view is excluded.
    """
    formats = ExampleFormats([command["format"] for command in commands])
    observations: dict[int, dict[str, dict[str, str]]] = {}
    for filename, page in pages.items():
        if len(page.get("ParentView", [])) != 1:
            continue
        target = page["ParentView"][0]
        try:
            page_formats = ExampleFormats(page["CLIs"])
        except PatternCompilationError:
            continue
        for example in page.get("Examples", []):
            lines = [parsed for line in example if (parsed := prompt_command(line))]
            if len(lines) < 2 or not page_formats.matching(lines[-1][1]):
                continue
            final_prompt = lines[-1][0]
            known: dict[str, set[str]] = {}
            for index, ((prompt, text), (next_prompt, _)) in enumerate(
                zip(lines, lines[1:], strict=False)
            ):
                if text == "system-view" and prompt.startswith("<"):
                    known[next_prompt] = {entry_view}
                    continue
                candidates = {
                    candidate
                    for candidate in formats.matching(text)
                    if view_key(commands[candidate]["metadata"]["parent_view"])
                    in known.get(prompt, set())
                }
                if prompt == next_prompt or text.split()[:1] in (
                    ["undo"], ["quit"], ["return"]
                ):
                    continue
                destinations = {
                    commands[candidate]["switch_to_view"]
                    for candidate in candidates
                    if "switch_to_view" in commands[candidate]
                }
                if destinations:
                    known[next_prompt] = destinations
                if next_prompt != final_prompt:
                    continue
                if any(p != final_prompt for p, _ in lines[index + 1 :]):
                    continue
                for candidate in candidates:
                    observations.setdefault(candidate, {}).setdefault(
                        view_key(target),
                        {
                            "field": "Examples",
                            "corpus_file": filename,
                            "text": "\n".join(example),
                            "view": target,
                        },
                    )
    return observations


def restored_views(
    commands: list[dict[str, Any]], pages: dict[str, Any], entry_view: str
) -> dict[str, list[dict[str, Any]]]:
    observations = example_switches(commands, pages, entry_view)
    destinations: dict[str, set[str]] = {}
    for index, targets in observations.items():
        command = commands[index]
        if "switch_to_view" not in command and len(targets) == 1:
            target, evidence = next(iter(targets.items()))
            command["switch_to_view"] = target
            command["metadata"]["switch_to_view_source"] = evidence
        if "switch_to_view" in command:
            for parent in targets:
                destinations.setdefault(parent, set()).add(command["switch_to_view"])

    ambiguous = {
        name: targets for name, targets in destinations.items() if len(targets) > 1
    }
    if ambiguous:
        raise ValueError(f"conflicting documentation view destinations: {ambiguous}")
    aliases = {name: next(iter(targets)) for name, targets in destinations.items()}
    views: dict[str, list[dict[str, Any]]] = {}
    for command in commands:
        parent = view_key(command["metadata"]["parent_view"])
        views.setdefault(aliases.get(parent, parent), []).append(command)
    validate_hierarchy(views, entry_view)
    return views


def validate_hierarchy(views: dict[str, list[dict[str, Any]]], entry_view: str) -> None:
    """Require populated targets and reachability before overwriting fixtures."""
    if entry_view not in views:
        raise ValueError(f"missing entry view: {entry_view}")
    reachable = {entry_view}
    pending = [entry_view]
    while pending:
        for command in views[pending.pop()]:
            target = command.get("switch_to_view")
            if target is None:
                continue
            if not views.get(target):
                raise ValueError(f"missing commands for target view: {target}")
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    if unreachable := views.keys() - reachable:
        raise ValueError(f"unreachable documentation views: {sorted(unreachable)}")


def refresh(corpus: Path, directory: Path) -> dict[str, int]:
    """Restore the selected documentation hierarchy without reading device data."""
    grouped_path = directory / "documentation_grouped.json"
    flat_path = directory / "documentation_flat.json"
    grouped = json.loads(grouped_path.read_text(encoding="utf-8"))
    flat = json.loads(flat_path.read_text(encoding="utf-8"))
    pages: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    for name, commands in grouped["views"].items():
        for command in commands:
            command.pop("switch_to_view", None)
            metadata = command["metadata"]
            metadata.pop("switch_to_view_source", None)
            filename = metadata["corpus_file"]
            if filename not in pages:
                pages[filename] = json.loads(
                    (corpus / filename).read_text(encoding="utf-8")
                )
            page = pages[filename]
            if page["CLIs"][metadata["format_index"]] != command["format"]:
                raise ValueError(f"source format changed: {filename}")
            parent = metadata.get("parent_view", name)
            source_views = {
                view_key(view): view for view in page["ParentView"]
            }
            if view_key(parent) not in source_views:
                raise ValueError(f"source parent view changed: {filename}: {parent}")
            metadata["parent_view"] = source_views[view_key(parent)]
            evidence = switch_evidence(command["format"], page["FuncDef"])
            if evidence is not None:
                target = view_key(evidence["view"])
                command["switch_to_view"] = target
                metadata["switch_to_view_source"] = evidence
            entries.append(command)

    entry_view = view_key(grouped["entry_view"])
    views = restored_views(entries, pages, entry_view)
    grouped["views"] = views
    grouped["entry_view"] = entry_view
    flat["commands"] = [
        {
            key: copy.deepcopy(value)
            for key, value in command.items()
            if key != "switch_to_view"
        }
        for commands in views.values()
        for command in commands
    ]
    for catalog in (grouped, flat):
        metadata = catalog["metadata"]
        metadata["synthetic_fields"] = ["mock creates/requires rules"]
        metadata["switch_to_view_extraction"] = (
            "FuncDef destinations preserved; missing entries and group membership "
            "recovered from documented example prompts and ParentView"
        )
        metadata["view_name_normalization"] = "lowercase and collapsed whitespace"
        metadata["command_count"] = len(entries)
    switches = sum("switch_to_view" in command for command in entries)
    for path, catalog in ((grouped_path, grouped), (flat_path, flat)):
        path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "commands": len(flat["commands"]),
        "views": len(views),
        "switches": switches,
        "unknown_switches": len(flat["commands"]) - switches,
        "empty_views": sum(not commands for commands in views.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument(
        "--catalog-dir", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    print(json.dumps(refresh(args.corpus, args.catalog_dir), indent=2))


if __name__ == "__main__":
    main()
