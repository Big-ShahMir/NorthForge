"""Shared workflow definition fixtures for schema and validation tests.

``COMPLETE_DEFINITION`` is a full contract-review workflow -- retrieve,
extract, compare against policy, draft, human review, validate, finish --
with correctly wired references and approval points. It parses cleanly and
produces zero semantic errors and zero semantic warnings, whether or not a
real tool registry is supplied (it declares exactly the three real tool
names from ADR-022, so it is also usable by Stage B's integration tests).
"""

from __future__ import annotations

from typing import Any

#: The real tool names from ADR-022 (kept here, not imported, since schema
#: code must not depend on ``northforge.tools``).
REAL_TOOL_NAMES = ["search_documents", "get_document_chunk", "lookup_policy_rules"]

COMPLETE_DEFINITION: dict[str, Any] = {
    "schema_version": 1,
    "name": "Contract review",
    "description": "Review a vendor contract against procurement policy.",
    "user_request": "Review this contract for compliance with our procurement policy.",
    "inputs": {
        "contract_id": {
            "type": "document_id",
            "description": "The contract document to review.",
            "required": True,
        },
    },
    "steps": [
        {
            "id": "retrieve",
            "type": "retrieve_documents",
            "label": "Retrieve documents",
            "query": "acme master service agreement renewal terms",
            "document_types": ["contract"],
            "limit": 5,
            "min_results": 1,
        },
        {
            "id": "extract",
            "type": "extract_fields",
            "label": "Extract fields",
            "evidence": "$step.retrieve.chunks",
            "fields": [
                {
                    "name": "renewal_notice_days",
                    "description": "Renewal notice period in days.",
                    "type": "number",
                },
                {
                    "name": "auto_renew",
                    "description": "Whether the contract auto-renews.",
                    "type": "boolean",
                },
            ],
        },
        {
            "id": "compare",
            "type": "compare_policy",
            "label": "Compare against policy",
            "fields": "$step.extract.fields",
            "policy_area": "renewal",
            "rules": [
                {
                    "rule_id": "renewal_notice_min_30",
                    "field": "renewal_notice_days",
                    "operator": "gte",
                    "value": 30,
                    "severity": "high",
                    "description": "Renewal notice must be at least 30 days.",
                }
            ],
        },
        {
            "id": "draft",
            "type": "draft_summary",
            "label": "Draft summary",
            "sources": ["$step.compare.results", "$step.extract.fields"],
        },
        {
            "id": "review",
            "type": "human_review",
            "label": "Human review",
            "requires_approval": True,
            "show": ["$step.draft.summary_markdown"],
        },
        {
            "id": "validate",
            "type": "validate_output",
            "label": "Validate output",
            "target": "$step.draft.summary_markdown",
        },
        {
            "id": "finish",
            "type": "finish",
            "label": "Finish",
            "result": {
                "summary": "$step.draft.summary_markdown",
                "policy_results": "$step.compare.results",
            },
        },
    ],
    "edges": [
        {"source": "retrieve", "target": "extract"},
        {"source": "extract", "target": "compare"},
        {"source": "compare", "target": "draft"},
        {"source": "draft", "target": "review"},
        {"source": "review", "target": "validate"},
        {"source": "validate", "target": "finish"},
    ],
    "tools": list(REAL_TOOL_NAMES),
    "approval_points": ["review"],
    "policies": {},
}
