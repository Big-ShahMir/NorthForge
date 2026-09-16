"""Policy rule lookup: the ``PolicyRuleStore`` protocol and its two implementations.

Mirrors ``northforge.retrieval.retriever``: ``PostgresPolicyRuleStore`` (live
database, joined to ``documents`` for access filtering) and
``FixturePolicyRuleStore`` (the Phase 2 fixture corpus, for unit tests
without a database) implement the same protocol, so ``lookup_policy_rules``
never knows which one it is holding.

``FixturePolicyRuleStore`` also owns the ``__timeout__``/``__error__``
failure triggers for ``policy_area`` (see
``northforge.tools.fixtures.corpus`` for the shared trigger constants), so
the ``lookup_policy_rules`` tool implementation itself contains no magic
strings.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from northforge.schemas.evidence import PolicyRule, Severity
from northforge.tools.errors import ToolExecutionError
from northforge.tools.fixtures import corpus
from northforge.tools.fixtures.corpus import ERROR_TRIGGER, TIMEOUT_TRIGGER

SessionFactory = async_sessionmaker[AsyncSession] | Callable[[], AsyncSession]
SessionOrFactory = AsyncSession | SessionFactory

_TIMEOUT_SLEEP_SECONDS = 3600.0

_SEVERITY_ORDER: dict[Severity, int] = {"low": 0, "medium": 1, "high": 2}


def _severity_at_least(candidate: Severity, threshold: Severity) -> bool:
    return _SEVERITY_ORDER[candidate] >= _SEVERITY_ORDER[threshold]


@runtime_checkable
class PolicyRuleStore(Protocol):
    """Access-filtered policy rule lookup for a project."""

    async def rules_for(
        self,
        project_id: str,
        policy_area: str,
        severity_at_least: Severity | None,
        access_groups: frozenset[str],
    ) -> list[PolicyRule]: ...


_RULES_SQL = text(
    """
    SELECT
        policy_rules.rule_id AS rule_id,
        documents.external_id AS policy_document_id,
        policy_rules.chunk_id AS chunk_id,
        policy_rules.policy_area AS policy_area,
        policy_rules.condition AS condition,
        policy_rules.requirement AS requirement,
        policy_rules.severity AS severity
    FROM policy_rules
    JOIN documents ON documents.id = policy_rules.policy_document_id
    WHERE policy_rules.project_id = CAST(:project_id AS uuid)
      AND policy_rules.policy_area = :policy_area
      AND documents.access_group = ANY(:access_groups)
    ORDER BY policy_rules.rule_id
    """
).bindparams(bindparam("access_groups", type_=ARRAY(String())))


class PostgresPolicyRuleStore:
    """A ``PolicyRuleStore`` backed by PostgreSQL.

    ``policy_rules`` carries no access group of its own -- it inherits the
    access group of the policy document it cites, so this query joins to
    ``documents`` and filters there, exactly like ``PostgresRetriever``.
    """

    def __init__(self, session_or_factory: SessionOrFactory) -> None:
        self._session_or_factory = session_or_factory

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if isinstance(self._session_or_factory, AsyncSession):
            yield self._session_or_factory
            return
        session = self._session_or_factory()
        try:
            yield session
        finally:
            await session.close()

    async def rules_for(
        self,
        project_id: str,
        policy_area: str,
        severity_at_least: Severity | None,
        access_groups: frozenset[str],
    ) -> list[PolicyRule]:
        params = {
            "project_id": project_id,
            "policy_area": policy_area,
            "access_groups": sorted(access_groups),
        }
        async with self._session() as session:
            result = await session.execute(_RULES_SQL, params)
            rows = result.mappings().all()

        rules = [
            PolicyRule(
                rule_id=row["rule_id"],
                policy_document_id=row["policy_document_id"],
                chunk_id=row["chunk_id"],
                policy_area=row["policy_area"],
                condition=row["condition"],
                requirement=row["requirement"],
                severity=row["severity"],
            )
            for row in rows
        ]
        if severity_at_least is not None:
            rules = [rule for rule in rules if _severity_at_least(rule.severity, severity_at_least)]
        return rules


class FixturePolicyRuleStore:
    """A ``PolicyRuleStore`` over the small, hand-authored Phase 2 fixture corpus.

    The fixture corpus has no notion of project, so ``project_id`` is
    accepted (to satisfy the ``PolicyRuleStore`` protocol) but otherwise
    ignored.
    """

    async def rules_for(
        self,
        project_id: str,
        policy_area: str,
        severity_at_least: Severity | None,
        access_groups: frozenset[str],
    ) -> list[PolicyRule]:
        del project_id  # the fixture corpus is not project-scoped

        if policy_area == TIMEOUT_TRIGGER:
            await asyncio.sleep(_TIMEOUT_SLEEP_SECONDS)
            return []
        if policy_area == ERROR_TRIGGER:
            raise ToolExecutionError(
                "simulated provider error from lookup_policy_rules", retryable=True
            )

        matches = [
            rule
            for rule in corpus.policy_rules()
            if rule.policy_area == policy_area
            and corpus.document_access_group(rule.policy_document_id) in access_groups
        ]
        if severity_at_least is not None:
            matches = [
                rule
                for rule in matches
                if corpus.severity_at_least(rule.severity, severity_at_least)
            ]
        matches.sort(key=lambda rule: rule.rule_id)
        return matches


__all__ = ["FixturePolicyRuleStore", "PolicyRuleStore", "PostgresPolicyRuleStore"]
