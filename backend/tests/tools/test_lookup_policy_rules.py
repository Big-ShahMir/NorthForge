from __future__ import annotations

from northforge.tools.invoke import invoke_tool
from northforge.tools.registry import ToolRegistry
from tests.tools.conftest import PROCUREMENT, PROCUREMENT_AND_LEGAL, make_context


async def test_lookup_policy_rules_success(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry, "lookup_policy_rules", {"policy_area": "renewal"}, context
    )

    rule_ids = [rule["rule_id"] for rule in invocation.output["rules"]]
    assert rule_ids == ["pr_renewal_notice", "pr_renewal_term_cap"]


async def test_lookup_policy_rules_filters_by_severity(registry: ToolRegistry) -> None:
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry,
        "lookup_policy_rules",
        {"policy_area": "renewal", "severity_at_least": "high"},
        context,
    )

    assert invocation.output["rules"] == []


async def test_lookup_policy_rules_access_filter_hides_restricted_area(
    registry: ToolRegistry,
) -> None:
    # Liability rules cite the legal_restricted policy document.
    context = make_context(access_groups=PROCUREMENT)

    invocation = await invoke_tool(
        registry, "lookup_policy_rules", {"policy_area": "liability"}, context
    )

    assert invocation.output["rules"] == []


async def test_lookup_policy_rules_reveals_restricted_area_with_access(
    registry: ToolRegistry,
) -> None:
    context = make_context(access_groups=PROCUREMENT_AND_LEGAL)

    invocation = await invoke_tool(
        registry, "lookup_policy_rules", {"policy_area": "liability"}, context
    )

    rule_ids = [rule["rule_id"] for rule in invocation.output["rules"]]
    assert rule_ids == ["pr_liability_cap_floor", "pr_liability_carveouts"]
