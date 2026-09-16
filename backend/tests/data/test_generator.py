"""Tests for the synthetic dataset generator and its committed output.

Two things are being protected here. First, determinism: the committed corpus in
``backend/data/synthetic`` must be exactly what the generator produces for seed
``20260916``, so a reviewer can read the data and CI can detect drift. Second,
the promises the rest of Phase 3 builds on: edge-case coverage, addressable
ground truth, honest retrieval cases, and the safety rules that keep the corpus
recognisably fictional.
"""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

import pytest

from northforge.data.synthetic.generator import (
    CONTRACT_PLANS,
    DEFAULT_SEED,
    DEFAULT_VERSION,
    MISS_CASE_PROBES,
    POLICY_PLANS,
    VENDORS,
    denied_company_name,
    generate_dataset,
    write_dataset,
)
from northforge.data.synthetic.models import (
    DOCUMENTS_DIR,
    Dataset,
    DocumentRecord,
    load_dataset,
)

COMMITTED_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"

MINIMUM_EDGE_CASES: dict[str, int] = {
    "ambiguous_terms": 2,
    "conflicting_policy": 2,
    "long_document": 1,
    "malformed_document": 1,
    "missing_clause": 2,
    "near_duplicate": 2,
    "unauthorized": 2,
}

MINIMUM_EVAL_CASES: dict[str, int] = {
    "ambiguous": 1,
    "conflicting_policy": 2,
    "duplicate": 2,
    "injection": 1,
    "long_document": 1,
    "malformed": 1,
    "miss": 4,
    "normal": 12,
    "unauthorized": 3,
}


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return generate_dataset(seed=DEFAULT_SEED, version=DEFAULT_VERSION)


@pytest.fixture(scope="module")
def documents(dataset: Dataset) -> dict[str, DocumentRecord]:
    return {document.external_id: document for document in dataset.documents}


def _headings(content: str) -> list[str]:
    return [line[3:].strip() for line in content.splitlines() if line.startswith("## ")]


def _section_text(content: str, heading: str) -> str:
    lines = content.splitlines()
    start = lines.index(f"## {heading}")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        body.append(line)
    return "\n".join(body).strip()


# --------------------------------------------------------------------------
# Determinism and the committed files
# --------------------------------------------------------------------------


def test_generation_is_reproducible_in_memory(dataset: Dataset) -> None:
    again = generate_dataset(seed=DEFAULT_SEED, version=DEFAULT_VERSION)
    assert again == dataset


def test_regeneration_is_byte_identical_to_committed_files(
    dataset: Dataset, tmp_path: Path
) -> None:
    write_dataset(dataset, tmp_path)

    regenerated = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.json")}
    committed = {
        path.relative_to(COMMITTED_DIR).as_posix() for path in COMMITTED_DIR.rglob("*.json")
    }
    assert regenerated == committed, "committed dataset has files the generator does not produce"

    for relative in sorted(regenerated):
        assert (tmp_path / relative).read_bytes() == (COMMITTED_DIR / relative).read_bytes(), (
            f"{relative} differs from the committed dataset; regenerate with "
            "uv run python -m northforge.data.synthetic --out data/synthetic "
            "--seed 20260916 --version v1"
        )


def test_committed_dataset_loads_back_into_the_same_model(dataset: Dataset) -> None:
    assert load_dataset(COMMITTED_DIR) == dataset


def test_manifest_hashes_match_document_content(dataset: Dataset) -> None:
    assert dataset.manifest.seed == DEFAULT_SEED
    assert dataset.manifest.dataset_version == DEFAULT_VERSION
    assert dataset.manifest.generated_with == "northforge.data.synthetic"
    assert dataset.manifest.document_count == len(dataset.documents)
    assert set(dataset.manifest.sha256) == {doc.external_id for doc in dataset.documents}

    for document in dataset.documents:
        digest = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        assert dataset.manifest.sha256[document.external_id] == digest


def test_committed_json_files_use_the_agreed_encoding() -> None:
    for path in sorted(COMMITTED_DIR.rglob("*.json")):
        raw = path.read_bytes()
        assert b"\r\n" not in raw, f"{path.name} has Windows line endings"
        assert raw.endswith(b"\n")
        payload = json.loads(raw.decode("utf-8"))
        expected = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert raw.decode("utf-8") == expected


# --------------------------------------------------------------------------
# Corpus composition
# --------------------------------------------------------------------------


def test_corpus_counts(dataset: Dataset) -> None:
    contracts = [doc for doc in dataset.documents if doc.document_type != "policy"]
    policies = [doc for doc in dataset.documents if doc.document_type == "policy"]

    assert len(contracts) == len(CONTRACT_PLANS) == 40
    assert len(policies) == len(POLICY_PLANS) == 8
    assert len(dataset.vendors) == len(VENDORS) == 15
    assert len({doc.external_id for doc in dataset.documents}) == len(dataset.documents)
    assert len({vendor.vendor_id for vendor in dataset.vendors}) == len(dataset.vendors)
    assert len({rule.rule_id for rule in dataset.policy_rules}) == len(dataset.policy_rules)
    assert {gt.external_id for gt in dataset.ground_truth} == {doc.external_id for doc in contracts}


def test_documents_are_sorted_and_well_formed(dataset: Dataset) -> None:
    external_ids = [document.external_id for document in dataset.documents]
    assert external_ids == sorted(external_ids)

    for document in dataset.documents:
        lines = document.content.splitlines()
        assert lines[0] == f"# {document.name}"
        assert lines[1] == ""
        assert lines[2].strip(), f"{document.external_id} has no preamble paragraph"
        assert not lines[2].startswith("#")
        assert _headings(document.content), f"{document.external_id} has no ## sections"
        assert document.content.endswith("\n")


def test_dates_stay_inside_the_designed_window(dataset: Dataset) -> None:
    for document in dataset.documents:
        for value in (document.effective_date, document.expires_at):
            if value is None:
                continue
            assert 2025 <= int(value[:4]) <= 2028, f"{document.external_id}: {value}"
        if document.effective_date is not None and document.expires_at is not None:
            assert document.effective_date < document.expires_at


def test_every_vendor_is_referenced_by_a_contract(dataset: Dataset) -> None:
    referenced = {doc.vendor for doc in dataset.documents if doc.vendor is not None}
    assert referenced == {vendor.name for vendor in dataset.vendors}


def test_policy_rules_address_existing_policy_sections(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    for rule in dataset.policy_rules:
        policy = documents[rule.policy_document_id]
        assert policy.document_type == "policy"
        assert rule.section in _headings(policy.content), rule.rule_id


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_every_edge_case_category_meets_its_minimum(dataset: Dataset) -> None:
    counts: collections.Counter[str] = collections.Counter(
        str(edge_case) for document in dataset.documents for edge_case in document.edge_cases
    )
    for edge_case, minimum in sorted(MINIMUM_EDGE_CASES.items()):
        assert counts[edge_case] >= minimum, f"{edge_case}: {counts[edge_case]} < {minimum}"


def test_injection_fixtures_are_exactly_four_and_marked(dataset: Dataset) -> None:
    injected = [doc for doc in dataset.documents if "injection" in doc.edge_cases]
    assert len(injected) == 4

    for document in injected:
        assert document.metadata["injection_fixture"] is True
        section = document.metadata["injection_section"]
        assert section in _headings(document.content)
        payload = _section_text(document.content, section).lower()
        assert any(
            marker in payload
            for marker in ("ignore previous instructions", "disregard", "do not cite")
        ), document.external_id

    marked = [doc for doc in dataset.documents if doc.metadata.get("injection_fixture")]
    assert {doc.external_id for doc in marked} == {doc.external_id for doc in injected}


def test_missing_clause_documents_really_miss_the_clause(dataset: Dataset) -> None:
    documents = [doc for doc in dataset.documents if "missing_clause" in doc.edge_cases]
    assert len(documents) >= 2
    for document in documents:
        assert "Limitation of Liability" not in _headings(document.content)
        assert document.metadata["missing_sections"] == ["Limitation of Liability"]


def test_ambiguous_documents_state_no_notice_period(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    ground_truth = {record.external_id: record for record in dataset.ground_truth}
    ambiguous = [doc for doc in dataset.documents if "ambiguous_terms" in doc.edge_cases]
    assert len(ambiguous) >= 2
    for document in ambiguous:
        assert "reasonable notice" in document.content.lower()
        record = ground_truth[document.external_id]
        assert record.fields.notice_period_days is None
        assert "notice_period_days" not in record.field_sections


def test_long_document_has_at_least_forty_sections(dataset: Dataset) -> None:
    long_documents = [doc for doc in dataset.documents if "long_document" in doc.edge_cases]
    assert len(long_documents) >= 1
    for document in long_documents:
        assert len(_headings(document.content)) >= 40


def test_malformed_document_carries_the_planned_defects(dataset: Dataset) -> None:
    malformed = [doc for doc in dataset.documents if "malformed_document" in doc.edge_cases]
    assert len(malformed) >= 1
    for document in malformed:
        content = document.content
        assert document.metadata["malformed"] is True
        assert "\n##Termination\n" in content, "expected a heading with no space after ##"
        assert content.count("## Payment Terms") == 2, "expected a duplicated section"
        assert "#### " in content, "expected a stray deeper heading"
        assert any(char in content for char in "\x0b\x0c"), "expected stray control characters"


def test_near_duplicate_versions_differ_only_in_the_renewal_clause(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    renewals = [doc for doc in dataset.documents if doc.metadata.get("duplicate_of")]
    assert len(renewals) >= 2

    for renewal in renewals:
        original = documents[str(renewal.metadata["duplicate_of"])]
        assert original.effective_date is not None and renewal.effective_date is not None
        assert original.effective_date < renewal.effective_date
        assert original.metadata["superseded_by"] == renewal.external_id

        differing = [
            heading
            for heading in _headings(original.content)
            if _section_text(original.content, heading) != _section_text(renewal.content, heading)
        ]
        assert differing == ["Term and Renewal"], renewal.external_id


def test_restricted_documents_are_marked_unauthorized(dataset: Dataset) -> None:
    for document in dataset.documents:
        if document.access_group != "procurement":
            assert "unauthorized" in document.edge_cases, document.external_id


def test_conflicting_policies_are_unsuperseded_and_the_valid_pair_is_superseded(
    documents: dict[str, DocumentRecord],
) -> None:
    older = documents["doc_policy_procurement_v1"]
    newer = documents["doc_policy_procurement_v2"]
    assert older.metadata["policy_area"] == newer.metadata["policy_area"] == "renewal"
    assert older.effective_date == "2025-01-01"
    assert newer.effective_date == "2026-01-01"
    assert older.metadata["supersedes"] == []
    assert newer.metadata["supersedes"] == [], "the conflicting pair must stay unresolved"
    assert "conflicting_policy" in older.edge_cases
    assert "conflicting_policy" in newer.edge_cases
    assert "thirty (30) days" in _section_text(older.content, "Renewal Notice Requirement")
    assert "sixty (60) days" in _section_text(newer.content, "Renewal Notice Requirement")

    security_v1 = documents["doc_policy_security_v1"]
    security_v2 = documents["doc_policy_security_v2"]
    assert security_v1.metadata["policy_area"] == security_v2.metadata["policy_area"] == "security"
    assert security_v2.metadata["supersedes"] == ["doc_policy_security_v1"]
    assert "conflicting_policy" not in security_v2.edge_cases


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


def test_ground_truth_sections_exist_and_contain_their_values(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    for record in dataset.ground_truth:
        content = documents[record.external_id].content
        for field_name, heading in sorted(record.field_sections.items()):
            assert f"## {heading}\n" in content, f"{record.external_id}: missing {heading!r}"
            value = getattr(record.fields, field_name)
            assert value is not None
            if isinstance(value, bool):
                continue  # booleans are expressed by the wording, not by a literal
            section = _section_text(content, heading).replace(",", "")
            if field_name == "liability_cap_type":
                continue  # the type is expressed by the phrasing of the cap
            assert str(value) in section, f"{record.external_id}.{field_name} not in {heading!r}"


def test_fields_without_a_section_are_null(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    from northforge.data.synthetic.generator import FIELD_TO_SECTION

    for record in dataset.ground_truth:
        headings = set(_headings(documents[record.external_id].content))
        for field_name, heading in sorted(FIELD_TO_SECTION.items()):
            if heading not in headings:
                assert getattr(record.fields, field_name) is None, (
                    f"{record.external_id}.{field_name} has a value but {heading!r} is absent"
                )


def test_violations_reference_known_rules(dataset: Dataset) -> None:
    rule_ids = {rule.rule_id for rule in dataset.policy_rules}
    field_names = set(type(dataset.ground_truth[0].fields).model_fields)
    seen_outcomes: set[str] = set()

    for record in dataset.ground_truth:
        for violation in record.violations:
            assert violation.rule_id in rule_ids, record.external_id
            assert violation.field in field_names
            seen_outcomes.add(violation.expected_outcome)

    assert {"violation", "requires_review", "conflicting_policy"} <= seen_outcomes


# --------------------------------------------------------------------------
# Retrieval evaluation cases
# --------------------------------------------------------------------------


def test_retrieval_case_count_and_category_coverage(dataset: Dataset) -> None:
    cases = dataset.retrieval_eval
    assert 30 <= len(cases) <= 40
    assert len({case.case_id for case in cases}) == len(cases)

    counts: collections.Counter[str] = collections.Counter(str(case.category) for case in cases)
    assert set(counts) == set(MINIMUM_EVAL_CASES)
    for category, minimum in sorted(MINIMUM_EVAL_CASES.items()):
        assert counts[category] >= minimum, f"{category}: {counts[category]} < {minimum}"


def test_expected_sections_exist_in_the_named_documents(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    for case in dataset.retrieval_eval:
        for expected in case.expected.relevant:
            document = documents[expected.external_id]
            assert expected.section in _headings(document.content), case.case_id
            assert document.access_group in case.access_groups, case.case_id
            if case.filters.vendor is not None:
                assert document.vendor == case.filters.vendor, case.case_id
            if case.filters.document_types:
                assert document.document_type in case.filters.document_types, case.case_id


def test_normal_cases_share_vocabulary_with_their_target_section(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    normal = [case for case in dataset.retrieval_eval if case.category == "normal"]
    assert len(normal) >= 12

    for case in normal:
        assert case.expected.outcome == "ok"
        assert len(case.expected.relevant) == 1
        expected = case.expected.relevant[0]
        document = documents[expected.external_id]
        section = _section_text(document.content, expected.section).lower()
        query_terms = {
            term.strip(",.?").lower() for term in case.query.split() if len(term.strip(",.?")) > 4
        }
        shared = {term for term in query_terms if term in section}
        assert len(shared) >= 3, f"{case.case_id}: only {sorted(shared)} overlap with the clause"

        # The filters must leave exactly one document that could answer the query.
        candidates = [
            candidate
            for candidate in documents.values()
            if candidate.access_group in case.access_groups
            and (case.filters.vendor is None or candidate.vendor == case.filters.vendor)
            and (
                not case.filters.document_types
                or candidate.document_type in case.filters.document_types
            )
            and expected.section in _headings(candidate.content)
        ]
        assert [c.external_id for c in candidates] == [expected.external_id], case.case_id


def test_miss_cases_ask_about_clauses_the_corpus_does_not_have(dataset: Dataset) -> None:
    corpus = "\n".join(document.content.lower() for document in dataset.documents)
    miss_cases = {case.case_id: case for case in dataset.retrieval_eval if case.category == "miss"}
    assert len(miss_cases) >= 4
    assert set(MISS_CASE_PROBES) == set(miss_cases)

    for case_id, probes in sorted(MISS_CASE_PROBES.items()):
        case = miss_cases[case_id]
        assert case.expected.outcome == "insufficient_evidence"
        assert case.expected.relevant == []
        for probe in probes:
            assert probe not in corpus, f"{case_id}: {probe!r} now exists in the corpus"
            assert probe in case.query.lower()


def test_unauthorized_cases_name_only_restricted_documents(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    cases = [case for case in dataset.retrieval_eval if case.category == "unauthorized"]
    assert len(cases) >= 3

    for case in cases:
        assert case.access_groups == ["procurement"]
        assert case.expected.relevant == []
        assert case.expected.outcome == "insufficient_evidence"
        assert case.must_not_contain
        for external_id in case.must_not_contain:
            document = documents[external_id]
            assert document.access_group != "procurement", case.case_id
            assert "unauthorized" in document.edge_cases


def test_duplicate_cases_expect_the_newest_version_only(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    cases = [case for case in dataset.retrieval_eval if case.category == "duplicate"]
    assert len(cases) >= 2

    for case in cases:
        assert case.duplicate_of is not None
        superseded = documents[case.duplicate_of]
        assert len(case.expected.relevant) == 1
        newest = documents[case.expected.relevant[0].external_id]
        assert newest.metadata["duplicate_of"] == superseded.external_id
        assert superseded.effective_date is not None and newest.effective_date is not None
        assert superseded.effective_date < newest.effective_date
        assert case.duplicate_of not in {item.external_id for item in case.expected.relevant}

        section = case.expected.relevant[0].section
        assert _section_text(newest.content, section) == _section_text(superseded.content, section)


def test_conflicting_policy_cases_expect_both_editions(dataset: Dataset) -> None:
    cases = [case for case in dataset.retrieval_eval if case.category == "conflicting_policy"]
    assert len(cases) == 2

    for case in cases:
        assert case.expected.outcome == "conflicting_evidence"
        assert {item.external_id for item in case.expected.relevant} == {
            "doc_policy_procurement_v1",
            "doc_policy_procurement_v2",
        }


def test_injection_cases_point_at_the_injected_sections(
    dataset: Dataset, documents: dict[str, DocumentRecord]
) -> None:
    cases = [case for case in dataset.retrieval_eval if case.category == "injection"]
    assert len(cases) == 4

    for case in cases:
        expected = case.expected.relevant[0]
        document = documents[expected.external_id]
        assert document.metadata["injection_fixture"] is True
        assert document.metadata["injection_section"] == expected.section


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_no_real_company_names_anywhere(dataset: Dataset) -> None:
    for vendor in dataset.vendors:
        assert denied_company_name(vendor.name) is None, vendor.name
    for document in dataset.documents:
        assert denied_company_name(document.name) is None, document.external_id
        assert denied_company_name(document.content) is None, document.external_id
        assert denied_company_name(json.dumps(document.metadata)) is None, document.external_id
    for case in dataset.retrieval_eval:
        assert denied_company_name(case.query) is None, case.case_id


def test_denylist_matcher_is_case_insensitive_and_word_bounded() -> None:
    assert denied_company_name("a contract with Microsoft Ireland") == "Microsoft"
    assert denied_company_name("ORACLE") == "ORACLE"
    assert denied_company_name("Saltmarsh Security Labs") is None
    assert denied_company_name("sapphire blue") is None


def test_only_example_com_addresses_appear(dataset: Dataset) -> None:
    for document in dataset.documents:
        for token in document.content.split():
            if "@" in token:
                assert token.rstrip(".,;").endswith(".example.com"), document.external_id


def test_documents_declare_themselves_fictional(dataset: Dataset) -> None:
    for document in dataset.documents:
        assert document.metadata["fictional"] is True
        preamble = document.content.splitlines()[2].lower()
        assert "fictional" in preamble or "synthetic" in preamble, document.external_id


def test_signatories_are_marked_fictional(dataset: Dataset) -> None:
    for document in dataset.documents:
        if document.document_type == "policy":
            assert "signatories" not in document.metadata
            continue
        assert document.metadata["fictional_signatories"] is True
        signatories = document.metadata["signatories"]
        assert len(signatories) == 2
        signature_clause = _section_text(document.content, "Signatures")
        for signatory in signatories:
            assert signatory["name"] in signature_clause
        assert "fictional" in signature_clause.lower()


def test_written_documents_directory_only_holds_documents(tmp_path: Path, dataset: Dataset) -> None:
    write_dataset(dataset, tmp_path)
    stale = tmp_path / DOCUMENTS_DIR / "doc_stale.json"
    stale.write_text("{}", encoding="utf-8")

    write_dataset(dataset, tmp_path)
    assert not stale.exists(), "a second write must not leave removed documents behind"
