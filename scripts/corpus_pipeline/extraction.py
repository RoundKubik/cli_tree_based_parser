"""Resumable, schema-constrained Codex extraction and catalog assembly."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from .contract import RESPONSE_SCHEMA, validate_response
from .parameter_annotations import complete_types
from .source import Corpus, Page, pattern_parameters, read_json, write_json

PROMPT_PATH = Path(__file__).with_name("prompt.txt")
# Every request and its cache key use the same instructions throughout one run.
EXTRACTION_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


def page_input(page: Page, corpus: Corpus) -> dict[str, Any]:
    return {
        "source": page.source,
        "title": page.data.get("PageTitle", ""),
        "parent_views": page.views,
        "formats": [
            {
                "format_index": index,
                "format": pattern,
                "parameter_names": list(
                    dict.fromkeys(
                        node.declaration.name for node in pattern_parameters(pattern)
                    )
                ),
            }
            for index, pattern in page.included_formats.items()
        ],
        "documentation": {
            key: page.data.get(key, "")
            for key in (
                "FuncDef",
                "ParaDef",
                "UsageGuidelines",
                "Examples",
                "ExtraInfo",
            )
        },
        "related_commands": corpus.related_commands(page),
    }


@dataclass(frozen=True)
class Codex:
    executable: str
    model: str
    timeout: int = 300
    prompt: str = EXTRACTION_PROMPT
    schema: dict[str, Any] = field(default_factory=lambda: RESPONSE_SCHEMA)

    def extract(self, payload: dict[str, Any]) -> Any:
        prompt = self.prompt
        prompt += "\nINPUT JSON:\n" + json.dumps(payload, ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix="vrp-extraction-") as temporary:
            directory = Path(temporary)
            schema = directory / "schema.json"
            result = directory / "answer.json"
            write_json(schema, self.schema)
            command = [
                self.executable,
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                self.model,
                "-c",
                'model_reasoning_effort="low"',
                "--cd",
                str(directory),
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(result),
                "-",
            ]
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode:
                raise RuntimeError("Codex failed: " + completed.stderr[-1500:])
            if not result.exists():
                raise RuntimeError("Codex did not produce a response file")
            return read_json(result)


def fingerprint(payload: dict[str, Any], model: str) -> str:
    content = json.dumps(
        [payload, RESPONSE_SCHEMA, model, EXTRACTION_PROMPT],
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()


def extract_page(
    page: Page,
    corpus: Corpus,
    agent: Codex,
    directory: Path,
) -> tuple[Any, bool]:
    """Persist the entire agent response before any semantic validation."""
    page.validate()
    payload = page_input(page, corpus)
    digest = fingerprint(payload, agent.model)
    cache = directory / "pages" / page.path.name
    if cache.exists():
        saved = read_json(cache)
        if saved.get("fingerprint") == digest:
            return saved["response"], True
    response = agent.extract(payload)
    write_json(cache, {"fingerprint": digest, "response": response})
    return response, False


def validate_responses(
    corpus: Corpus,
    selected: list[Page],
    responses: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Check saved responses without rewriting or discarding the originals."""
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for page in selected:
        name = page.path.name
        if name not in responses:
            continue
        try:
            results[name] = validate_response(responses[name], page, corpus)
        except (ValueError, ValidationError) as error:
            errors[name] = "validation: " + str(error)[:2000]
    return results, errors


def extract_corpus(
    corpus: Corpus,
    selected: list[Page],
    agent: Codex,
    directory: Path,
    workers: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    responses: dict[str, Any] = {}
    errors = dict(corpus.errors)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(extract_page, page, corpus, agent, directory): page
            for page in selected
        }
        for done, future in enumerate(as_completed(futures), 1):
            page = futures[future]
            try:
                response, cached = future.result()
                responses[page.path.name] = response
                state = "cached" if cached else "extracted"
            except (
                ValueError,
                OSError,
                RuntimeError,
                subprocess.TimeoutExpired,
            ) as error:
                errors[page.path.name] = "extraction: " + str(error)[:2000]
                state = "failed"
            print(f"[{done}/{len(selected)}] {state}: {page.path.name}", flush=True)
    print("Collection finished; validating saved responses.", flush=True)
    results, validation_errors = validate_responses(corpus, selected, responses)
    return results, {**errors, **validation_errors}


def view_key(name: str) -> str:
    """Normalize spelling only; never infer that two different views are equal."""
    return " ".join(name.split()).casefold()


def assemble(
    corpus: Corpus,
    selected: list[Page],
    results: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
    header: dict[str, Any],
    directory: Path,
) -> dict[str, Any]:
    views: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    for page in selected:
        for name in page.views:
            names.setdefault(view_key(name), name)
    commands: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    sources: list[dict[str, Any]] = []
    type_changes = []
    for page in selected:
        records = results.get(page.path.name, [])
        if records:
            sources.append(
                {
                    **page.source,
                    "command_start": len(commands),
                    "command_count": len(records),
                    "format_indices": [item["format_index"] for item in records],
                }
            )
        for annotation in records:
            index = annotation["format_index"]
            pattern = page.formats[index]
            pending = list(annotation["issues"])
            target = annotation["switch_to_view"]
            types, changes = complete_types(
                page, pattern, annotation["parameter_types"]
            )
            type_changes.extend(
                {"file": page.path.name, "format_index": index, **edit}
                for edit in changes
            )
            canonical = names.get(view_key(target)) if target else None
            if target and canonical is None:
                pending.append(
                    {
                        "field": "view_membership",
                        "message": f"No ParentView group for target: {target}",
                    }
                )
            command = {
                "format": pattern,
                "description": annotation["description"],
                "parent_views": page.views,
                "switch_to_view": canonical or target,
                "parameter_types": types,
                "creates": annotation["creates"],
                "requires": annotation["requires"],
            }
            fields = {issue["field"] for issue in pending}
            commands.append(command)
            counts["formats_annotated"] += 1
            counts["formats_complete"] += not annotation["issues"]
            counts["transitions_established"] += (
                bool(target) and "switch_to_view" not in fields
            )
            counts["transitions_unresolved"] += "switch_to_view" in fields
            counts["targets_without_groups"] += bool(target) and canonical is None
            counts["unresolved_requirement_contexts"] += sum(
                rule.get("context") == "unresolved" for rule in command["requires"]
            )
            if pending:
                issues.append(
                    {"file": page.path.name, "format_index": index, "issues": pending}
                )
            grouped = {**command}
            if "switch_to_view" in fields:
                grouped["switch_to_view"] = {"status": "unresolved"}
            for view in page.views:
                views.setdefault(names[view_key(view)], []).append(grouped)
    for view in names.values():
        views.setdefault(view, [])
    entry = names.get(view_key(header["entry_view"]))
    if entry is None:
        raise ValueError(
            f"Entry view absent from selected pages: {header['entry_view']}"
        )
    common = {key: value for key, value in header.items() if key != "entry_view"}
    write_json(
        directory / "documentation_flat.json",
        {
            **common,
            "type": "flat",
            "commands": commands,
        },
    )
    write_json(
        directory / "documentation_grouped.json",
        {
            **common,
            "type": "grouped",
            "entry_view": entry,
            "views": views,
        },
    )
    report = {
        "corpus_pages": (
            len(corpus.pages) + len(corpus.errors) + corpus.display_pages_skipped
        ),
        "display_pages_skipped": corpus.display_pages_skipped,
        "display_formats_skipped": corpus.display_formats_skipped,
        "pages_selected": len(selected),
        "pages_processed": len(results),
        "pages_failed": len(errors),
        "pages_validation_failed": sum(
            error.startswith("validation: ") for error in errors.values()
        ),
        **{
            key: counts[key]
            for key in (
                "formats_annotated",
                "formats_complete",
                "transitions_established",
                "transitions_unresolved",
                "targets_without_groups",
                "unresolved_requirement_contexts",
            )
        },
        "errors": errors,
        "issues": issues,
        "sources": sources,
        "type_changes": type_changes,
    }
    write_json(directory / "report.json", report)
    return report
