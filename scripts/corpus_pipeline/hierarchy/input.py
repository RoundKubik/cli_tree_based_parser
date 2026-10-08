"""Original annotations, source locations and conservative view-name lookup."""

from collections import defaultdict
from pathlib import Path

from ..extraction import view_key
from ..source import Corpus, read_json


class ViewNames:
    def __init__(self, names):
        self.names = tuple(names)
        self.normalized = defaultdict(list)
        for name in self.names:
            self.normalized[view_key(name)].append(name)

    def find(self, name):
        if name in self.names:
            return [name], "exact"
        matches = self.normalized.get(view_key(name), [])
        return list(matches), "normalized"


class AnnotatedCorpus:
    def __init__(self, directory: Path, corpus: Corpus):
        self.document = read_json(directory / "documentation_flat.json")
        if self.document.get("type") != "flat":
            raise ValueError("Hierarchy recovery requires a flat extraction catalog")
        self.commands = self.document["commands"]
        if not isinstance(self.commands, list) or not self.commands:
            raise ValueError("The annotated catalog must contain commands")
        if any(
            c.get("switch_to_view") is not None
            and not isinstance(c["switch_to_view"], str)
            for c in self.commands
        ):
            raise ValueError("Extraction transitions must be strings or null")
        self.corpus = corpus
        self.locations = {}
        self.issues = {}
        report = read_json(directory / "report.json")
        pending = {
            (r["file"], r["format_index"]): r["issues"] for r in report["issues"]
        }
        for source in report["sources"]:
            page = corpus.pages[source["file"]]
            for offset, index in enumerate(source["format_indices"]):
                position = source["command_start"] + offset
                if position in self.locations or position >= len(self.commands):
                    raise ValueError("Duplicate or out-of-range source position")
                command = self.commands[position]
                if command["format"] != page.formats[index] or (
                    command["parent_views"] != page.views
                ):
                    raise ValueError(f"Source and annotation disagree at {position}")
                self.locations[position] = {
                    "file": page.path.name,
                    "format_index": index,
                }
                self.issues[position] = pending.get((page.path.name, index), [])
        if set(self.locations) != set(range(len(self.commands))):
            raise ValueError("Source locations must cover every annotated format")
        self.views = tuple(
            dict.fromkeys(
                view for command in self.commands for view in command["parent_views"]
            )
        )

    def transition_issue(self, position):
        return [i for i in self.issues[position] if i["field"] == "switch_to_view"]
