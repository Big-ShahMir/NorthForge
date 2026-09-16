"""Evidence models shared by tools, step outputs, and the runtime.

Every material claim in a NorthForge result must trace back to a
``Citation`` that names a document and chunk identifier. Tools return
``EvidenceChunk`` values that carry the identifiers needed to build one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["low", "medium", "high"]


class EvidenceChunk(BaseModel):
    """A retrievable passage with the identifiers needed for citations."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    document_name: str = Field(min_length=1, max_length=300)
    document_type: str = Field(min_length=1, max_length=64)
    text: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """A reference from a claim to the chunk that supports it."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    quote: str | None = None


class PolicyRule(BaseModel):
    """A company policy rule, cited to the policy document chunk that states it."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    policy_document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    policy_area: str = Field(min_length=1, max_length=64)
    condition: str
    requirement: str
    severity: Severity = "medium"
