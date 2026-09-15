"""``python -m northforge.worker [--check]``."""

from __future__ import annotations

import asyncio
import sys

from northforge.core.config import get_settings
from northforge.core.errors import ConfigurationError
from northforge.core.logging import configure_logging
from northforge.worker.main import check_health, run


def main(argv: list[str]) -> int:
    try:
        if "--check" in argv:
            settings = get_settings()
            configure_logging(settings.log_level)
            return asyncio.run(check_health(settings))
        run()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
