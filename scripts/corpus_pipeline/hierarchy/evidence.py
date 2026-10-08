"""Retrieve source passages, preserve model answers and verify their provenance."""

import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from ..contract import TEXT, array_schema, object_schema
from ..source import read_json, write_json
from .input import ViewNames

PROMPT = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
SCHEMA = object_schema(
    {
        "kind": {
            "enum": [
                "alias",
                "group",
                "ambiguous",
                "missing",
                "unknown",
                "exit_parent",
                "exit_view",
            ]
        },
        "target": {"type": ["string", "null"], "minLength": 1},
        "members": array_schema(TEXT),
        "evidence": array_schema(
            object_schema(
                {
                    "file": TEXT,
                    "field": {
                        "enum": [
                            "FuncDef",
                            "UsageGuidelines",
                            "ParaDef",
                            "ParentView",
                            "Examples",
                        ]
                    },
                    "quote": TEXT,
                }
            )
        ),
        "explanation": TEXT,
    }
)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def terms(text):
    return set(re.findall(r"[a-z0-9]+", text.casefold())) - {
        "view",
        "views",
        "the",
        "of",
        "a",
        "an",
        "and",
        "command",
        "family",
        "address",
    }


def related_files(page):
    return {
        filename
        for topic in page.data.get("related_topics", [])
        for filename in topic.get("target_files", [])
    }


class EvidenceRequests:
    def __init__(self, annotated, global_views):
        self.annotated = annotated
        self.global_views = global_views
        self.by_view = {}
        for page in annotated.corpus.pages.values():
            for view in page.views:
                self.by_view.setdefault(view, []).append(page)
        self.links = {
            name: related_files(page) for name, page in annotated.corpus.pages.items()
        }
        self.names = ViewNames(v for v in annotated.views if v not in global_views)

    def build(self):
        groups = {}
        for i, command in enumerate(self.annotated.commands):
            target = command.get("switch_to_view")
            uncertain = self.annotated.transition_issue(i)
            if target is None and not uncertain:
                continue
            location = self.annotated.locations[i]
            page = self.annotated.corpus.pages[location["file"]]
            exits = re.search(
                r"\b(?:quits?|returns?|exits?|leaves?)\b",
                page.data.get("FuncDef", ""),
                re.I,
            )
            matches = self.names.find(target)[0] if isinstance(target, str) else []
            if len(matches) == 1 and not exits:
                continue
            key = (page.path.name, target)
            groups.setdefault(key, []).append(i)
        return {str(indices[0]): self._request(indices) for indices in groups.values()}

    def _request(self, indices):
        annotated = self.annotated
        first = annotated.commands[indices[0]]
        anchor = annotated.corpus.pages[annotated.locations[indices[0]]["file"]]
        focus = terms(first.get("switch_to_view") or anchor.data["FuncDef"])
        linked = {
            name
            for name, links in self.links.items()
            if anchor.path.name in links or name in self.links[anchor.path.name]
        }
        linked_views = {
            view for name in linked for view in annotated.corpus.pages[name].views
        }
        # This ranking retrieves passages only. It never resolves a view.
        candidates = sorted(
            (
                v
                for v in self.by_view
                if v not in self.global_views
                and (v in linked_views or terms(v) & focus)
            ),
            key=lambda v: (
                not bool(terms(v) & focus),
                v not in linked_views,
                v not in annotated.views,
                -len(terms(v) & focus),
                v,
            ),
        )[:12]
        if re.search(
            r"\b(?:quits?|returns?|exits?|leaves?)\b",
            anchor.data.get("FuncDef", ""),
            re.I,
        ):
            # An exit description usually contains its whole effect. Do not pull
            # every page that happens to use an exit in a configuration example.
            candidates = []
        pages = {anchor.path.name: anchor}
        literal = re.match(r"[\w-]+(?:\s+[\w-]+)*", first["format"])
        prefix = literal.group() if literal else first["format"].split()[0]
        reference = re.compile(r"\b" + re.escape(prefix) + r"\s+command\b", re.I)
        for view in candidates:
            ranked = sorted(
                self.by_view[view],
                key=lambda p: (
                    p.path.name not in linked,
                    not reference.search(p.data.get("UsageGuidelines", "")),
                    len(p.views) != 1,
                    len(str(p.data.get("UsageGuidelines", ""))),
                    p.path.name,
                ),
            )
            # Prefer explicit source links and keep a second supporting page.
            # These are retrieval hints, never proof of view equivalence.
            supporting = [ranked[0]]
            if len(ranked) > 1 and ranked[1].path.name in linked:
                supporting.append(ranked[1])
            for page in supporting:
                pages[page.path.name] = page
        return {
            "requested_target": first.get("switch_to_view"),
            "commands": [
                {
                    "index": i,
                    "format": annotated.commands[i]["format"],
                    "parent_views": annotated.commands[i]["parent_views"],
                    "issues": annotated.transition_issue(i),
                }
                for i in indices
            ],
            "available_views": list(self.names.names),
            "candidate_views": list(dict.fromkeys([*self.names.names, *candidates])),
            "pages": [
                {
                    "file": p.path.name,
                    "PageTitle": p.data.get("PageTitle", ""),
                    "related_topics": [
                        {
                            k: t[k]
                            for k in ("title", "target_files", "reference_kind")
                            if k in t
                        }
                        for t in p.data.get("related_topics", [])
                    ],
                    "Examples": p.data.get("Examples", [])[:3],
                    "examples_total": len(p.data.get("Examples", [])),
                    **{
                        field: p.data.get(field, [])
                        for field in (
                            "ParentView",
                            "FuncDef",
                            "UsageGuidelines",
                            "ParaDef",
                        )
                    },
                }
                for p in pages.values()
            ],
        }


def validate_evidence(answer, request):
    Draft202012Validator(SCHEMA).validate(answer)
    pages = {p["file"]: p for p in request["pages"]}
    covered = set()
    named_scopes = set()
    quotes = []
    for citation in answer["evidence"]:
        page = pages.get(citation["file"])
        if page is None or not any(
            citation["quote"] in s for s in strings(page[citation["field"]])
        ):
            raise ValueError(f"Unverifiable source quotation: {citation}")
        if citation["field"] == "ParentView":
            if citation["quote"] not in page["ParentView"]:
                raise ValueError("Quote a complete ParentView entry, not a fragment")
            named_scopes.add(citation["quote"])
        if citation["field"] != "ParentView":
            quotes.append(citation["quote"])
            covered.update(page["ParentView"])
            covered.update(
                v
                for v in request["candidate_views"]
                if v.casefold() in citation["quote"].casefold()
            )
    kind, target, members = answer["kind"], answer["target"], answer["members"]
    if len(set(members)) != len(members) or set(members) - set(
        request["candidate_views"]
    ):
        raise ValueError("Members must be distinct documented candidate views")
    if kind in {"alias", "group", "exit_parent", "exit_view"} and not quotes:
        raise ValueError("A resolved relationship requires prose evidence")
    if kind in {"alias", "exit_view"}:
        if target is None or members:
            raise ValueError("A fixed transition requires one target, without members")
        if target not in covered | named_scopes:
            raise ValueError("The evidence does not identify the target scope")
    if kind == "group":
        if not target or not members or target in members:
            raise ValueError("A group requires a distinct generic target and members")
        if target != request["requested_target"]:
            raise ValueError("Preserve the original generic target name")
        if set(members) - covered:
            raise ValueError("Every group member requires prose evidence")
    if kind == "exit_parent" and (target is not None or members):
        raise ValueError("A parent exit has no fixed target or member list")
    if kind in {"unknown", "missing"} and members:
        raise ValueError("Use ambiguous when retaining candidate views")
    if kind == "missing" and not target:
        raise ValueError("A missing group requires a known target name")
    return answer


def collect_evidence(requests, agent, directory, workers):
    agent = replace(agent, prompt=PROMPT, schema=SCHEMA)
    write_json(directory / "requests.json", requests)
    write_json(directory / "response.schema.json", SCHEMA)
    (directory / "prompt.txt").write_text(PROMPT, encoding="utf-8")

    def collect(key, request):
        digest = hashlib.sha256(
            json.dumps([request, PROMPT, SCHEMA, agent.model], sort_keys=True).encode()
        ).hexdigest()
        path = directory / "answers" / f"{key}.json"
        if path.exists():
            saved = read_json(path)
            if saved.get("fingerprint") == digest:
                return saved["response"]
            write_json(
                directory / "history" / key / f"{saved['fingerprint']}.json", saved
            )
        response = agent.extract(request)
        write_json(
            path, {"fingerprint": digest, "request": request, "response": response}
        )
        return response

    responses, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(collect, key, request): key
            for key, request in requests.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                responses[key] = future.result()
            except (
                ValueError,
                OSError,
                RuntimeError,
                subprocess.TimeoutExpired,
            ) as error:
                errors[key] = str(error)
            print(
                f"Hierarchy evidence: {len(responses) + len(errors)}/{len(requests)}",
                flush=True,
            )
    answers = {}
    for key, response in responses.items():
        try:
            answers[key] = validate_evidence(response, requests[key])
        except (ValueError, ValidationError) as error:
            errors[key] = str(error)
    return answers, errors
