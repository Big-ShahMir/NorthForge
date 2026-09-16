"""File records for the synthetic dataset.

These models are the contract between the generator and every consumer:
ingestion, the retrieval quality gate, and the tests. They are deliberately
plain -- strings and primitives, dates as ISO ``YYYY-MM-DD`` text -- so the JSON
on disk is readable and stable under ``sort_keys=True``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import Severity

AccessGroup = Literal["procurement", "legal_restricted", "hr_restricted"]
"""Access groups used by the corpus; ``procurement`` is every user's default."""

EdgeCase = Literal[
    "missing_clause",
    "conflicting_policy",
    "ambiguous_terms",
    "long_document",
    "malformed_document",
    "unauthorized",
    "near_duplicate",
    "injection",
]

RetrievalOutcome = Literal["ok", "insufficient_evidence", "conflicting_evidence"]

EvalCategory = Literal[
    "normal",
    "miss",
    "duplicate",
    "conflicting_policy",
    "unauthorized",
    "ambiguous",
    "long_document",
    "malformed",
    "injection",
]

ViolationOutcome = Literal["violation", "requires_review", "conflicting_policy"]

MANIFEST_FILE = "manifest.json"
VENDORS_FILE = "vendors.json"
POLICY_RULES_FILE = "policy_rules.json"
GROUND_TRUTH_FILE = "ground_truth.json"
RETRIEVAL_EVAL_FILE = "retrieval_eval.json"
DOCUMENTS_DIR = "documents"


class DocumentRecord(BaseModel):
    """One corpus document: metadata plus its full markdown body.

    ``content`` is a markdown document whose first line is ``# <name>`` and
    whose clauses are ``## <Heading>`` sections; the Phase 3 chunker splits on
    those headings, so headings are also how ``policy_rules.section`` and
    ``ground_truth.field_sections`` address text.
    """

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    document_type: str = Field(min_length=1, max_length=64)
    vendor: str | None = Field(default=None, max_length=200)
    effective_date: str | None = None
    expires_at: str | None = None
    access_group: AccessGroup = "procurement"
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str = Field(min_length=1)
    edge_cases: list[EdgeCase] = Field(default_factory=list)


class VendorRecord(BaseModel):
    """A fictional vendor; ``vendor_id`` is stable across dataset versions."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=64)
    country: str = Field(min_length=1, max_length=64)
    risk_tier: Severity
    preferred: bool


class PolicyRuleRecord(BaseModel):
    """A machine-checkable policy rule, addressed to a policy document section.

    ``chunk_id`` is intentionally absent: ingestion resolves the section heading
    to a chunk id, because chunk ids depend on the chunker, not on the dataset.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    policy_document_id: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=300)
    policy_area: str = Field(min_length=1, max_length=64)
    condition: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    severity: Severity


class GroundTruthFields(BaseModel):
    """Extractable contract terms; ``None`` means the clause is absent or vague."""

    model_config = ConfigDict(extra="forbid")

    renewal_date: str | None = None
    notice_period_days: int | None = None
    auto_renewal: bool | None = None
    liability_cap: int | None = None
    liability_cap_type: Literal["months_of_fees", "fixed_amount"] | None = None
    governing_law: str | None = None
    termination_for_convenience: bool | None = None
    data_protection_addendum: bool | None = None
    payment_terms_days: int | None = None


class GroundTruthViolation(BaseModel):
    """A policy rule the contract fails, or that cannot be applied cleanly."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    field: str = Field(min_length=1, max_length=64)
    expected_outcome: ViolationOutcome


class GroundTruthRecord(BaseModel):
    """Expected extraction and policy result for one contract."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    fields: GroundTruthFields
    field_sections: dict[str, str] = Field(default_factory=dict)
    violations: list[GroundTruthViolation] = Field(default_factory=list)


class EvalFilters(BaseModel):
    """Retrieval filters applied before ranking."""

    model_config = ConfigDict(extra="forbid")

    document_types: list[str] = Field(default_factory=list)
    vendor: str | None = None


class ExpectedSection(BaseModel):
    """A document section a case expects to see cited."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=300)


class EvalExpectation(BaseModel):
    """What a correct retriever returns for a case."""

    model_config = ConfigDict(extra="forbid")

    relevant: list[ExpectedSection] = Field(default_factory=list)
    outcome: RetrievalOutcome


class RetrievalEvalCase(BaseModel):
    """One retrieval evaluation case.

    ``must_not_contain`` lists external ids that must never appear in a result
    for the case access groups -- the binding assertion for unauthorized cases.
    ``duplicate_of`` names the superseded version a duplicate case must not
    return in place of the newest one.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1)
    filters: EvalFilters
    access_groups: list[AccessGroup]
    expected: EvalExpectation
    category: EvalCategory
    must_not_contain: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    notes: str = ""


class Manifest(BaseModel):
    """Dataset identity and per-document content hashes (drift detection)."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(min_length=1, max_length=64)
    seed: int
    generated_with: str = "northforge.data.synthetic"
    document_count: int = Field(ge=1)
    sha256: dict[str, str] = Field(default_factory=dict)


class Dataset(BaseModel):
    """The whole generated dataset, in memory."""

    model_config = ConfigDict(extra="forbid")

    manifest: Manifest
    documents: list[DocumentRecord]
    vendors: list[VendorRecord]
    policy_rules: list[PolicyRuleRecord]
    ground_truth: list[GroundTruthRecord]
    retrieval_eval: list[RetrievalEvalCase]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(directory: Path) -> Dataset:
    """Read a written dataset back from ``directory``.

    Documents are loaded in sorted ``external_id`` order so a loaded dataset
    compares equal to the generated one regardless of directory order.
    """
    manifest = Manifest.model_validate(_read_json(directory / MANIFEST_FILE))
    document_paths = sorted((directory / DOCUMENTS_DIR).glob("*.json"), key=lambda path: path.name)
    documents = [DocumentRecord.model_validate(_read_json(path)) for path in document_paths]
    vendors = [VendorRecord.model_validate(item) for item in _read_json(directory / VENDORS_FILE)]
    policy_rules = [
        PolicyRuleRecord.model_validate(item) for item in _read_json(directory / POLICY_RULES_FILE)
    ]
    ground_truth = [
        GroundTruthRecord.model_validate(item) for item in _read_json(directory / GROUND_TRUTH_FILE)
    ]
    retrieval_eval = [
        RetrievalEvalCase.model_validate(item)
        for item in _read_json(directory / RETRIEVAL_EVAL_FILE)
    ]
    return Dataset(
        manifest=manifest,
        documents=documents,
        vendors=vendors,
        policy_rules=policy_rules,
        ground_truth=ground_truth,
        retrieval_eval=retrieval_eval,
    )
