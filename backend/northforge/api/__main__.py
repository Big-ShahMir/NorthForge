"""``python -m northforge.api`` starts the development API server."""

from __future__ import annotations

import sys

from northforge.core.errors import ConfigurationError


def main() -> int:
    try:
        from northforge.api.main import run

        run()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
