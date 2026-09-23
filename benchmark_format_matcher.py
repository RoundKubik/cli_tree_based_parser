"""Match a real CLIs corpus against itself and verify every source parameter."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_format_matcher import (  # noqa: E402
    FormatError,
    FormatMatcher,
    PreparationProgress,
)
from vrp_format_matcher.documents.catalog import (  # noqa: E402
    DocumentFormat,
    DocumentSource,
)
from vrp_parser_automaton.patterns import (  # noqa: E402
    Group,
    Node,
    Parameter,
    PatternLanguageError,
    Repeat,
    Sequence,
)


@dataclass(frozen=True)
class CorpusIssue:
    document_id: str
    format: str
    reason: str


@dataclass(frozen=True)
class CorpusContents:
    documents: tuple[DocumentFormat, ...]
    issues: tuple[CorpusIssue, ...]
    excluded: int


@dataclass(frozen=True)
class CorpusDirectory:
    path: Path
    include_display: bool = False

    def read(self) -> CorpusContents:
        files = sorted(self.path.glob("*.json"))
        if not files:
            raise FormatError(f"no corpus JSON files found in {self.path}")
        documents = []
        issues = []
        excluded = 0
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            formats = data.get("CLIs")
            if not isinstance(formats, list) or any(
                not isinstance(value, str) for value in formats
            ):
                raise FormatError(f"{file.name}: CLIs must be an array of strings")
            for index, pattern in enumerate(formats):
                if not self.include_display and pattern.split()[:1] == ["display"]:
                    excluded += 1
                    continue
                document_id = f"{file.stem}:{index}"
                try:
                    document = DocumentSource(
                        {"id": document_id, "format": pattern}, document_id
                    ).parsed()
                except (FormatError, PatternLanguageError) as error:
                    issues.append(CorpusIssue(document_id, pattern, str(error)))
                else:
                    documents.append(document)
        return CorpusContents(tuple(documents), tuple(issues), excluded)


def parameters(node: Node | Sequence) -> Iterator[Parameter]:
    if isinstance(node, Parameter):
        yield node
    elif isinstance(node, Sequence):
        for child in node.items:
            yield from parameters(child)
    elif isinstance(node, Group):
        for branch in node.alternatives:
            yield from parameters(branch)
    elif isinstance(node, Repeat):
        yield from parameters(node.atom)


def main() -> None:
    args_parser = argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument("--corpus", type=Path, required=True)
    args_parser.add_argument("--include-display", action="store_true")
    args_parser.add_argument("--skip-invalid", action="store_true")
    args_parser.add_argument(
        "--save", type=Path, help="Save plain matching result JSON"
    )
    args_parser.add_argument("--report", type=Path, help="Small benchmark report JSON")
    args = args_parser.parse_args()

    started = perf_counter()
    corpus = CorpusDirectory(args.corpus, args.include_display).read()
    loaded = perf_counter() - started
    for issue in corpus.issues:
        print(f"INVALID {issue.document_id}: {issue.reason}", file=sys.stderr)
    if corpus.issues and not args.skip_invalid:
        args_parser.error(
            "invalid corpus formats; use --skip-invalid to report and skip"
        )
    documents = [
        {"id": document.document_id, "format": document.format}
        for document in corpus.documents
    ]
    targets = []
    expected = {}
    for document in corpus.documents:
        pattern = document.format
        slots = list(parameters(document.ast))
        targets.append(pattern)
        expected[document.document_id] = (pattern, len(slots))

    def progress(event: PreparationProgress) -> None:
        if event.devices_done % 1000 == 0 or event.devices_done == event.devices_total:
            print(
                f"{event.stage}: devices {event.devices_done}/{event.devices_total}; "
                f"pairs {event.pairs_prepared}",
                file=sys.stderr,
                flush=True,
            )

    started = perf_counter()
    prepared = FormatMatcher().compile_formats(
        targets,
        documents,
        target_syntax="document",
        on_progress=progress,
    )
    matching_seconds = perf_counter() - started
    verified = set()
    links = 0
    for pair in prepared.pairs:
        target, count = expected[pair.document_id]
        links += len(pair.bindings)
        if pair.device_format == target:
            if pair.status != "equivalent" or len(pair.bindings) != count:
                raise AssertionError(
                    f"incomplete self correspondence: {pair.document_id}"
                )
            if len({link.document.slot_id for link in pair.bindings}) != count:
                raise AssertionError(f"missing document slot: {pair.document_id}")
            if len({link.device.slot_id for link in pair.bindings}) != count:
                raise AssertionError(f"missing device slot: {pair.document_id}")
            if any(link.document != link.device for link in pair.bindings):
                raise AssertionError(f"non-identity self mapping: {pair.document_id}")
            verified.add(pair.document_id)
    if len(verified) != len(documents):
        raise AssertionError(f"self matches missing: {len(documents) - len(verified)}")
    report = {
        "documents": len(documents),
        "targets": len(targets),
        "unique_formats": len(set(targets)),
        "excluded_display": corpus.excluded,
        "invalid": [asdict(issue) for issue in corpus.issues],
        "target_syntax": "document",
        "loading_seconds": loaded,
        "matching_seconds": matching_seconds,
        "pairs": len(prepared.pairs),
        "parameter_links": links,
        "verified_documents": len(verified),
        "device_statuses": dict(Counter(d.status for d in prepared.devices.values())),
        "stages": dict(Counter(d.stage for d in prepared.devices.values())),
        "pair_statuses": dict(Counter(p.status for p in prepared.pairs)),
    }
    if args.save:
        started = perf_counter()
        args.save.write_text(
            json.dumps(prepared.to_dict(), ensure_ascii=False), encoding="utf-8"
        )
        report["saving_seconds"] = perf_counter() - started
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
