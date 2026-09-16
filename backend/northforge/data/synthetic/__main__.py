"""``python -m northforge.data.synthetic`` regenerates the committed corpus.

The defaults match the command recorded in the Phase 3 design, so a reviewer or
CI job can run it with no arguments and compare the result against the files in
version control::

    uv run python -m northforge.data.synthetic --out data/synthetic --seed 20260916 --version v1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from northforge.data.synthetic.generator import (
    DEFAULT_OUT_DIR,
    DEFAULT_SEED,
    DEFAULT_VERSION,
    DatasetError,
    generate_dataset,
    write_dataset,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m northforge.data.synthetic",
        description="Generate the deterministic synthetic contract and policy dataset.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"random seed; the same seed always produces the same bytes (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"dataset version recorded in the manifest (default: {DEFAULT_VERSION})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        dataset = generate_dataset(seed=args.seed, version=args.version)
        written = write_dataset(dataset, args.out)
    except DatasetError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 2
    print(
        f"Wrote {len(written)} files to {args.out} "
        f"({dataset.manifest.document_count} documents, "
        f"{len(dataset.retrieval_eval)} retrieval cases, "
        f"dataset_version={dataset.manifest.dataset_version}, seed={dataset.manifest.seed})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
