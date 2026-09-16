from __future__ import annotations

from northforge.retrieval.chunking import chunk_markdown


def test_offsets_slice_back_to_the_original_text_exactly() -> None:
    content = (
        "## Introduction\n"
        "This Master Service Agreement is entered into between Acme and Customer. "
        "It governs the provision of services described in the Order Form.\n\n"
        "## Renewal Term\n"
        "This Agreement renews automatically for successive twelve month terms. "
        "Either party may decline renewal with sixty days notice.\n"
    )

    chunks = chunk_markdown(content)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert content[chunk.start_offset : chunk.end_offset] == chunk.text


def test_chunk_ids_are_sequential_from_c01() -> None:
    content = (
        "## Section One\n"
        "First clause text goes here as a single sentence.\n\n"
        "## Section Two\n"
        "Second clause text goes here as a single sentence.\n\n"
        "## Section Three\n"
        "Third clause text goes here as a single sentence.\n"
    )

    chunks = chunk_markdown(content)

    assert [c.chunk_id for c in chunks] == ["c01", "c02", "c03"]
    assert [c.sequence for c in chunks] == [1, 2, 3]


def test_headings_are_captured_per_chunk() -> None:
    content = "## Termination\nEither party may terminate for material breach.\n"

    chunks = chunk_markdown(content)

    assert len(chunks) == 1
    assert chunks[0].heading == "Termination"


def test_preamble_before_first_heading_has_no_heading() -> None:
    content = (
        "This Agreement is between Acme and Customer.\n\n"
        "## Renewal\nThis Agreement renews automatically for twelve months.\n"
    )

    chunks = chunk_markdown(content)

    assert chunks[0].heading is None
    assert chunks[1].heading == "Renewal"


def test_long_section_is_capped_at_max_tokens() -> None:
    sentences = [
        f"This is clause sentence number {i} about vendor obligations. " for i in range(60)
    ]
    content = "## Scope of Services\n" + "".join(sentences) + "\n"

    chunks = chunk_markdown(content, max_tokens=50)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= 50
    # every chunk still slices back exactly to the source text
    for chunk in chunks:
        assert content[chunk.start_offset : chunk.end_offset] == chunk.text


def test_long_section_default_cap_is_220_tokens() -> None:
    sentence = "The vendor shall provide services in accordance with this section. "
    long_body = sentence * 40  # well over 220 tokens
    content = f"## Scope of Services\n{long_body}\n"

    chunks = chunk_markdown(content)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= 220


def test_consecutive_chunks_in_a_section_share_a_sentence_overlap() -> None:
    sentences = [f"This is distinct clause sentence number {i} here. " for i in range(20)]
    content = "## Obligations\n" + "".join(sentences) + "\n"

    chunks = chunk_markdown(content, max_tokens=30)

    assert len(chunks) >= 2
    # The last sentence of chunk N must appear verbatim as the start of
    # chunk N+1 (the one-sentence overlap).
    first_sentences = [s.strip() for s in chunks[0].text.split(". ") if s.strip()]
    last_sentence_of_first = first_sentences[-1].rstrip(".") + "."
    assert chunks[1].text.startswith(last_sentence_of_first)


def test_control_characters_are_stripped() -> None:
    content = "## Notes\x00\nThis clause has a \x07bell and an \x1bescape character in it.\n"

    chunks = chunk_markdown(content)

    assert len(chunks) == 1
    assert "\x00" not in chunks[0].heading if chunks[0].heading else True
    assert "\x07" not in chunks[0].text
    assert "\x1b" not in chunks[0].text
    assert chunks[0].heading == "Notes"


def test_malformed_headings_are_tolerated() -> None:
    content = "###Extra Hashes And No Space\nBody text for this odd heading.\n"

    chunks = chunk_markdown(content)

    assert len(chunks) == 1
    assert chunks[0].heading == "Extra Hashes And No Space"


def test_duplicated_heading_produces_two_sections() -> None:
    content = (
        "## Renewal\nFirst version of the renewal clause text.\n\n"
        "## Renewal\nSecond version of the renewal clause text.\n"
    )

    chunks = chunk_markdown(content)

    assert len(chunks) == 2
    assert chunks[0].heading == "Renewal"
    assert chunks[1].heading == "Renewal"
    assert chunks[0].text != chunks[1].text


def test_empty_heading_with_no_body_produces_no_chunk() -> None:
    content = "## Empty Section\n\n## Real Section\nThis section has body text in it.\n"

    chunks = chunk_markdown(content)

    assert len(chunks) == 1
    assert chunks[0].heading == "Real Section"


def test_no_headings_at_all_is_a_single_unheaded_section() -> None:
    content = "Just a plain paragraph with no markdown headings at all in it.\n"

    chunks = chunk_markdown(content)

    assert len(chunks) == 1
    assert chunks[0].heading is None
    assert content[chunks[0].start_offset : chunks[0].end_offset] == chunks[0].text


def test_chunking_is_deterministic() -> None:
    content = (
        "## Alpha\nAlpha clause text goes here in full.\n\n"
        "## Beta\nBeta clause text goes here in full.\n"
    )

    first = chunk_markdown(content)
    second = chunk_markdown(content)

    assert first == second
