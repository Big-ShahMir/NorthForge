"""``lookup_policy_rules``: fetch policy rules for a policy area from the policy library.

A rule is visible only when the policy document it cites is in one of the
caller's ``access_groups``, mirroring the access filtering ``search_documents``
and ``get_document_chunk`` apply to chunks -- a caller without the
``legal_restricted`` group cannot discover legal policy rules through this
tool either.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import PolicyRule, Severity
from northforge.tools.context import ToolContext
from northforge.tools.errors import ToolExecutionError
from northforge.tools.fixtures.corpus import (
    ERROR_TRIGGER,
    TIMEOUT_TRIGGER,
    document_access_group,
    policy_rules,
    severity_at_least,
)
from northforge.tools.spec import ToolSpec

_TIMEOUT_SLEEP_SECONDS = 3600.0


class LookupPolicyRulesInput(BaseModel):
    """Arguments for ``lookup_policy_rules``."""

    model_config = ConfigDict(extra="forbid")

    policy_area: str = Field(min_length=1, max_length=64)
    severity_at_least: Severity | None = None


class LookupPolicyRulesOutput(BaseModel):
    """Result of ``lookup_policy_rules``: matching rules, ordered by rule id."""

    model_config = ConfigDict(extra="forbid")

    rules: list[PolicyRule] = Field(default_factory=list)


LOOKUP_POLICY_RULES_SPEC = ToolSpec(
    name="lookup_policy_rules",
    description=(
        "Look up company policy rules for a policy area (for example "
        "'renewal', 'liability', 'termination', or 'data_protection'), "
        "optionally filtered to a minimum severity."
    ),
    input_model=LookupPolicyRulesInput,
    output_model=LookupPolicyRulesOutput,
    side_effect_class="read_only",
    access_scope="policy_library",
    kind="lookup",
)


async def lookup_policy_rules(args: BaseModel, context: ToolContext) -> dict[str, Any]:
    assert isinstance(args, LookupPolicyRulesInput)

    if args.policy_area == TIMEOUT_TRIGGER:
        await asyncio.sleep(_TIMEOUT_SLEEP_SECONDS)
        return {}

    if args.policy_area == ERROR_TRIGGER:
        raise ToolExecutionError(
            "simulated provider error from lookup_policy_rules", retryable=True
        )

    matches = [
        rule
        for rule in policy_rules()
        if rule.policy_area == args.policy_area
        and document_access_group(rule.policy_document_id) in context.access_groups
    ]
    if args.severity_at_least is not None:
        matches = [
            rule for rule in matches if severity_at_least(rule.severity, args.severity_at_least)
        ]
    matches.sort(key=lambda rule: rule.rule_id)

    output = LookupPolicyRulesOutput(rules=matches)
    return output.model_dump(mode="json")
