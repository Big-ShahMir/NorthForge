"""``search_documents``: deterministic token-overlap search over the fixture corpus.

Ranking is intentionally not semantic: chunks are scored by the size of the
overlap between the lowercase alphanumeric tokens of the query and of the
chunk text, ties are broken by ``chunk_id``, and the result is sliced to
``limit``. This keeps results reproducible for tests and evaluation without
depending on an embedding model.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import EvidenceChunk
from northforge.tools.context import ToolContext
from northforge.tools.errors import ToolExecutionError
from northforge.tools.fixtures.corpus import (
    ERROR_TRIGGER,
    TIMEOUT_TRIGGER,
    Chunk,
    visible_chunks,
)
from northforge.tools.spec import ToolSpec

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_TIMEOUT_SLEEP_SECONDS = 3600.0


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


class SearchDocumentsInput(BaseModel):
    """Arguments for ``search_documents``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    document_types: list[str] = Field(default_factory=list)
    vendor: str | None = None
    limit: int = Field(default=8, ge=1, le=20)


class SearchDocumentsOutput(BaseModel):
    """Result of ``search_documents``: matching chunks, best match first."""

    model_config = ConfigDict(extra="forbid")

    chunks: list[EvidenceChunk] = Field(default_factory=list)


SEARCH_DOCUMENTS_SPEC = ToolSpec(
    name="search_documents",
    description=(
        "Search the project's document corpus for chunks relevant to a "
        "natural-language query, optionally filtered by document type or "
        "vendor. Ranking is by deterministic token overlap, not semantic "
        "similarity."
    ),
    input_model=SearchDocumentsInput,
    output_model=SearchDocumentsOutput,
    side_effect_class="read_only",
    access_scope="project_documents",
    kind="retrieval",
)


def _score(query_tokens: set[str], chunk: Chunk) -> int:
    return len(query_tokens & _tokenize(chunk.text))


def _to_evidence_chunk(chunk: Chunk, score: int) -> EvidenceChunk:
    return EvidenceChunk(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        document_name=chunk.document_name,
        document_type=chunk.document_type,
        text=chunk.text,
        score=float(score),
        metadata=chunk.metadata,
    )


async def search_documents(args: BaseModel, context: ToolContext) -> dict[str, Any]:
    assert isinstance(args, SearchDocumentsInput)

    if args.query == TIMEOUT_TRIGGER:
        await asyncio.sleep(_TIMEOUT_SLEEP_SECONDS)
        return SearchDocumentsOutput().model_dump(mode="json")

    if args.query == ERROR_TRIGGER:
        raise ToolExecutionError("simulated provider error from search_documents", retryable=True)

    query_tokens = _tokenize(args.query)
    candidates = visible_chunks(context.access_groups)

    if args.document_types:
        wanted_types = set(args.document_types)
        candidates = [chunk for chunk in candidates if chunk.document_type in wanted_types]

    if args.vendor is not None:
        wanted_vendor = args.vendor.strip().lower()
        candidates = [
            chunk
            for chunk in candidates
            if str(chunk.metadata.get("vendor", "")).strip().lower() == wanted_vendor
        ]

    scored = [(chunk, _score(query_tokens, chunk)) for chunk in candidates]
    matches = [(chunk, score) for chunk, score in scored if score > 0]
    matches.sort(key=lambda pair: (-pair[1], pair[0].chunk_id))
    top = matches[: args.limit]

    output = SearchDocumentsOutput(
        chunks=[_to_evidence_chunk(chunk, score) for chunk, score in top]
    )
    return output.model_dump(mode="json")
