"""Retrieval quality gate: every ``retrieval_eval.json`` case through ``PostgresRetriever``.

Ingesting the full 48-document corpus takes a few hundred milliseconds
against the test database (measured directly; well under the 3-second
threshold the Phase 3 design uses to decide between a session-scoped and a
per-test fixture), so this module ingests once per test function via the
ordinary function-scoped ``db_session`` fixture rather than adding a second,
session-scoped database fixture alongside it. There is exactly one test
function that ingests, so in practice the corpus is only loaded once per
test run of this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.data.synthetic.models import RetrievalEvalCase, load_dataset
from northforge.db.models import Project, User
from northforge.ingestion.pipeline import ingest_dataset
from northforge.retrieval.postgres import PostgresRetriever
from northforge.retrieval.retriever import RetrievalOutcome, RetrievalQuery
from northforge.storage.memory import MemoryObjectStorage

pytestmark = pytest.mark.usefixtures("migrated_database")

DATASET_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"
DATASET = load_dataset(DATASET_DIR)

NORMAL_HIT_AT_8_THRESHOLD = 0.85
NORMAL_MRR_THRESHOLD = 0.5

_HARD_GATED_CATEGORIES = {"miss", "conflicting_policy", "unauthorized", "duplicate"}


async def _ingest_fresh_project(session: AsyncSession) -> Project:
    user = User(clerk_user_id="dev|retrieval-quality")
    session.add(user)
    await session.flush()
    project = Project(
        owner_id=user.id, name="retrieval-quality", description="", vertical="contract_review"
    )
    session.add(project)
    await session.flush()
    await ingest_dataset(session, MemoryObjectStorage(), project.id, DATASET_DIR, "v1")
    return project


def _case_passed(case: RetrievalEvalCase, outcome: RetrievalOutcome) -> bool | None:
    """Whether ``outcome`` satisfies the hard gate for ``case``'s category.

    Returns ``None`` for categories with no hard gate (their row is still
    printed, but does not fail the test).
    """
    if case.category == "miss":
        return outcome.status == "insufficient_evidence"
    if case.category == "conflicting_policy":
        return outcome.status == "conflicting_evidence"
    if case.category == "unauthorized":
        returned_ids = {chunk.document_id for chunk in outcome.chunks}
        return returned_ids.isdisjoint(case.must_not_contain)
    if case.category == "duplicate":
        content_hashes = [chunk.metadata.get("content_hash") for chunk in outcome.chunks]
        return len(content_hashes) == len(set(content_hashes))
    return None


async def test_retrieval_eval_cases_meet_the_quality_gate(db_session: AsyncSession) -> None:
    project = await _ingest_fresh_project(db_session)
    retriever = PostgresRetriever(db_session)

    rows: list[tuple[str, str, bool | None]] = []
    normal_hits = 0
    normal_count = 0
    reciprocal_ranks: list[float] = []
    hard_gate_failures: list[tuple[str, str]] = []

    for case in DATASET.retrieval_eval:
        query = RetrievalQuery(
            project_id=str(project.id),
            query=case.query,
            access_groups=frozenset(case.access_groups),
            document_types=case.filters.document_types,
            vendor=case.filters.vendor,
            limit=8,
        )
        outcome = await retriever.search(query)

        passed: bool | None
        if case.category == "normal":
            normal_count += 1
            expected = {(item.external_id, item.section) for item in case.expected.relevant}
            rank = next(
                (
                    position
                    for position, chunk in enumerate(outcome.chunks, start=1)
                    if (chunk.document_id, chunk.metadata.get("heading")) in expected
                ),
                None,
            )
            passed = rank is not None
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            if passed:
                normal_hits += 1
        else:
            passed = _case_passed(case, outcome)

        if case.category in _HARD_GATED_CATEGORIES and passed is False:
            hard_gate_failures.append((case.category, case.case_id))

        rows.append((case.category, case.case_id, passed))

    by_category: dict[str, list[bool | None]] = {}
    for category, _case_id, passed in rows:
        by_category.setdefault(category, []).append(passed)

    print("\nRetrieval evaluation results by category:")
    print(f"{'category':20s} {'cases':>6s} {'passed':>7s}")
    for category in sorted(by_category):
        results = by_category[category]
        scored = [r for r in results if r is not None]
        passed_count = sum(1 for r in scored if r)
        summary = f"{passed_count}/{len(scored)}" if scored else "n/a"
        print(f"{category:20s} {len(results):6d} {summary:>7s}")

    hit_at_8 = normal_hits / normal_count if normal_count else 0.0
    mrr = sum(reciprocal_ranks) / normal_count if normal_count else 0.0
    print(f"\nnormal: hit@8={hit_at_8:.3f} mrr={mrr:.3f} (n={normal_count})")

    assert not hard_gate_failures, f"hard-gated cases failed: {hard_gate_failures}"
    assert hit_at_8 >= NORMAL_HIT_AT_8_THRESHOLD, (
        f"hit@8={hit_at_8:.3f} is below the {NORMAL_HIT_AT_8_THRESHOLD} gate"
    )
    assert mrr >= NORMAL_MRR_THRESHOLD, f"mrr={mrr:.3f} is below the {NORMAL_MRR_THRESHOLD} gate"
