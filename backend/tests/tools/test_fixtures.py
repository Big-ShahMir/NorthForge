from __future__ import annotations

from northforge.tools.fixtures import corpus


def test_chunk_ids_are_unique() -> None:
    keys = [(chunk.document_id, chunk.chunk_id) for chunk in corpus.chunks()]
    assert len(keys) == len(set(keys))


def test_documents_have_between_four_and_eight_chunks() -> None:
    counts: dict[str, int] = {}
    for chunk in corpus.chunks():
        counts[chunk.document_id] = counts.get(chunk.document_id, 0) + 1

    assert set(counts) == {doc.document_id for doc in corpus.documents()}
    for document_id, count in counts.items():
        assert 4 <= count <= 8, f"{document_id} has {count} chunks"


def test_every_policy_rule_cites_an_existing_chunk() -> None:
    chunk_keys = {(chunk.document_id, chunk.chunk_id) for chunk in corpus.chunks()}
    for rule in corpus.policy_rules():
        assert (rule.policy_document_id, rule.chunk_id) in chunk_keys, rule.rule_id


def test_policy_rule_ids_are_unique() -> None:
    rule_ids = [rule.rule_id for rule in corpus.policy_rules()]
    assert len(rule_ids) == len(set(rule_ids))


def test_policy_rules_cover_the_required_areas() -> None:
    areas = {rule.policy_area for rule in corpus.policy_rules()}
    assert areas == {"renewal", "liability", "termination", "data_protection"}


def test_injection_fixtures_are_present_and_marked() -> None:
    injection_chunks = [
        chunk for chunk in corpus.chunks() if chunk.metadata.get("injection_fixture") is True
    ]

    assert len(injection_chunks) >= 2
    for chunk in injection_chunks:
        lowered = chunk.text.lower()
        assert "ignore previous instructions" in lowered

    keys = {(chunk.document_id, chunk.chunk_id) for chunk in injection_chunks}
    assert ("doc_northwind_msa", "c05") in keys
    assert ("doc_blueharbor_dpa", "c04") in keys


def test_visible_chunks_filters_by_access_group() -> None:
    procurement_only = corpus.visible_chunks(frozenset({"procurement"}))
    assert all(chunk.access_group == "procurement" for chunk in procurement_only)

    with_legal = corpus.visible_chunks(frozenset({"procurement", "legal_restricted"}))
    assert len(with_legal) == len(corpus.chunks())

    nothing_visible = corpus.visible_chunks(frozenset())
    assert nothing_visible == []


def test_find_chunk_respects_access_groups() -> None:
    restricted = corpus.find_chunk(
        "doc_blueharbor_dpa", "c01", access_groups=frozenset({"procurement"})
    )
    assert restricted is None

    visible = corpus.find_chunk(
        "doc_blueharbor_dpa",
        "c01",
        access_groups=frozenset({"procurement", "legal_restricted"}),
    )
    assert visible is not None
    assert visible.document_id == "doc_blueharbor_dpa"


def test_document_access_group_lookup() -> None:
    assert corpus.document_access_group("doc_acme_msa") == "procurement"
    assert corpus.document_access_group("doc_blueharbor_dpa") == "legal_restricted"
    assert corpus.document_access_group("doc_unknown") is None
