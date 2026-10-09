"""Allow ``python -m vrp_format_matcher`` to run offline matching."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
