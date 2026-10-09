# Semantic candidates after parsing

The parser and matcher keep their existing JSON formats. Parsing identifies valid
syntax; matching records documentation correspondences. Neither component evaluates
`creates`, `requires`, predicates or the existence of configuration entities.

## Device formats and an offline mapping

Keep the original device catalog, original documentation catalog and `mapping.json`
together. Generate the mapping once:

```bash
python3.13 manual_format_matcher_test.py \
  --patterns device_grouped.json \
  --documents documentation_grouped.json \
  --save mapping.json --summary
```

Parse configurations using that same device catalog. For each successful line:

1. Iterate over `line.matches`, including the primary and every alternative parse.
2. Find `mapping["devices"][match.pattern_id]`.
3. Iterate over its `mappings`. Each pair references a `document_id`.
4. Find `mapping["documents"][document_id]["source"]`. Its `view` and `index`
   address the original documentation record, containing `creates` and `requires`.
   A null source view addresses `documentation["commands"][index]` instead.
5. Check the pair's applicability and bind captured values before interpreting an
   effect. Keep candidates from different parses and documentation records separate.

The grouped default isolates system from non-system in both components. It does not
prove a particular nested view. A unique parse may still have multiple documentation
matches. `primary_match` is the first successful source pattern, not the most credible
semantic interpretation. No shared/global scope exceptions are applied.

## Captures and applicability

For `binding_mode="structural"`, use each binding's device `slot_id` to find captured
values. The documentation slot supplies its parameter name. Preserve every repeated
value and its `iterations`; map device and documentation `repeat_ids` pairwise.
An omitted optional parameter has no captured value. Do not create a value for it.
Commands without parameters can still carry command-level semantics.

For `path_dependent`, bindings are possibilities across different paths. Replay the
actual parsed trace against `mapping["automata"][pair["automaton_id"]]` before using
them. A complete accepting path is required. A `prefix_dependent` pair describes an
unfinished shared trace and does not authorize whole-command effects. An unavailable
mapping establishes neither parameter values nor applicability.
See [conditional bindings](automaton-format-matcher.md#partial-and-path-dependent-correspondences).

Keep both levels of ambiguity: different parser matches and different documentation
pairs. Do not union their `creates`/`requires` into one action list or select a candidate
solely because it is first. An external semantic resolver can apply an effect when
its conditions and interpretation are established; conflicting effects stay candidates.
For `parameter_comb`, resolve the named captures within one interpretation and the
appropriate repetition occurrence, not by taking the first value of each name.

## Runnable lookup example

[`examples/semantic_candidates.py`](../../examples/semantic_candidates.py) retrieves
the source semantics for every candidate and binds structural correspondences:

```bash
PYTHONPATH=src python3.13 examples/semantic_candidates.py \
  --patterns device_grouped.json \
  --mapping mapping.json \
  --documentation documentation_grouped.json \
  --config config.txt > semantic_candidates.json
```

The example deliberately returns candidates, not resolved effects. Conditional or
unavailable mappings carry `needs_trace: true` without guessed parameter assignments.
The saved graph ID is included when available. Graph replay, predicate evaluation
and entity resolution remain separate work in the consuming application.

The example's output is not a new parser, matcher or catalog schema. It is a small
adapter that can be copied into the consuming project. It also reports parse errors.

## Parsing documentation directly

No mapping is required when the parser consumes the documentation itself:

```bash
PYTHONPATH=src python3.13 examples/semantic_candidates.py \
  --patterns documentation_grouped.json \
  --config config.txt > semantic_candidates.json
```

`match.pattern_index` addresses the original flattened sequence of records, following
the JSON view and command order. It identifies the source record directly. Named
captures retain their original `<name>` declarations even when a type annotation
selects a validator. All alternative parses still need consideration.

Preserve catalog contents and ordering between offline matching and runtime. Do not
flatten, sort, deduplicate or reformat the saved inputs: source locations, duplicate
ordinals and character-based slots address those originals. Regenerate the mapping
when catalogs change. The example checks format strings at lookup time, but this is
not a full catalog-version check.
