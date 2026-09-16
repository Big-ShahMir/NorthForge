"""``get_document_chunk``: fetch one chunk of one document by id.

A chunk outside the caller's access groups is treated identically to a
chunk that does not exist: both raise a non-retryable ``ToolExecutionError``
with a generic message, so a caller can never distinguish "wrong id" from
"exists but you can't see it".
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import EvidenceChunk
from northforge.tools.context import ToolContext
from northforge.tools.errors import ToolExecutionError
from northforge.tools.fixtures.corpus import (
    ERROR_TRIGGER,
    MALFORMED_DOCUMENT_ID,
    TIMEOUT_TRIGGER,
    find_chunk,
)
from northforge.tools.spec import ToolSpec

_TIMEOUT_SLEEP_SECONDS = 3600.0


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

    if args.document_id == TIMEOUT_TRIGGER:
        await asyncio.sleep(_TIMEOUT_SLEEP_SECONDS)
        return {}

    if args.document_id == ERROR_TRIGGER:
        raise ToolExecutionError("simulated provider error from get_document_chunk", retryable=True)

    if args.document_id == MALFORMED_DOCUMENT_ID:
        # Deliberately violates EvidenceChunk (document_name requires
        # min_length=1) to exercise TOOL_OUTPUT_INVALID.
        return {
            "chunk": {
                "document_id": args.document_id,
                "chunk_id": args.chunk_id,
                "document_name": "",
                "document_type": "contract",
                "text": "",
                "metadata": {},
            }
        }

    chunk = find_chunk(args.document_id, args.chunk_id, access_groups=context.access_groups)
    if chunk is None:
        raise ToolExecutionError(
            f"no chunk found for document_id={args.document_id!r} chunk_id={args.chunk_id!r}",
            retryable=False,
        )

    output = GetDocumentChunkOutput(
        chunk=EvidenceChunk(
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            document_name=chunk.document_name,
            document_type=chunk.document_type,
            text=chunk.text,
            score=None,
            metadata=chunk.metadata,
        )
    )
    return output.model_dump(mode="json")
