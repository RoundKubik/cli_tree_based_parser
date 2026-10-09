# Repository utilities

Run commands from the repository root with Python 3.13+. Parsing and matching use
the package CLIs; scripts here prepare inputs or measure their behavior.

| Tool | Purpose |
|---|---|
| `python -m vrp_parser_automaton` | Validate catalogs and parse configuration files |
| `python -m vrp_format_matcher` | Match catalogs and export `mapping.json` |
| `group_formats_by_view.py` | Export documentation formats under their original `ParentView` names |
| `benchmark_format_matcher.py` | Match a corpus against itself and check parameter coverage |
| `refresh_mock_views.py` | Regenerate the small CloudEngine documentation fixtures from source pages |
| `corpus_pipeline/` | Extract annotations, validate them, prepare hierarchy experiments and mock device catalogs |

## Export formats by view

```bash
python3.13 scripts/group_formats_by_view.py \
  --corpus /path/to/cmd_corpus \
  --output /tmp/documentation_grouped.json
```

This structural export preserves original view names and deduplicates formats
within each view. It does not infer types, transitions, or semantics. The existing
CloudEngine export is in [data/examples](../data/examples/README.md).
Use `--help` for the catalog header and entry-view options.

## Measure matching

```bash
python3.13 scripts/benchmark_format_matcher.py \
  --corpus /path/to/cmd_corpus --skip-invalid --report /tmp/benchmark.json
```

The benchmark excludes `display` by default, preserves duplicate formats, and
reports invalid patterns. Without `--skip-invalid`, invalid input stops the run.
Use [corpus experiments](corpus_pipeline/README.md#reproduce-parser-and-matcher-experiments)
to compare separate catalogs with time and memory limits.

## Regenerate mock view annotations

```bash
PYTHONPATH=src python3.13 scripts/refresh_mock_views.py \
  --corpus /path/to/cmd_corpus \
  --catalog-dir data/mocks/cloudengine_150
```

This updates the mock documentation files in `--catalog-dir`. It uses their source
page references and conservative extraction from function descriptions and examples.
See the [fixture description](../data/mocks/cloudengine_150/README.md).

## Corpus pipeline and examples

The [pipeline guide](corpus_pipeline/README.md) covers LLM extraction, schemas,
prompts, review reports, optional hierarchy recovery, and NE40E mock generation.
Generated results belong in `data/generated/` and are excluded from Git.

The maintained API examples are in [examples](../examples): parser initialization
and semantic candidate lookup. `examples/legacy/metadata_mapping.py` demonstrates
the original `vrp_parser.metadata` API, which has a separate result format.
