"""Allow ``python -m annotation_fix``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
