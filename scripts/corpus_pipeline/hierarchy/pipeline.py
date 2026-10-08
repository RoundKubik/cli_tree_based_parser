"""One offline pass from annotated formats to a catalog and hierarchy audit."""

from pathlib import Path

from ..source import Corpus, write_json
from .evidence import EvidenceRequests, collect_evidence
from .input import AnnotatedCorpus, ViewNames
from .recovery import DocumentationHierarchy


def recover_hierarchy(
    input_directory,
    corpus_directory,
    output_directory,
    agent,
    workers=4,
    entry_view="System view",
    global_views=None,
):
    input_directory, output_directory = Path(input_directory), Path(output_directory)
    if input_directory.resolve() == output_directory.resolve():
        raise ValueError(
            "Use a separate output directory to preserve extraction artifacts"
        )
    corpus = Corpus(Path(corpus_directory))
    annotated = AnnotatedCorpus(input_directory, corpus)
    names = ViewNames(annotated.views)
    shared = set()
    for name in global_views if global_views is not None else ["All views"]:
        matches, _ = names.find(name)
        if len(matches) > 1:
            raise ValueError(f"Ambiguous shared scope: {name}")
        shared.update(matches)
    recovery = DocumentationHierarchy(annotated, entry_view, shared)
    requests = EvidenceRequests(annotated, shared).build()
    answers, errors = collect_evidence(
        requests, agent, output_directory / "evidence", workers
    )
    document, report = recovery.recover(requests, answers, errors)
    report["evidence_model"] = agent.model
    write_json(output_directory / "documentation_grouped.json", document)
    write_json(output_directory / "hierarchy_report.json", report)
    return report
