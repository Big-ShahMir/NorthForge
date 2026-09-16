"""``python -m northforge.ingestion.seed``: ingest the dataset for local development.

Opens its own engine and object storage directly from ``Settings`` (this is
a standalone script, not a request handler, so there is no ``app.state`` to
borrow from), runs ``ingest_dataset``, commits, and prints the resulting
``IngestionReport`` as JSON. ``--grant-user`` appends an access group to an
already-seen user's ``access_groups_json`` in the same transaction, so a
local reviewer can, for example, grant themselves ``legal_restricted`` and
immediately see the difference in a search or documents-list call.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.core.config import get_settings
from northforge.db.engine import create_engine, create_session_factory
from northforge.db.models import User
from northforge.ingestion.pipeline import IngestionReport, ingest_dataset
from northforge.storage.s3 import S3ObjectStorage

DEFAULT_DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"
DEFAULT_DATASET_VERSION = "v1"


class SeedError(RuntimeError):
    """Raised when a ``--grant-user`` subject has no existing ``User`` row."""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m northforge.ingestion.seed",
        description="Ingest the synthetic dataset into a project for local development.",
    )
    parser.add_argument("--project-id", required=True, type=uuid.UUID, help="target project id")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_DIR,
        help=f"dataset directory (default: {DEFAULT_DATASET_DIR})",
    )
    parser.add_argument(
        "--dataset-version",
        default=DEFAULT_DATASET_VERSION,
        help=f"dataset version recorded on each document (default: {DEFAULT_DATASET_VERSION})",
    )
    parser.add_argument(
        "--grant-user",
        nargs=2,
        metavar=("SUBJECT", "GROUP"),
        action="append",
        default=[],
        dest="grant_user",
        help=(
            "grant an access group to an existing user, "
            "e.g. --grant-user dev|alice legal_restricted"
        ),
    )
    return parser.parse_args(argv)


async def _grant_group(session: AsyncSession, subject: str, group: str) -> None:
    stmt = select(User).where(User.clerk_user_id == subject)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise SeedError(
            f"no user found with subject {subject!r}; that caller must make at least one "
            "authenticated request before being granted a group."
        )
    groups = [str(existing) for existing in user.access_groups_json]
    if group not in groups:
        groups.append(group)
        user.access_groups_json = groups


async def _run(args: argparse.Namespace) -> IngestionReport:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    storage = S3ObjectStorage(settings)
    try:
        await storage.ensure_bucket()
        async with session_factory() as session:
            report = await ingest_dataset(
                session, storage, args.project_id, args.dataset, args.dataset_version
            )
            for subject, group in args.grant_user:
                await _grant_group(session, subject, group)
            await session.commit()
        return report
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except SeedError as exc:
        print(f"Seed error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(dataclasses.asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
