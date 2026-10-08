# Command catalog formats: draft specification v1

The matcher accepts these containers through `compile_catalogs()`, checks parameter
type compatibility, and preserves source record locations. For two grouped catalogs,
commands from the device's entry view are searched in the documentation's entry
view and explicitly shared scopes. Other views and flat catalogs retain a global
search for all full matches,
without scoring or selecting view correspondences. For two grouped catalogs, the
result also contains `hierarchy`: command and view relations, possible transition
targets, and explicit transitions from the target catalog. A single candidate does
not count as an established correspondence. This specification describes matcher
inputs. The runtime parser accepts one prepared document with standard parameter
declarations; for `type: grouped`, it uses known transitions, and unresolved
transitions must be marked explicitly.
It does not read documentation, type annotations, or mapping JSON. See
[parser input](automaton-parser.md#input-document) for details.

The offline `prepare_catalogs()` method accepts documentation with a recovered
hierarchy: in this mode, an omitted documentation `switch_to_view` means that the
context is preserved. It returns a separate runtime catalog and mapping.
A grouped catalog keeps its original views and all established transitions.
Unresolved transitions receive an explicit marker on the corresponding command.
Recovery rules and a usage example are described in the
[matcher guide](automaton-format-matcher.md#prepare-a-parser-catalog-and-final-mapping).

## Common fields

| Field | Value |
|---|---|
| `schema_version` | Required: `1` |
| `source` | Required: `device` or `documentation` |
| `type` | Required: `flat` or `grouped` |
| `commands` | For `flat`: a nonempty array of command objects |
| `views` | For `grouped`: an object mapping view names/IDs to arrays of command objects |
| `entry_view` | For `grouped`: the initial view, an exact reference to a `views` key |
| `shared_views` | Optional for `grouped`: distinct existing view keys declaring shared commands; cannot include `entry_view` |
| `vendor` | Required: manufacturer, such as `Huawei` |
| `device` | Required: platform/OS, such as `Huawei VRP` |
| `model_type` | Required: model or family, such as `CloudEngine` |
| `software_version` | Optional: software release, such as `v300r024c00` |
| `metadata` | Optional: an object containing additional information |

`source` identifies the origin and parameter profile; `type` defines the catalog
layout. `commands` and `views` are mutually exclusive. A `flat` catalog has no
`entry_view` or `shared_views`. A grouped catalog must contain at least one command; an individual
view may contain an empty array. Unknown transitions in the original device
catalog may be represented by omitting `switch_to_view`. Prepared documents require
an explicit marker instead; a separate global `hierarchy_status` is not needed.

Shared scopes and copies of their typed formats do not identify concrete device
views. They remain available to scoped matching. No scope name is special without
this declaration. The field does not change parser inheritance or add command
copies. An absent field means no scopes have been declared shared.

`vendor`, `device`, and `model_type` are nonempty strings required in all four v1
variants. They describe the entire catalog and are preserved during processing.

## Commands and view transitions

| Field | Value |
|---|---|
| `format` | Required: a nonempty original format string without a line break |
| `parameter_types` | Required for `documentation`: an array of named parameter types; absent for `device` |
| `switch_to_view` | Only for `grouped`: a string, `null`, or `{"status": "unresolved"}`; may be omitted |
| `creates`, `requires` | Optional: arrays of semantic objects from an external pipeline |
| `metadata` | Optional: an object containing additional information |

**Input records have no `id` field.** Internal identifiers are generated
programmatically, preserving the link to the source record and its view. Identical
formats in different views are not treated as semantically identical. Existing
`pattern_id` and `slot_id` values remain internal links between matcher and parser
results.

`switch_to_view` uses the following representations:

| Representation | Meaning |
|---|---|
| `"switch_to_view": "BGP view"` | The command enters the specified view |
| `"switch_to_view": null` | The command is known to preserve the current context |
| `"switch_to_view": {"status": "unresolved"}` | No single transition has been established; this does not mean that the context is preserved |
| Field omitted | Unknown in the matcher's original device input; context preservation in prepared documentation and runtime catalogs |

`compile_catalogs()` collects evidence without assuming completeness: an omitted
field remains unknown even in documentation. `prepare_catalogs()` accepts prepared
documentation, so its omissions mean context preservation. An explicit `unresolved`
remains unknown in both modes.

A string must exactly match a `views` key, including case. For example, `BGP view`
and `BGP View` are different references. A value such as `"BGP view or bgp"` is
invalid unless that exact key exists. Not every view must be a transition target.

Extraction preserves the target name from the original description: `ACL view`
must not be narrowed to `Advanced ACL view` based on a parameter's name or type.
If case and whitespace are normalized, the same normalization applies to both keys
and references. A target with no known commands is represented by an empty group;
this does not establish hierarchy completeness.

A string describes one target view for the entire format. If the target depends on
a parameter value and no single transition exists, use `{"status": "unresolved"}`.
Do not merge possible targets under one name just to obtain a string.
Conditional transitions are not executed yet; discovered documentation alternatives
and their bindings are preserved in the mapping. This field does not describe
explicit exit commands. Returning from a configuration block follows separate block
handling rules; `entry_view` alone does not define indentation rules.

These rules apply to offline catalogs with transitions that are still unknown.
The prepared runtime parser input has a simpler contract: omitting `switch_to_view`
means preserving the current view. For an explicit `unresolved`, only the child
block is parsed without view restrictions. The rest of the hierarchy is preserved;
the parser does not infer unknown targets. A manual correction replaces the marker
with an original device view ID or `null`, without changing the format, record
order, or parameter identifiers.

Device formats use typed parameters: `INTEGER<2000-2999>`. Documentation preserves
names: `<acl-number>`. The matcher's current documentation profile treats
`INTEGER<2000-2999>` as a literal, so it must not replace a parameter name.

Documentation parameter types are specified separately in `parameter_types`.
Each entry contains `parameter_name`, the name without angle brackets, and
`parameter_type`: `string`, `integer`, `ipv4-address`, `ipv6-address`, `ipv6-prefix`,
`text`, `hex`, `mac`, `passwordex`, `date-slash`, `date-iso`, `month-day`, `date-us`,
`datetime-slash`, `time-seconds`, `time`, or `unknown`.
Every unique parameter name in `format` requires exactly one entry; extra names
and duplicates are forbidden. Commands without parameters use an empty array: `[]`.
If a name appears multiple times in the format, its type applies to every occurrence,
but the occurrences retain distinct `slot_id` values. Use `unknown` when the source
does not establish a supported category; do not silently replace it with `string`.
Such a parameter can receive bindings, but cannot prove view coverage or a transition.
This field does not yet specify string lengths or
numeric ranges. The original `format` with named placeholders is preserved unchanged.

The matcher maps built-in device declarations to these categories, comparing both
`TEXT` and documentation `text` as `string`. See the
[declaration table](automaton-format-matcher.md#parameter-type-compatibility).
Different known types reject a parameter correspondence. Unmapped device types and
explicit documentation `unknown` do not prevent a match, but supply no type proof.
This check applies at every stage, including intersections and incomplete traces.
The runtime parser also uses `parameter_types` to read and validate named parameters
with the corresponding built-in validators, without numeric ranges or length bounds.
`text` reads the remainder; `string` reads one token. Missing annotations and `unknown`
retain the unvalidated named behavior. Explicit device declarations retain their
original validators and bounds. See [runtime annotations](automaton-parser.md#input-document).

`creates` and `requires` are preserved without evaluation. An omitted field means
"no data"; an empty array means "no rules". Their internal structure is defined by
an external pipeline; `parameter_name` refers to a name in the original format.

## Examples

These examples illustrate the data structure, not a complete model of a specific device.

### Device: flat catalog

```json
{
  "schema_version": 1,
  "source": "device",
  "vendor": "Huawei",
  "device": "Huawei VRP",
  "model_type": "Test VRP device",
  "type": "flat",
  "commands": [
    {"format": "bgp INTEGER<1-40000>"}
  ]
}
```

### Device: grouped catalog

```json
{
  "schema_version": 1,
  "source": "device",
  "vendor": "Huawei",
  "device": "Huawei VRP",
  "model_type": "Test VRP device",
  "type": "grouped",
  "entry_view": "system",
  "views": {
    "system": [
      {"format": "bgp INTEGER<1-40000>"},
      {"format": "acl [ number ] INTEGER<2000-2999>"}
    ],
    "bgp": [
      {"format": "ipv6-family unicast"}
    ],
    "acl4-basic": []
  }
}
```

### Documentation: flat catalog

```json
{
  "schema_version": 1,
  "source": "documentation",
  "vendor": "Huawei",
  "device": "Huawei VRP",
  "model_type": "CloudEngine",
  "software_version": "v300r024c00",
  "type": "flat",
  "commands": [
    {
      "format": "acl [ number ] <acl-number>",
      "parameter_types": [
        {"parameter_name": "acl-number", "parameter_type": "integer"}
      ],
      "creates": [
        {"kind": "entity_type", "parameter_name": "acl-number", "entity_type": "acl"}
      ],
      "requires": []
    }
  ]
}
```

### Documentation: grouped catalog with a transition

```json
{
  "schema_version": 1,
  "source": "documentation",
  "vendor": "Huawei",
  "device": "Huawei VRP",
  "model_type": "CloudEngine",
  "software_version": "v300r024c00",
  "type": "grouped",
  "entry_view": "System view",
  "views": {
    "System view": [
      {
        "format": "acl [ number ] <acl-number>",
        "parameter_types": [
          {"parameter_name": "acl-number", "parameter_type": "integer"}
        ],
        "switch_to_view": "ACL view",
        "creates": [
          {"kind": "entity_type", "parameter_name": "acl-number", "entity_type": "acl"}
        ],
        "requires": []
      }
    ],
    "ACL view": [
      {
        "format": "description <text>",
        "parameter_types": [
          {"parameter_name": "text", "parameter_type": "string"}
        ],
        "switch_to_view": null
      }
    ]
  }
}
```

## Validation and compatibility

- Strict JSON: no trailing commas, ellipses outside strings, or duplicate keys.
- The structure matches `type`; v1 records are objects, not a mix of objects and strings.
- `vendor`, `device`, and `model_type` are present and contain nonempty strings.
- `format` is checked against the grammar for its `source`. Strings are preserved
  without reformatting: parameter positions contribute to `slot_id` calculation.
- For documentation, `parameter_types` covers all parameter names without duplicates;
  types belong to the supported set above, including explicit `unknown`.
- `entry_view` and all string `switch_to_view` values reference existing groups.
  Misspelled structural fields, such as `switch_to_veiw`, must be detected.
- Valid references do not prove that the extracted semantics are correct.

The legacy input `{"commands": ["bgp INTEGER<1-40000>"]}` remains supported.
The matcher provides the `compile_catalogs(device_catalog, documentation_catalog)`
adapter. Results containing bindings, source locations, and partial-match graphs
remain a separate format; this specification does not replace it.
