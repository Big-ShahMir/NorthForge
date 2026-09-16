"""Deterministic synthetic contract, policy, and vendor dataset (ADR-023).

The generator produces the same bytes for the same ``(seed, version)`` pair so
the corpus can be committed, reviewed by a human, and regenerated in CI as a
drift check. Ingestion (Phase 3, section 4) reads the written files back through
:func:`northforge.data.synthetic.models.load_dataset`.

Everything in the corpus is fictional: the buyer, the vendors, the signatories,
and the clause text. No real company, person, address, or contract is described.
"""

from northforge.data.synthetic.generator import generate_dataset, write_dataset
from northforge.data.synthetic.models import (
    Dataset,
    DocumentRecord,
    GroundTruthRecord,
    Manifest,
    PolicyRuleRecord,
    RetrievalEvalCase,
    VendorRecord,
    load_dataset,
)

__all__ = [
    "Dataset",
    "DocumentRecord",
    "GroundTruthRecord",
    "Manifest",
    "PolicyRuleRecord",
    "RetrievalEvalCase",
    "VendorRecord",
    "generate_dataset",
    "load_dataset",
    "write_dataset",
]
