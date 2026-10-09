# Huawei VRP parser and format matcher

Parse configuration files using device or documentation command formats, then
look up documentation correspondences in an offline mapping.

- `vrp_parser_automaton` compiles patterns into an automaton and parses configuration
  lines. Results retain original formats, parameter values, `pattern_id`, `slot_id`,
  repetition coordinates, and alternative matches.
- `vrp_format_matcher` matches device → documentation or documentation → documentation
  formats and saves their parameter correspondences as JSON.
- Semantic evaluation belongs to the consuming application. The parser reads one
  catalog; it does not load the mapping or evaluate `creates` / `requires`.

Python 3.13+ is required. The parser and matcher have no runtime dependencies.

## Installation

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e .
```

This installs `vrp-parser-automaton` and `vrp-format-matcher`. The original
`vrp-parser` entry point remains available for compatibility.
The commands below run from the repository root without installation.

## Input catalogs

Both tools support [flat and grouped catalogs](docs/reference/command-catalog-format.md).
The matcher expects the v1 header: `schema_version`, `source`, `vendor`, `device`,
`model_type`, and `type`. Command records contain `format`; documentation may also
contain `parameter_types`, `creates`, and `requires`.

- **Flat:** commands are in `commands`; all formats are searched together.
- **Grouped:** commands are in `views`, and `entry_view` identifies the system group.
  The matcher compares system only with system and all remaining groups together.
  The parser searches system for unindented lines and the other groups for indented
  lines. It retains alternative matches without claiming an exact nested view.

The grouped default ignores `switch_to_view` and `shared_views`. The optional
`--context-mode hierarchy` retains the earlier hierarchy workflow; it is not
required for the default parser or matcher.

[Small CloudEngine fixtures](data/mocks/cloudengine_150/README.md) provide all four
catalog variants. Their semantic annotations are mock data, not an extraction benchmark.

## Offline matching

```bash
PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --patterns device_grouped.json \
  --documents documentation_grouped.json \
  --save mapping.json --summary
```

The same command accepts flat catalogs. For documentation → documentation, pass
another documentation catalog as `--patterns`.
The matcher prints progress and counts and writes indented JSON. It retains source
formats, source view locations, statuses, bindings, and path applicability graphs.
Neither input catalog is rewritten.

For a runnable example using the checked-in fixtures:

```bash
PYTHONPATH=src python3.13 -m vrp_format_matcher \
  --patterns data/mocks/cloudengine_150/device_flat.json \
  --documents data/mocks/cloudengine_150/documentation_flat.json \
  --save /tmp/mapping.json --summary
```

See the [matcher guide](docs/reference/automaton-format-matcher.md) for the Python
API, matching stages, output fields, and conditional bindings.

## Configuration parsing

Validate the catalog, then parse a configuration:

```bash
PYTHONPATH=src python3.13 -m vrp_parser_automaton check-patterns device_grouped.json

PYTHONPATH=src python3.13 -m vrp_parser_automaton parse \
  --patterns device_grouped.json --config config.txt > parsed.json
```

Use the same device catalog used during matching, or a documentation catalog
directly. Add `--flat` to search all groups regardless of indentation.
The parser exits with `0` on success, `1` when some lines failed, and `2` for
catalog, compilation, or file errors.

```python
from vrp_parser_automaton import CommandLineParser, ConfigurationParser

parser = CommandLineParser.from_json_file("device_grouped.json")
report = ConfigurationParser(parser).parse("acl 2018\n")
print(report.to_dict())
```

The [parser guide](docs/reference/automaton-parser.md) describes validation,
ambiguities, result fields, and limitations. A small
[initialization example](examples/parse_configuration.py) saves a report to a file.

## Semantic lookup

For device parsing, use `match.pattern_id` to access `mapping["devices"]`, follow
each `document_id` into `mapping["documents"]`, and locate the original semantics
in the documentation. A document record's source view and original format can
identify it; `source.index` also preserves its position. Bind captured values using
the saved slot correspondences. Keep ambiguous interpretations separate.

For documentation parsing, semantics are already in the source catalog.
See [semantic lookup](docs/reference/semantic-lookup.md) and the runnable
[candidate lookup example](examples/semantic_candidates.py). The example binds
structural mappings; conditional paths and semantic evaluation remain external.

## Repository layout

| Path | Purpose |
|---|---|
| `src/vrp_parser_automaton/` | Current parser and CLI |
| `src/vrp_format_matcher/` | Offline matcher and CLI |
| `src/vrp_parser/` | Original parser and metadata API, retained for compatibility |
| `examples/` | Parser initialization and semantic lookup; original metadata example in `legacy/` |
| `scripts/` | Corpus export, fixture regeneration, and performance measurements |
| `scripts/corpus_pipeline/` | LLM extraction, validation, optional hierarchy recovery, and mock device generation |
| `data/mocks/` | Small test catalogs |
| `data/examples/` | Structural corpus exports without semantic annotations |
| `data/generated/` | Local pipeline outputs, excluded from Git |
| `tests/` | Parser, matcher, CLI, and corpus pipeline checks |
| `docs/reference/` | Input/output contracts and algorithms |

See [script usage](scripts/README.md) and
[corpus extraction](scripts/corpus_pipeline/README.md).
Documentation for the original parser starts in the
[legacy project guide](docs/PROJECT_GUIDE.md).

## Development checks

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/ruff check src tests examples scripts
.venv/bin/ruff format --check src/vrp_parser_automaton src/vrp_format_matcher examples scripts
```

The former root-level manual runners have been replaced by package CLIs.
Use `python -m vrp_format_matcher` for matching and
`python -m vrp_parser_automaton` for parsing, with `PYTHONPATH=src` when running
without installation. Experimental pattern cases are covered by the automated tests.
