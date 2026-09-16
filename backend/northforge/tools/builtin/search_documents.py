"""``search_documents``: access-filtered, ranked search over the project's corpus.

The tool itself holds no ranking or storage logic and no magic strings --
it builds a ``RetrievalQuery`` from its validated arguments and the calling
``ToolContext``, and delegates entirely to ``context.retriever`` (either
``PostgresRetriever`` in production or ``FixtureRetriever`` in tests that
have no database). The ``__timeout__``/``__error__`` failure triggers used
by ``tests/tools`` live in ``FixtureRetriever``, not here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from northforge.retrieval.retriever import RetrievalQuery
from northforge.schemas.evidence import EvidenceChunk
from northforge.tools.context import ToolContext
from northforge.tools.spec import ToolSpec


class SearchDocumentsInput(BaseModel):
    """Arguments for ``search_documents``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    document_types: list[str] = Field(default_factory=list)
    vendor: str | None = None
    limit: int = Field(default=8, ge=1, le=20)


class SearchOutcome(BaseModel):
    """The retrieval outcome that accompanies a ``search_documents`` result."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "insufficient_evidence", "conflicting_evidence"]
    reason: str | None = None


class SearchDocumentsOutput(BaseModel):
    """Result of ``search_documents``: matching chunks, best match first, plus outcome."""

    model_config = ConfigDict(extra="forbid")

    chunks: list[EvidenceChunk] = Field(default_factory=list)
    outcome: SearchOutcome


SEARCH_DOCUMENTS_SPEC = ToolSpec(
    name="search_documents",
    description=(
        "Search the project's document corpus for chunks relevant to a "
        "natural-language query, optionally filtered by document type or "
        "vendor. Ranking is by full-text relevance, not semantic "
        "similarity."
    ),
    input_model=SearchDocumentsInput,
    output_model=SearchDocumentsOutput,
    side_effect_class="read_only",
    access_scope="project_documents",
    kind="retrieval",
)


async def search_documents(args: BaseModel, context: ToolContext) -> dict[str, Any]:
    assert isinstance(args, SearchDocumentsInput)

    query = RetrievalQuery(
        project_id=context.project_id,
        query=args.query,
        access_groups=context.access_groups,
        document_types=args.document_types,
        vendor=args.vendor,
        limit=args.limit,
    )
    result = await context.retriever.search(query)

    output = SearchDocumentsOutput(
        chunks=result.chunks,
        outcome=SearchOutcome(status=result.status, reason=result.reason),
    )
    return output.model_dump(mode="json")
