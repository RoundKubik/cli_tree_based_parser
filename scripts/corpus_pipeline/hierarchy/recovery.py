"""Link documented contexts while keeping unknown and aggregate scopes explicit."""

from collections import Counter, defaultdict, deque
from copy import deepcopy

from vrp_parser_automaton.catalogs.source import PatternCatalog

from .input import ViewNames


def reachable(entry, edges):
    found = {entry}
    pending = deque([entry])
    while pending:
        for target in edges.get(pending.popleft(), ()):
            if target not in found:
                found.add(target)
                pending.append(target)
    return found


class DocumentationHierarchy:
    def __init__(self, annotated, entry_view, global_views):
        self.source = annotated
        self.names = ViewNames(annotated.views)
        entries, _ = self.names.find(entry_view)
        if len(entries) != 1:
            raise ValueError(
                "The initial view must resolve to exactly one source group"
            )
        self.entry = entries[0]
        self.globals = set(global_views) & set(annotated.views)
        if self.entry in self.globals:
            raise ValueError("A shared scope cannot be the initial view")

    def recover(self, requests, answers, errors):
        records = deepcopy(self.source.commands)
        origins = {view: [] for view in self.source.views}
        for index, command in enumerate(records):
            for view in dict.fromkeys(command["parent_views"]):
                origins[view].append(index)
        decisions = {}
        for key, request in requests.items():
            for command in request["commands"]:
                decisions[command["index"]] = (key, answers.get(key))
        transitions, aggregates = {}, {}
        for index, command in enumerate(records):
            original = self.source.commands[index].get("switch_to_view")
            issue = self.source.transition_issue(index)
            if original is None and not issue:
                continue
            row = self._initial(index, original, issue)
            if index in decisions:
                key, answer = decisions[index]
                row["evidence"] = key
                if answer:
                    self._apply(row, answer, aggregates)
                elif key in errors:
                    row["evidence_error"] = errors[key]
            transitions[index] = row
            target = row.get("target")
            if row["status"] in {"resolved", "grouped", "exit_view"}:
                command["switch_to_view"] = target
            else:
                command["switch_to_view"] = {"status": "unresolved"}
        # Aggregates contain only positively documented, present source groups.
        # Original groups and parent_views are retained; members are not aliases.
        for target, group in aggregates.items():
            origins[target] = list(
                dict.fromkeys(i for member in group["members"] for i in origins[member])
            )
        shared = list(
            dict.fromkeys(
                i for v in self.source.views if v in self.globals for i in origins[v]
            )
        )
        copies = 0
        for view, indices in origins.items():
            if view not in self.globals:
                for index in shared:
                    if index not in indices:
                        indices.append(index)
                        copies += 1
        views = {
            v: [deepcopy(records[i]) for i in indices] for v, indices in origins.items()
        }
        document = {
            k: deepcopy(v)
            for k, v in self.source.document.items()
            if k not in {"commands", "type", "entry_view", "views"}
        }
        document.update(type="grouped", entry_view=self.entry, views=views)
        if self.globals:
            document["shared_views"] = sorted(self.globals)
        PatternCatalog.read(document)
        graph = self._graph(origins, transitions, aggregates)
        counts = Counter(row["status"] for row in transitions.values())
        methods = Counter(
            row.get("method")
            for row in transitions.values()
            if row["status"] == "resolved"
        )
        report = {
            "summary": {
                "formats": len(records),
                "source_groups": len(self.source.views),
                "output_groups": len(views),
                "aggregate_groups": len(aggregates),
                "exact_links": methods["exact"],
                "normalized_links": methods["normalized"],
                "documented_alias_links": methods["documentation"],
                "shared_formats": len(shared),
                "shared_copies": copies,
                **{
                    s: counts[s]
                    for s in (
                        "resolved",
                        "grouped",
                        "unknown",
                        "ambiguous",
                        "missing",
                        "exit_parent",
                        "exit_view",
                    )
                },
                "evidence_errors": len(errors),
                "reachable_views": len(graph["entered_views"]),
                "unreachable_views": len(graph["unreachable_views"]),
            },
            "transitions": list(transitions.values()),
            "aggregates": aggregates,
            "reachability": graph,
            "shared_views": sorted(self.globals),
            "origins": origins,
            "conflicting_formats": self._conflicts(views, origins),
            "evidence_errors": errors,
            "runtime_limitations": [
                "Exit effects describe documentation; the parser follows indentation, "
                "not interactive quit/return execution.",
                "Aggregate views combine member commands; they do not select a "
                "concrete member or validate parameter-dependent scope.",
                "Evidence quotations are checked for provenance; that check does "
                "not prove the model's semantic interpretation.",
            ],
        }
        return document, report

    def _initial(self, index, target, issue):
        matches, method = (
            self.names.find(target) if isinstance(target, str) else ([], "")
        )
        row = {
            "command_index": index,
            **self.source.locations[index],
            "requested_target": target,
        }
        if len(matches) == 1 and matches[0] not in self.globals:
            row.update(status="resolved", target=matches[0], method=method)
        elif len(matches) > 1:
            row.update(status="ambiguous", candidates=matches)
        elif isinstance(target, str):
            row.update(status="missing")
        else:
            row.update(status="unknown")
        if issue:
            row["source_issues"] = issue
        return row

    def _apply(self, row, answer, aggregates):
        kind, target = answer["kind"], answer["target"]
        row["explanation"] = answer["explanation"]
        for field in ("target", "method", "candidates"):
            row.pop(field, None)
        if kind in {"alias", "exit_view"}:
            matches, _ = self.names.find(target)
            if len(matches) == 1 and matches[0] not in self.globals:
                row.update(
                    status="exit_view" if kind == "exit_view" else "resolved",
                    target=matches[0],
                    method="documentation",
                )
            else:
                row.update(
                    status="ambiguous" if len(matches) > 1 else "missing",
                    documented_target=target,
                    candidates=matches,
                )
        elif kind == "group":
            present = [
                v
                for v in answer["members"]
                if v in self.source.views and v not in self.globals
            ]
            absent = [v for v in answer["members"] if v not in present]
            matches, _ = self.names.find(target)
            if matches:
                row.update(
                    status="ambiguous", documented_target=target, candidates=matches
                )
            elif not present:
                row.update(status="missing", documented_target=target)
            else:
                group = aggregates.setdefault(
                    target,
                    {
                        "members": [],
                        "members_without_commands": [],
                        "evidence": [],
                        "exhaustive": False,
                    },
                )
                for name, values in (
                    ("members", present),
                    ("members_without_commands", absent),
                    ("evidence", [row["evidence"]]),
                ):
                    group[name] = list(dict.fromkeys([*group[name], *values]))
                row.update(status="grouped", target=target, method="documentation")
        elif kind == "exit_parent":
            row.update(status="exit_parent")
            row.pop("target", None)
        else:
            row.update(status=kind)
            if kind == "unknown" and row["requested_target"] is not None:
                row["status"] = "missing"
            if target:
                row["documented_target"] = target
            if answer["members"]:
                row["candidates"] = answer["members"]
            row.pop("target", None)

    def _graph(self, origins, transitions, aggregates):
        entries, exits = defaultdict(set), defaultdict(set)
        for view, indices in origins.items():
            if view in self.globals:
                continue
            for index in indices:
                row = transitions.get(index, {})
                if row.get("status") in {"resolved", "grouped"}:
                    entries[view].add(row["target"])
                elif row.get("status") == "exit_view":
                    exits[view].add(row["target"])
        entered = reachable(self.entry, entries)
        session = reachable(self.entry, {v: entries[v] | exits[v] for v in origins})
        members = {
            m for v in entered if v in aggregates for m in aggregates[v]["members"]
        }
        concrete = set(origins) - self.globals
        return {
            "entry_view": self.entry,
            "entered_views": sorted(entered),
            "members_covered_by_aggregates": sorted(members),
            "reachable_with_fixed_exits": sorted(session),
            "unreachable_views": sorted(concrete - entered - members),
            "views_without_entry_command": sorted(
                concrete
                - {self.entry}
                - {v for targets in entries.values() for v in targets}
                - {m for group in aggregates.values() for m in group["members"]}
            ),
            "entry_edges": {v: sorted(t) for v, t in entries.items() if t},
            "fixed_exit_edges": {v: sorted(t) for v, t in exits.items() if t},
        }

    @staticmethod
    def _conflicts(views, origins):
        result = []
        for view, commands in views.items():
            formats = defaultdict(list)
            for i, command in enumerate(commands):
                formats[command["format"]].append(i)
            for pattern, indices in formats.items():
                targets = {str(commands[i].get("switch_to_view")) for i in indices}
                if len(targets) > 1:
                    result.append(
                        {
                            "view": view,
                            "format": pattern,
                            "commands": [origins[view][i] for i in indices],
                        }
                    )
        return result
