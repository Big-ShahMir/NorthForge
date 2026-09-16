"""``lookup_policy_rules``: fetch policy rules for a policy area from the policy library.

A rule is visible only when the policy document it cites is in one of the
caller's ``access_groups`` -- enforced entirely inside ``context.rule_store``
(``PostgresPolicyRuleStore`` in production, ``FixturePolicyRuleStore`` in
tests without a database), mirroring the access filtering
``search_documents`` and ``get_document_chunk`` apply to chunks. The
``__timeout__``/``__error__`` failure triggers used by ``tests/tools`` live
in ``FixturePolicyRuleStore``, not here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from northforge.schemas.evidence import PolicyRule, Severity
from northforge.tools.context import ToolContext
from northforge.tools.spec import ToolSpec


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

    rules = await context.rule_store.rules_for(
        context.project_id, args.policy_area, args.severity_at_least, context.access_groups
    )

    output = LookupPolicyRulesOutput(rules=rules)
    return output.model_dump(mode="json")
