# Structural documentation export

[cloudengine_grouped.json](cloudengine_grouped.json) contains the CloudEngine
v300r024c00 formats grouped by their original documentation `ParentView` names:
282 views and 36,750 view/format records. It was moved here from the repository root
without changing its contents or order.

Records contain `format` only. This is a structural export, not a corpus with
validated semantics or recovered hierarchy. It may include console-only commands
such as `display`; the semantic extraction pipeline has its own selection rules.

Regenerate an export with `scripts/group_formats_by_view.py`; see
[script usage](../../scripts/README.md). Smaller annotated mock fixtures are in
[data/mocks/cloudengine_150](../mocks/cloudengine_150/README.md).
