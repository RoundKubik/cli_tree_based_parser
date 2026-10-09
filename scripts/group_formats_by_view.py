"""Export documentation CLIs as a grouped catalog with original view names."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--corpus", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--vendor", default="Huawei")
    arguments.add_argument("--device", default="Huawei VRP")
    arguments.add_argument("--model-type", default="CloudEngine 9800/8800/6800")
    arguments.add_argument("--software-version", default="v300r024c00")
    arguments.add_argument("--entry-view", default="System view")
    args = arguments.parse_args()

    files = sorted(args.corpus.expanduser().glob("*.json"))
    if not files:
        arguments.error(f"no corpus JSON files found in {args.corpus}")

    views: dict[str, set[str]] = defaultdict(set)
    for path in files:
        page = json.loads(path.read_text(encoding="utf-8"))
        for view in page["ParentView"]:
            views[view].update(page["CLIs"])

    if args.entry_view not in views:
        arguments.error(f"entry view is absent from the corpus: {args.entry_view!r}")

    result = {
        "schema_version": 1,
        "source": "documentation",
        "vendor": args.vendor,
        "device": args.device,
        "model_type": args.model_type,
        "software_version": args.software_version,
        "type": "grouped",
        "entry_view": args.entry_view,
        "views": {
            view: [{"format": pattern} for pattern in sorted(formats)]
            for view, formats in sorted(views.items())
        },
    }
    args.output.expanduser().write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(views)} views from {len(files)} pages to {args.output}")


if __name__ == "__main__":
    main()
