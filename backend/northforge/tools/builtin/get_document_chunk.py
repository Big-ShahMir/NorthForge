"""``get_document_chunk``: fetch one chunk of one document by id.

A chunk outside the caller's access groups is treated identically to a
chunk that does not exist: ``context.retriever.get_chunk`` returns ``None``
for both, so a caller can never distinguish "wrong id" from "exists but you
can't see it". The tool itself holds no fixture-specific magic strings --
the ``__timeout__``/``__error__``/``doc_malformed`` triggers used by
``tests/tools`` live in ``FixtureRetriever``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import EvidenceChunk
from northforge.tools.context import ToolContext
from northforge.tools.errors import ToolExecutionError
from northforge.tools.spec import ToolSpec


class GetDocumentChunkInput(BaseModel):
    """Arguments for ``get_document_chunk``."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)


class GetDocumentChunkOutput(BaseModel):
    """Result of ``get_document_chunk``: the requested chunk."""

    model_config = ConfigDict(extra="forbid")

    chunk: EvidenceChunk


GET_DOCUMENT_CHUNK_SPEC = ToolSpec(
    name="get_document_chunk",
    description=(
        "Fetch a single document chunk by document id and chunk id, for "
        "when the exact identifiers are already known (for example, from a "
        "prior search_documents result)."
    ),
    input_model=GetDocumentChunkInput,
    output_model=GetDocumentChunkOutput,
    side_effect_class="read_only",
    access_scope="project_documents",
    kind="lookup",
)


async def get_document_chunk(args: BaseModel, context: ToolContext) -> dict[str, Any]:
    assert isinstance(args, GetDocumentChunkInput)

    chunk = await context.retriever.get_chunk(
        context.project_id, args.document_id, args.chunk_id, context.access_groups
    )
    if chunk is None:
        raise ToolExecutionError(
            f"no chunk found for document_id={args.document_id!r} chunk_id={args.chunk_id!r}",
            retryable=False,
        )

    output = GetDocumentChunkOutput(chunk=chunk)
    return output.model_dump(mode="json")
