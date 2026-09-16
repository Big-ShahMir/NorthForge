"""Deterministic markdown chunking for retrieval.

Documents are split on ``##`` headings, then each section's body is further
split so no chunk exceeds a token cap (approximated as whitespace-separated
tokens -- not a model tokenizer, documented here because it under- or
over-counts relative to any real tokenizer, but it is cheap, deterministic,
and good enough to bound chunk size for full-text search). Consecutive
chunks within the same section share a one-sentence overlap so a clause
split across a chunk boundary is not orphaned from its neighbour.

Offsets are computed against the *normalized* content (control characters
stripped first), so ``normalized_content[chunk.start_offset:chunk.end_offset]
== chunk.text`` always holds; there is no way to recover offsets into a raw
string that still contains control characters, because those characters are
removed before any offset is computed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# C0 control characters other than tab (0x09) and newline (0x0a), plus DEL.
# Carriage returns are stripped too, which has the side effect of folding
# "\r\n" line endings down to "\n".
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# A "##" (or more) heading at the start of a line, tolerant of a missing
# space after the hashes and of any number of leading hashes >= 2 (malformed
# documents may use "###" or "##" inconsistently for what is conceptually
# the same clause-heading level).
_HEADING_PATTERN = re.compile(r"^#{2,}[ \t]*(.*)$", re.MULTILINE)

# End of a sentence: one or more of . ! ? followed by whitespace or the end
# of the text. This is a heuristic, not a real sentence boundary detector;
# it is only used to choose a one-sentence overlap point between chunks.
_SENTENCE_END_PATTERN = re.compile(r"[.!?]+(?=\s|$)")

_TOKEN_PATTERN = re.compile(r"\S+")

DEFAULT_MAX_TOKENS = 220


@dataclass(frozen=True)
class ChunkSpan:
    """One chunk of a document's normalized markdown content."""

    chunk_id: str
    sequence: int
    heading: str | None
    text: str
    start_offset: int
    end_offset: int
    token_count: int


def _strip_control_characters(content: str) -> str:
    return _CONTROL_CHAR_PATTERN.sub("", content)


def _token_count(text: str) -> int:
    return len(_TOKEN_PATTERN.findall(text))


@dataclass(frozen=True)
class _Section:
    heading: str | None
    start: int
    end: int


def _find_sections(content: str) -> list[_Section]:
    """Split ``content`` into heading-delimited sections.

    A section's span excludes its own heading line; it runs from the end of
    that heading line to the start of the next heading (or end of content).
    Content preceding the first heading, if any and non-blank, becomes a
    section with ``heading=None``.
    """
    matches = list(_HEADING_PATTERN.finditer(content))
    sections: list[_Section] = []

    preamble_end = matches[0].start() if matches else len(content)
    if content[:preamble_end].strip():
        sections.append(_Section(heading=None, start=0, end=preamble_end))

    for index, match in enumerate(matches):
        heading_text = match.group(1).strip() or None
        body_start = match.end() + 1 if match.end() < len(content) else match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections.append(
            _Section(heading=heading_text, start=min(body_start, body_end), end=body_end)
        )

    return sections


def _strip_span(content: str, start: int, end: int) -> tuple[int, int]:
    """Narrow ``[start, end)`` to exclude leading/trailing whitespace."""
    segment = content[start:end]
    left_trim = len(segment) - len(segment.lstrip())
    right_trim = len(segment) - len(segment.rstrip())
    return start + left_trim, end - right_trim


def _sentence_spans(content: str, start: int, end: int) -> list[tuple[int, int]]:
    """Sentence boundaries within ``content[start:end]``, as absolute offsets."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END_PATTERN.finditer(content, start, end):
        boundary = match.end()
        spans.append((cursor, boundary))
        cursor = boundary
    if cursor < end:
        spans.append((cursor, end))
    return [(a, b) for a, b in spans if content[a:b].strip()]


def _chunk_section(content: str, section: _Section, max_tokens: int) -> list[tuple[int, int]]:
    """Token-capped, sentence-overlapping spans covering one section's body."""
    start, end = _strip_span(content, section.start, section.end)
    if start >= end:
        return []

    sentences = _sentence_spans(content, start, end)
    if not sentences:
        return []

    spans: list[tuple[int, int]] = []
    chunk_start_idx = 0
    token_total = 0
    idx = 0
    while idx < len(sentences):
        sentence_start, sentence_end = sentences[idx]
        sentence_tokens = _token_count(content[sentence_start:sentence_end])
        would_exceed = token_total + sentence_tokens > max_tokens
        if would_exceed and idx > chunk_start_idx:
            span_start = sentences[chunk_start_idx][0]
            span_end = sentences[idx - 1][1]
            spans.append((span_start, span_end))
            # Overlap the next chunk with the last sentence of this one.
            chunk_start_idx = idx - 1
            token_total = _token_count(content[sentences[chunk_start_idx][0] : sentence_end])
            idx += 1
            continue
        token_total += sentence_tokens
        idx += 1

    span_start = sentences[chunk_start_idx][0]
    span_end = sentences[-1][1]
    if not spans or spans[-1] != (span_start, span_end):
        spans.append((span_start, span_end))

    # A span that starts at a sentence boundary other than the section's own
    # start inherits the whitespace separating it from the previous
    # sentence (the sentence-span cursor does not skip it). Trim that
    # leading whitespace here so every chunk's text is clean, while keeping
    # offsets an exact slice of ``content``.
    return [_strip_span(content, s, e) for s, e in spans]


def chunk_markdown(content: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[ChunkSpan]:
    """Split ``content`` (markdown with ``##`` clause headings) into chunks.

    Deterministic: identical input always produces identical output, with
    stable sequential ``chunk_id`` values (``c01``, ``c02``, ...).
    """
    normalized = _strip_control_characters(content)
    sections = _find_sections(normalized)

    chunks: list[ChunkSpan] = []
    sequence = 0
    for section in sections:
        for start, end in _chunk_section(normalized, section, max_tokens):
            text = normalized[start:end]
            if not text.strip():
                continue
            sequence += 1
            chunks.append(
                ChunkSpan(
                    chunk_id=f"c{sequence:02d}",
                    sequence=sequence,
                    heading=section.heading,
                    text=text,
                    start_offset=start,
                    end_offset=end,
                    token_count=_token_count(text),
                )
            )
    return chunks


__all__ = ["DEFAULT_MAX_TOKENS", "ChunkSpan", "chunk_markdown"]
