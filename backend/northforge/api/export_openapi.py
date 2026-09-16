"""Export the FastAPI OpenAPI schema for frontend type generation.

Run as ``python -m northforge.api.export_openapi [output_path]``. The app is
built with a minimal, in-memory ``Settings`` (dev auth mode, dummy database
and Redis DSNs) -- ``app.openapi()`` only introspects registered routes and
Pydantic models, so no network access or running services are required, and
the app's lifespan is never entered.

The output is written as deterministic JSON (sorted keys, 2-space indent, a
trailing newline) so the file is stable across runs and diffs cleanly in CI's
``contracts`` job (see ``.github/workflows/ci.yml``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from northforge.api.main import create_app
from northforge.core.config import Settings

#: ``backend/northforge/api/export_openapi.py`` -> repo root -> ``frontend/openapi.json``.
_DEFAULT_OUTPUT = Path(__file__).resolve().parents[3] / "frontend" / "openapi.json"


def _export_settings() -> Settings:
    """A minimal settings object good enough to build the app, nothing else.

    ``_env_file=None`` skips loading ``.env`` (mirrors ``load_settings``'s
    test usage) so this never depends on -- or is perturbed by -- the local
    environment; the DSNs are never connected to.
    """
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="development",
        auth_mode="dev",
        database_url="postgresql://export:export@localhost/export_only",
        redis_url="redis://localhost/0",
    )


def export_openapi(output_path: Path | None = None) -> Path:
    """Write the app's OpenAPI schema to ``output_path`` and return it."""
    target = output_path or _DEFAULT_OUTPUT
    app = create_app(_export_settings())
    schema = app.openapi()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    output_path = Path(args[0]) if args else None
    target = export_openapi(output_path)
    print(f"wrote OpenAPI schema to {target}")


if __name__ == "__main__":
    main()
