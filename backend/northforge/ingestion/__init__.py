"""Ingestion of the synthetic dataset into PostgreSQL and object storage.

``northforge.ingestion.pipeline.ingest_dataset`` is the single entry point
used by the local seed CLI (``python -m northforge.ingestion.seed``) and the
arq worker job (``ingest_synthetic_dataset``, registered in
``northforge.worker.main``). Both callers own their own session's commit;
the pipeline itself only flushes, so it can be exercised repeatedly inside
one uncommitted test transaction.
"""

from __future__ import annotations

from northforge.ingestion.pipeline import IngestionReport, ingest_dataset

__all__ = ["IngestionReport", "ingest_dataset"]
