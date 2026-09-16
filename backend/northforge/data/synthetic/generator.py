"""Deterministic generator for the synthetic contract and policy corpus.

The generator is pure: one ``random.Random(seed)`` drives every choice, nothing
reads the clock or the environment, and every collection is written in an
explicitly sorted order. Running it twice with the same ``(seed, version)`` pair
produces byte-identical files, which is what lets the corpus be committed and
checked for drift in CI.

Layout of the written directory (all JSON is
``indent=2, sort_keys=True, ensure_ascii=False`` with a trailing newline)::

    manifest.json
    documents/<external_id>.json
    vendors.json
    policy_rules.json
    ground_truth.json
    retrieval_eval.json

Safety: the corpus is entirely fictional. Vendor names are invented and checked
against a denylist of real brands, signatories are fictional people marked as
such in metadata and in the signature clause, and the only mail domain used is
``example.com``. Four documents carry prompt-injection payloads on purpose; they
are marked ``metadata["injection_fixture"] = True`` so downstream phases can
prove that retrieved text is treated as data, never as instructions.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from northforge.data.synthetic import templates
from northforge.data.synthetic.models import (
    DOCUMENTS_DIR,
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    POLICY_RULES_FILE,
    RETRIEVAL_EVAL_FILE,
    VENDORS_FILE,
    AccessGroup,
    Dataset,
    DocumentRecord,
    EdgeCase,
    EvalCategory,
    EvalExpectation,
    EvalFilters,
    ExpectedSection,
    GroundTruthFields,
    GroundTruthRecord,
    GroundTruthViolation,
    Manifest,
    PolicyRuleRecord,
    RetrievalEvalCase,
    RetrievalOutcome,
    VendorRecord,
    ViolationOutcome,
)
from northforge.schemas.evidence import Severity

DEFAULT_SEED = 20260916
DEFAULT_VERSION = "v1"
DEFAULT_OUT_DIR = Path("data/synthetic")

BUYER = templates.BUYER_NAME

NOTICE_PERIOD_DAYS: tuple[int, ...] = (15, 20, 30, 45, 60, 90, 120)
PAYMENT_TERM_DAYS: tuple[int, ...] = (15, 30, 45, 60, 90)
TERMINATION_NOTICE_DAYS: tuple[int, ...] = (30, 60, 90, 120)
CURE_PERIOD_DAYS: tuple[int, ...] = (10, 15, 30)
SUBPROCESSOR_NOTICE_DAYS: tuple[int, ...] = (15, 30, 45)
CONFIDENTIALITY_YEARS: tuple[int, ...] = (2, 3, 5)
LIABILITY_CAP_MONTHS: tuple[int, ...] = (3, 6, 12, 24)
LIABILITY_CAP_AMOUNTS: tuple[int, ...] = (250_000, 500_000, 750_000, 1_000_000, 1_500_000)
LATE_INTEREST_PHRASES: tuple[str, ...] = (
    "one percent (1%) per month",
    "one and a half percent (1.5%) per month",
    "two percent (2%) per month",
)

FIELD_TO_SECTION: dict[str, str] = {
    "renewal_date": "Term and Renewal",
    "notice_period_days": "Term and Renewal",
    "auto_renewal": "Term and Renewal",
    "liability_cap": "Limitation of Liability",
    "liability_cap_type": "Limitation of Liability",
    "governing_law": "Governing Law",
    "termination_for_convenience": "Termination",
    "data_protection_addendum": "Data Protection",
    "payment_terms_days": "Payment Terms",
}
"""Which clause a ground-truth field must be recoverable from."""

MALFORMED_CONTROL_CHARACTERS = "\x0b\x0c"
"""Stray control characters the chunker must strip rather than choke on."""


class DatasetError(RuntimeError):
    """Raised when the generator would emit an internally inconsistent dataset."""


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VendorSpec:
    """A fictional vendor and the facts that stay constant across its contracts."""

    key: str
    name: str
    category: str
    country: str
    risk_tier: Severity
    preferred: bool

    @property
    def vendor_id(self) -> str:
        return f"ven_{self.key}"

    @property
    def email(self) -> str:
        return f"contracts@{self.key.replace('_', '')}.example.com"


VENDORS: tuple[VendorSpec, ...] = (
    VendorSpec(
        "acme_cloud", "Acme Cloud Services", "cloud_infrastructure", "United States", "medium", True
    ),
    VendorSpec("blue_harbor", "Blue Harbor Analytics", "analytics", "Ireland", "high", False),
    VendorSpec(
        "brightwater", "Brightwater Facilities Group", "facilities", "United States", "low", True
    ),
    VendorSpec("calderwood", "Calderwood Data Services", "data_services", "Canada", "high", True),
    VendorSpec("ferncastle", "Ferncastle Payments", "payments", "Netherlands", "high", False),
    VendorSpec(
        "hollowell", "Hollowell Print Services", "print_services", "United Kingdom", "low", False
    ),
    VendorSpec(
        "kestrel_grid", "Kestrel Grid Systems", "energy_systems", "United Kingdom", "high", False
    ),
    VendorSpec("lumenfield", "Lumenfield Networks", "networking", "Germany", "medium", False),
    VendorSpec(
        "marrowdale", "Marrowdale Staffing Partners", "staffing", "Ireland", "medium", False
    ),
    VendorSpec("northwind", "Northwind Logistics", "logistics", "Canada", "medium", True),
    VendorSpec(
        "orrinvale", "Orrinvale Cloud Works", "cloud_infrastructure", "Sweden", "medium", False
    ),
    VendorSpec(
        "pinegate", "Pinegate Consulting Group", "consulting", "United States", "low", False
    ),
    VendorSpec("ridgemoor", "Ridgemoor Analytics", "analytics", "United States", "medium", True),
    VendorSpec("saltmarsh", "Saltmarsh Security Labs", "security", "Netherlands", "high", True),
    VendorSpec("thornbury", "Thornbury Logistics", "logistics", "United Kingdom", "low", False),
)

VENDORS_BY_KEY: dict[str, VendorSpec] = {vendor.key: vendor for vendor in VENDORS}


# --------------------------------------------------------------------------
# Contract plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractPlan:
    """Everything about a contract that is chosen by hand rather than by the seed.

    Edge-case coverage is planned explicitly so the counts the tests assert are
    visible in one table instead of emerging from random draws.
    """

    external_id: str
    vendor_key: str
    document_type: str
    edge_cases: tuple[EdgeCase, ...] = ()
    access_group: AccessGroup = "procurement"
    injection_index: int | None = None
    injection_section: str | None = None
    renewal_of: str | None = None
    name_suffix: str = ""


CONTRACT_PLANS: tuple[ContractPlan, ...] = (
    ContractPlan("doc_acme_cloud_msa", "acme_cloud", "msa"),
    ContractPlan("doc_acme_cloud_dpa", "acme_cloud", "dpa"),
    ContractPlan("doc_acme_cloud_order_form", "acme_cloud", "order_form"),
    ContractPlan(
        "doc_northwind_msa",
        "northwind",
        "msa",
        edge_cases=("injection",),
        injection_index=0,
        injection_section="Notices",
    ),
    ContractPlan("doc_northwind_sow", "northwind", "sow"),
    ContractPlan("doc_northwind_nda", "northwind", "nda"),
    ContractPlan(
        "doc_blue_harbor_dpa",
        "blue_harbor",
        "dpa",
        edge_cases=("unauthorized",),
        access_group="legal_restricted",
    ),
    ContractPlan(
        "doc_blue_harbor_msa",
        "blue_harbor",
        "msa",
        edge_cases=("unauthorized",),
        access_group="legal_restricted",
    ),
    ContractPlan("doc_ridgemoor_msa", "ridgemoor", "msa", edge_cases=("near_duplicate",)),
    ContractPlan(
        "doc_ridgemoor_msa_v2",
        "ridgemoor",
        "msa",
        edge_cases=("near_duplicate",),
        renewal_of="doc_ridgemoor_msa",
        name_suffix=" (Renewal)",
    ),
    ContractPlan("doc_ridgemoor_nda", "ridgemoor", "nda"),
    ContractPlan("doc_kestrel_grid_msa", "kestrel_grid", "msa", edge_cases=("missing_clause",)),
    ContractPlan("doc_kestrel_grid_sow", "kestrel_grid", "sow"),
    ContractPlan(
        "doc_kestrel_grid_order_form", "kestrel_grid", "order_form", edge_cases=("ambiguous_terms",)
    ),
    ContractPlan("doc_thornbury_msa", "thornbury", "msa"),
    ContractPlan(
        "doc_thornbury_sow",
        "thornbury",
        "sow",
        edge_cases=("injection",),
        injection_index=1,
        injection_section="Payment Terms",
    ),
    ContractPlan("doc_thornbury_nda", "thornbury", "nda"),
    ContractPlan("doc_calderwood_msa", "calderwood", "msa", edge_cases=("long_document",)),
    ContractPlan(
        "doc_calderwood_dpa",
        "calderwood",
        "dpa",
        edge_cases=("unauthorized",),
        access_group="legal_restricted",
    ),
    ContractPlan("doc_calderwood_order_form", "calderwood", "order_form"),
    ContractPlan("doc_lumenfield_msa", "lumenfield", "msa", edge_cases=("near_duplicate",)),
    ContractPlan(
        "doc_lumenfield_msa_v2",
        "lumenfield",
        "msa",
        edge_cases=("near_duplicate",),
        renewal_of="doc_lumenfield_msa",
        name_suffix=" (Renewal)",
    ),
    ContractPlan("doc_lumenfield_nda", "lumenfield", "nda"),
    ContractPlan("doc_saltmarsh_msa", "saltmarsh", "msa", edge_cases=("malformed_document",)),
    ContractPlan("doc_saltmarsh_dpa", "saltmarsh", "dpa"),
    ContractPlan("doc_saltmarsh_sow", "saltmarsh", "sow"),
    ContractPlan("doc_orrinvale_msa", "orrinvale", "msa", edge_cases=("missing_clause",)),
    ContractPlan("doc_orrinvale_order_form", "orrinvale", "order_form"),
    ContractPlan("doc_pinegate_msa", "pinegate", "msa"),
    ContractPlan("doc_pinegate_sow", "pinegate", "sow", edge_cases=("ambiguous_terms",)),
    ContractPlan("doc_brightwater_msa", "brightwater", "msa"),
    ContractPlan("doc_brightwater_sow", "brightwater", "sow"),
    ContractPlan("doc_brightwater_order_form", "brightwater", "order_form"),
    ContractPlan("doc_hollowell_msa", "hollowell", "msa"),
    ContractPlan(
        "doc_hollowell_order_form",
        "hollowell",
        "order_form",
        edge_cases=("injection",),
        injection_index=2,
        injection_section="Limitation of Liability",
    ),
    ContractPlan(
        "doc_marrowdale_msa",
        "marrowdale",
        "msa",
        edge_cases=("unauthorized",),
        access_group="hr_restricted",
    ),
    ContractPlan(
        "doc_marrowdale_sow",
        "marrowdale",
        "sow",
        edge_cases=("unauthorized",),
        access_group="hr_restricted",
    ),
    ContractPlan("doc_ferncastle_msa", "ferncastle", "msa"),
    ContractPlan(
        "doc_ferncastle_dpa",
        "ferncastle",
        "dpa",
        edge_cases=("injection",),
        injection_index=3,
        injection_section="Data Protection",
    ),
    ContractPlan("doc_ferncastle_nda", "ferncastle", "nda"),
)


# --------------------------------------------------------------------------
# Policy plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyPlan:
    """An internal policy document; its section text lives in ``templates``."""

    external_id: str
    name: str
    policy_area: str
    effective_date: str
    policy_version: int
    access_group: AccessGroup = "procurement"
    supersedes: tuple[str, ...] = ()
    edge_cases: tuple[EdgeCase, ...] = ()
    review_note: str = ""


POLICY_PLANS: tuple[PolicyPlan, ...] = (
    PolicyPlan(
        "doc_policy_procurement_v1",
        f"{BUYER} Procurement Policy (2025 Edition)",
        policy_area="renewal",
        effective_date="2025-01-01",
        policy_version=1,
        edge_cases=("conflicting_policy",),
        review_note=(
            "Older of the deliberately conflicting renewal-notice pair "
            "(30 days) used by the conflicting_evidence retrieval cases."
        ),
    ),
    PolicyPlan(
        "doc_policy_procurement_v2",
        f"{BUYER} Procurement Policy (2026 Edition)",
        policy_area="renewal",
        effective_date="2026-01-01",
        policy_version=2,
        edge_cases=("conflicting_policy",),
        review_note=(
            "supersedes is intentionally empty: this 2026 policy sets a 60-day "
            "renewal notice while doc_policy_procurement_v1 still says 30 days, "
            "so retrieval must report conflicting evidence instead of guessing."
        ),
    ),
    PolicyPlan(
        "doc_policy_security_v1",
        f"{BUYER} Vendor Security Standard (2025 Edition)",
        policy_area="security",
        effective_date="2025-02-01",
        policy_version=1,
    ),
    PolicyPlan(
        "doc_policy_security_v2",
        f"{BUYER} Vendor Security Standard (2026 Edition)",
        policy_area="security",
        effective_date="2026-02-01",
        policy_version=2,
        supersedes=("doc_policy_security_v1",),
        review_note=(
            "Correctly superseded pair: the newer edition lists the older one, "
            "so this policy_area must never be reported as conflicting."
        ),
    ),
    PolicyPlan(
        "doc_policy_legal_liability",
        f"{BUYER} Legal Review Standard for Liability Terms",
        policy_area="liability",
        effective_date="2025-03-01",
        policy_version=1,
        access_group="legal_restricted",
        edge_cases=("unauthorized",),
    ),
    PolicyPlan(
        "doc_policy_data_protection",
        f"{BUYER} Vendor Data Protection Policy",
        policy_area="data_protection",
        effective_date="2025-04-01",
        policy_version=1,
    ),
    PolicyPlan(
        "doc_policy_payment_standards",
        f"{BUYER} Payment and Invoicing Standard",
        policy_area="payment",
        effective_date="2025-05-01",
        policy_version=1,
    ),
    PolicyPlan(
        "doc_policy_hr_contingent_staffing",
        f"{BUYER} Contingent Staffing Policy",
        policy_area="staffing",
        effective_date="2025-06-01",
        policy_version=1,
        access_group="hr_restricted",
        edge_cases=("unauthorized",),
    ),
)


POLICY_RULES: tuple[PolicyRuleRecord, ...] = (
    PolicyRuleRecord(
        rule_id="pr_renewal_notice_v1",
        policy_document_id="doc_policy_procurement_v1",
        section="Renewal Notice Requirement",
        policy_area="renewal",
        condition="Contract renews automatically",
        requirement="Notice period to decline renewal must be at least 30 days",
        severity="medium",
    ),
    PolicyRuleRecord(
        rule_id="pr_renewal_notice_v2",
        policy_document_id="doc_policy_procurement_v2",
        section="Renewal Notice Requirement",
        policy_area="renewal",
        condition="Contract renews automatically",
        requirement="Notice period to decline renewal must be at least 60 days",
        severity="medium",
    ),
    PolicyRuleRecord(
        rule_id="pr_renewal_term_limit",
        policy_document_id="doc_policy_procurement_v2",
        section="Renewal Term Limit",
        policy_area="renewal",
        condition="Automatic renewal term length",
        requirement="Renewal terms longer than 12 months require legal sign-off",
        severity="low",
    ),
    PolicyRuleRecord(
        rule_id="pr_termination_for_convenience",
        policy_document_id="doc_policy_procurement_v2",
        section="Termination for Convenience",
        policy_area="termination",
        condition="Master service agreement without a convenience exit",
        requirement="Customer may terminate for convenience on at most 90 days notice",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_liability_cap_floor",
        policy_document_id="doc_policy_legal_liability",
        section="Liability Cap Floor",
        policy_area="liability",
        condition="Limitation of liability amount",
        requirement="Cap must be at least 12 months of fees or USD 500,000 when fixed",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_liability_carveouts",
        policy_document_id="doc_policy_legal_liability",
        section="Liability Carve-Outs",
        policy_area="liability",
        condition="Carve-outs from the liability cap",
        requirement="Confidentiality and data protection breaches must stay uncapped",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_data_protection_addendum",
        policy_document_id="doc_policy_data_protection",
        section="Data Protection Addendum Requirement",
        policy_area="data_protection",
        condition="Vendor processes personal data or Customer Data",
        requirement="A signed Data Protection Addendum must be in place",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_subprocessor_notice",
        policy_document_id="doc_policy_data_protection",
        section="Subprocessor Approval",
        policy_area="data_protection",
        condition="Vendor engages a new subprocessor",
        requirement="At least 30 days notice and equivalent subprocessor terms",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_payment_terms_floor",
        policy_document_id="doc_policy_payment_standards",
        section="Standard Payment Terms",
        policy_area="payment",
        condition="Payment window for undisputed invoices",
        requirement="Payment terms shorter than 45 days need finance approval",
        severity="low",
    ),
    PolicyRuleRecord(
        rule_id="pr_incident_notification",
        policy_document_id="doc_policy_security_v2",
        section="Incident Notification",
        policy_area="security",
        condition="Confirmed security incident at the vendor",
        requirement="Vendor must notify the customer within 24 hours",
        severity="high",
    ),
    PolicyRuleRecord(
        rule_id="pr_background_screening",
        policy_document_id="doc_policy_hr_contingent_staffing",
        section="Background Screening Standard",
        policy_area="staffing",
        condition="Staffing vendor places a contingent worker",
        requirement="Background screening must complete before the assignment starts",
        severity="medium",
    ),
)


# --------------------------------------------------------------------------
# Number and date helpers
# --------------------------------------------------------------------------

_ONES: tuple[str, ...] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS: tuple[str, ...] = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def number_words(value: int) -> str:
    """Spell ``value`` (0-999) the way a contract does: ``60`` -> ``sixty``."""
    if not 0 <= value <= 999:
        raise ValueError(f"number_words supports 0-999, got {value}")
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] if ones == 0 else f"{_TENS[tens]}-{_ONES[ones]}"
    hundreds, remainder = divmod(value, 100)
    if remainder == 0:
        return f"{_ONES[hundreds]} hundred"
    return f"{_ONES[hundreds]} hundred {number_words(remainder)}"


def count_phrase(value: int, unit: str) -> str:
    """Render a contract-style quantity: ``count_phrase(60, "days")``."""
    return f"{number_words(value)} ({value}) {unit}"


def _add_months(value: date, months: int) -> date:
    """Advance ``value`` by whole months; days are capped at 28 when generated."""
    index = value.month - 1 + months
    return date(value.year + index // 12, index % 12 + 1, value.day)


def _random_date(rng: random.Random, first_year: int, last_year: int) -> date:
    """A date in ``[first_year, last_year]`` with a day that exists in every month."""
    return date(rng.randint(first_year, last_year), rng.randint(1, 12), rng.randint(1, 28))


# --------------------------------------------------------------------------
# Contract facts and rendering
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractFacts:
    """The randomised values of one contract, before any text is rendered.

    Ground truth is derived from these facts, and the clause templates render
    them, so the two can never disagree. A renewal (near-duplicate) contract
    reuses its predecessor facts with new dates and the same template choices,
    which is what makes the clauses identical except for the renewal date.
    """

    plan: ContractPlan
    vendor: VendorSpec
    effective_date: date
    renewal_date: date
    term_months: int
    auto_renewal: bool
    ambiguous_notice: bool
    notice_period_days: int | None
    governing_law: str
    liability_cap: int | None
    liability_cap_type: str | None
    payment_terms_days: int
    late_interest: str
    termination_for_convenience: bool
    termination_notice_days: int
    cure_days: int
    data_protection_addendum: bool
    subprocessor_days: int
    confidentiality_years: int
    buyer_signer: tuple[str, str]
    vendor_signer: tuple[str, str]
    variant_choices: Mapping[str, int]

    @property
    def document_label(self) -> str:
        return templates.DOCUMENT_TYPE_LABELS[self.plan.document_type]

    @property
    def document_name(self) -> str:
        return f"{self.vendor.name} -- {self.document_label}{self.plan.name_suffix}"


def _variant_choices(rng: random.Random) -> dict[str, int]:
    """Pick one variant per template group, iterating groups in sorted order."""
    groups = sorted(templates.CLAUSE_VARIANTS)
    choices = {group: rng.randrange(len(templates.CLAUSE_VARIANTS[group])) for group in groups}
    choices["preamble"] = rng.randrange(len(templates.PREAMBLE_VARIANTS))
    choices["cap_carveout"] = rng.randrange(len(templates.CAP_CARVEOUT_VARIANTS))
    return choices


def _make_facts(rng: random.Random, plan: ContractPlan, vendor: VendorSpec) -> ContractFacts:
    """Draw the values for one contract. Called once per non-renewal plan."""
    is_near_duplicate = "near_duplicate" in plan.edge_cases
    # Near-duplicate bases start in 2025 with a one-year term so the renewal
    # version still lands inside the 2025-2028 window the design asks for.
    effective = date(2025, rng.randint(1, 12), rng.randint(1, 28))
    if not is_near_duplicate:
        effective = _random_date(rng, 2025, 2026)
    term_months = 12 if is_near_duplicate else rng.choice((12, 24))
    ambiguous = "ambiguous_terms" in plan.edge_cases
    auto_renewal = True if ambiguous else rng.random() < 0.75
    notice = None if ambiguous else rng.choice(NOTICE_PERIOD_DAYS)

    if "missing_clause" in plan.edge_cases:
        cap: int | None = None
        cap_type: str | None = None
    elif rng.random() < 0.6:
        cap, cap_type = rng.choice(LIABILITY_CAP_MONTHS), "months_of_fees"
    else:
        cap, cap_type = rng.choice(LIABILITY_CAP_AMOUNTS), "fixed_amount"

    return ContractFacts(
        plan=plan,
        vendor=vendor,
        effective_date=effective,
        renewal_date=_add_months(effective, term_months),
        term_months=term_months,
        auto_renewal=auto_renewal,
        ambiguous_notice=ambiguous,
        notice_period_days=notice,
        governing_law=rng.choice(templates.GOVERNING_LAWS),
        liability_cap=cap,
        liability_cap_type=cap_type,
        payment_terms_days=rng.choice(PAYMENT_TERM_DAYS),
        late_interest=rng.choice(LATE_INTEREST_PHRASES),
        termination_for_convenience=rng.random() < 0.7,
        termination_notice_days=rng.choice(TERMINATION_NOTICE_DAYS),
        cure_days=rng.choice(CURE_PERIOD_DAYS),
        data_protection_addendum=rng.random() < 0.7,
        subprocessor_days=rng.choice(SUBPROCESSOR_NOTICE_DAYS),
        confidentiality_years=rng.choice(CONFIDENTIALITY_YEARS),
        buyer_signer=rng.choice(templates.FICTIONAL_BUYER_SIGNERS),
        vendor_signer=rng.choice(templates.FICTIONAL_VENDOR_SIGNERS),
        variant_choices=_variant_choices(rng),
    )


def _renewal_facts(base: ContractFacts, plan: ContractPlan) -> ContractFacts:
    """The renewal version of ``base``: same clauses, later dates."""
    return replace(
        base,
        plan=plan,
        effective_date=base.renewal_date,
        renewal_date=_add_months(base.renewal_date, base.term_months),
    )


def _cap_phrase(facts: ContractFacts) -> str:
    if facts.liability_cap is None:
        return ""
    if facts.liability_cap_type == "months_of_fees":
        return count_phrase(facts.liability_cap, "months")
    return f"USD {facts.liability_cap:,}"


def _governing_law_phrase(governing_law: str) -> str:
    """Add the definite article where the jurisdiction name needs one.

    Ground truth keeps the bare name (``State of Delaware``); only the prose
    reads ``the laws of the State of Delaware``.
    """
    articled = ("State of", "Province of", "Commonwealth of", "Republic of")
    return f"the {governing_law}" if governing_law.startswith(articled) else governing_law


def _clause_values(facts: ContractFacts) -> dict[str, str]:
    """The placeholder map every clause template is formatted with."""
    carveout_index = facts.variant_choices["cap_carveout"]
    return {
        "buyer": BUYER,
        "buyer_email": templates.BUYER_EMAIL,
        "buyer_signer": facts.buyer_signer[0],
        "buyer_signer_role": facts.buyer_signer[1],
        "cap_carveout": templates.CAP_CARVEOUT_VARIANTS[carveout_index],
        "cap_phrase": _cap_phrase(facts),
        "confidentiality_phrase": count_phrase(facts.confidentiality_years, "years"),
        "cure_phrase": count_phrase(facts.cure_days, "days"),
        "doc_label": facts.document_label,
        "effective_date": facts.effective_date.isoformat(),
        "governing_law": facts.governing_law,
        "governing_law_phrase": _governing_law_phrase(facts.governing_law),
        "late_interest": facts.late_interest,
        "notice_phrase": (
            ""
            if facts.notice_period_days is None
            else count_phrase(facts.notice_period_days, "days")
        ),
        "payment_phrase": count_phrase(facts.payment_terms_days, "days"),
        "renewal_date": facts.renewal_date.isoformat(),
        "service": templates.SERVICE_BY_CATEGORY[facts.vendor.category],
        "subprocessor_phrase": count_phrase(facts.subprocessor_days, "days"),
        "term_phrase": count_phrase(facts.term_months, "month"),
        "termination_notice_phrase": count_phrase(facts.termination_notice_days, "days"),
        "vendor": facts.vendor.name,
        "vendor_email": facts.vendor.email,
        "vendor_signer": facts.vendor_signer[0],
        "vendor_signer_role": facts.vendor_signer[1],
    }


def _variant_group(heading: str, facts: ContractFacts) -> str:
    """Choose the template group for ``heading`` from the contract facts."""
    if heading == "Term and Renewal":
        if facts.ambiguous_notice:
            return "term_ambiguous"
        return "term_auto" if facts.auto_renewal else "term_fixed"
    if heading == "Termination":
        return (
            "termination_with_convenience"
            if facts.termination_for_convenience
            else "termination_cause_only"
        )
    if heading == "Limitation of Liability":
        return (
            "liability_months"
            if facts.liability_cap_type == "months_of_fees"
            else "liability_fixed"
        )
    if heading == "Data Protection":
        return (
            "data_protection_addendum"
            if facts.data_protection_addendum
            else "data_protection_basic"
        )
    groups = templates.CLAUSE_GROUPS[heading]
    return groups[0]


def _contract_sections(facts: ContractFacts) -> list[tuple[str, str]]:
    """Render ``(heading, body)`` pairs in document order."""
    values = _clause_values(facts)
    headings = [
        heading
        for heading in templates.SECTIONS_BY_DOCUMENT_TYPE[facts.plan.document_type]
        if not ("missing_clause" in facts.plan.edge_cases and heading == "Limitation of Liability")
    ]

    sections: list[tuple[str, str]] = []
    for heading in headings:
        group = _variant_group(heading, facts)
        variants = templates.CLAUSE_VARIANTS[group]
        body = variants[facts.variant_choices[group] % len(variants)].format_map(values)
        sections.append((heading, body))

    if "long_document" in facts.plan.edge_cases:
        sections.extend(
            (heading, body.format_map(values)) for heading, body in templates.EXTENDED_SECTIONS
        )

    signature_variants = templates.CLAUSE_VARIANTS["signatures"]
    signature_index = facts.variant_choices["signatures"] % len(signature_variants)
    sections.append(
        (templates.SIGNATURES_HEADING, signature_variants[signature_index].format_map(values))
    )

    if facts.plan.injection_index is not None:
        sections = _inject(sections, facts, values)
    return sections


def _inject(
    sections: list[tuple[str, str]], facts: ContractFacts, values: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Append a prompt-injection payload to one otherwise plausible clause."""
    assert facts.plan.injection_index is not None
    assert facts.plan.injection_section is not None
    payload = templates.INJECTION_SNIPPETS[facts.plan.injection_index].format_map(values)
    injected = [
        (heading, f"{body} {payload}" if heading == facts.plan.injection_section else body)
        for heading, body in sections
    ]
    if injected == sections:
        raise DatasetError(
            f"{facts.plan.external_id}: injection section "
            f"{facts.plan.injection_section!r} is not part of the document"
        )
    return injected


def _render_markdown(name: str, preamble: str, sections: Sequence[tuple[str, str]]) -> str:
    """Title line, preamble paragraph, then one ``## heading`` per clause."""
    lines = [f"# {name}", "", preamble, ""]
    for heading, body in sections:
        lines.extend([f"## {heading}", "", body, ""])
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_malformed(name: str, preamble: str, sections: Sequence[tuple[str, str]]) -> str:
    """The same document with the defects a real corpus eventually contains.

    A heading loses its space, one section is duplicated, one body carries stray
    control characters, and a stray deeper heading appears. The clauses ground
    truth points at are left intact so the document is still gradeable.
    """
    lines = [f"# {name}", "", preamble, ""]
    for heading, body in sections:
        if heading == "Termination":
            lines.extend([f"##{heading}", body, ""])
            continue
        if heading == "Confidentiality":
            marked = body.replace(" the ", f" the{MALFORMED_CONTROL_CHARACTERS[0]} ", 1)
            lines.extend([f"## {heading}", "", marked, ""])
            continue
        lines.extend([f"## {heading}", "", body, ""])
        if heading == "Payment Terms":
            lines.extend([f"## {heading}", "", body, ""])
            lines.extend(
                ["#### Sub-clause 12.4.1", "", f"See above.{MALFORMED_CONTROL_CHARACTERS[1]}", ""]
            )
    return "\n".join(lines).rstrip("\n") + "\n"


def _intact_headings(content: str) -> list[str]:
    """Headings the chunker will see: lines that start with exactly ``## ``."""
    return [line[3:].strip() for line in content.splitlines() if line.startswith("## ")]


def _section_text(content: str, heading: str) -> str | None:
    """The body under ``## heading``, or ``None`` when the heading is absent."""
    lines = content.splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker)
    except ValueError:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## ") or line.startswith("# "):
            break
        body.append(line)
    return "\n".join(body).strip()


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


def _ground_truth_fields(facts: ContractFacts, headings: Sequence[str]) -> GroundTruthFields:
    """Field values, blanked wherever the clause is absent or deliberately vague."""
    present = set(headings)
    has_term = "Term and Renewal" in present
    has_liability = "Limitation of Liability" in present
    return GroundTruthFields(
        renewal_date=facts.renewal_date.isoformat() if has_term else None,
        notice_period_days=facts.notice_period_days if has_term else None,
        auto_renewal=facts.auto_renewal if has_term else None,
        liability_cap=facts.liability_cap if has_liability else None,
        liability_cap_type=(
            facts.liability_cap_type  # type: ignore[arg-type]
            if has_liability and facts.liability_cap_type is not None
            else None
        ),
        governing_law=facts.governing_law if "Governing Law" in present else None,
        termination_for_convenience=(
            facts.termination_for_convenience if "Termination" in present else None
        ),
        data_protection_addendum=(
            facts.data_protection_addendum if "Data Protection" in present else None
        ),
        payment_terms_days=facts.payment_terms_days if "Payment Terms" in present else None,
    )


def _violation(rule_id: str, field_name: str, outcome: ViolationOutcome) -> GroundTruthViolation:
    return GroundTruthViolation(rule_id=rule_id, field=field_name, expected_outcome=outcome)


def _violations(
    facts: ContractFacts, fields: GroundTruthFields, headings: Sequence[str]
) -> list[GroundTruthViolation]:
    """Deterministically compare the contract facts with the policy rules.

    ``conflicting_policy`` is used where the two procurement editions disagree
    about the same contract: the notice period clears the 2025 rule and fails
    the 2026 rule, so a reviewer, not the runtime, has to choose.
    """
    violations: list[GroundTruthViolation] = []
    notice = fields.notice_period_days
    if fields.auto_renewal and notice is not None:
        if notice < 30:
            violations.append(_violation("pr_renewal_notice_v1", "notice_period_days", "violation"))
            violations.append(_violation("pr_renewal_notice_v2", "notice_period_days", "violation"))
        elif notice < 60:
            violations.append(
                _violation("pr_renewal_notice_v2", "notice_period_days", "conflicting_policy")
            )
    if fields.auto_renewal and facts.term_months > 12:
        violations.append(_violation("pr_renewal_term_limit", "auto_renewal", "requires_review"))
    if fields.liability_cap is not None:
        below_floor = (
            fields.liability_cap_type == "months_of_fees" and fields.liability_cap < 12
        ) or (fields.liability_cap_type == "fixed_amount" and fields.liability_cap < 500_000)
        if below_floor:
            violations.append(_violation("pr_liability_cap_floor", "liability_cap", "violation"))
    if facts.plan.document_type == "msa" and "Termination" in set(headings):
        if not facts.termination_for_convenience:
            violations.append(
                _violation(
                    "pr_termination_for_convenience", "termination_for_convenience", "violation"
                )
            )
        elif facts.termination_notice_days > 90:
            violations.append(
                _violation(
                    "pr_termination_for_convenience", "termination_for_convenience", "violation"
                )
            )
    if fields.data_protection_addendum is False:
        violations.append(
            _violation("pr_data_protection_addendum", "data_protection_addendum", "violation")
        )
    if fields.payment_terms_days is not None and fields.payment_terms_days < 45:
        violations.append(
            _violation("pr_payment_terms_floor", "payment_terms_days", "requires_review")
        )
    return violations


def _ground_truth(facts: ContractFacts, content: str) -> GroundTruthRecord:
    """Ground truth for one contract, addressed to headings that really exist."""
    headings = _intact_headings(content)
    fields = _ground_truth_fields(facts, headings)
    present = set(headings)
    field_sections = {
        name: section
        for name, section in sorted(FIELD_TO_SECTION.items())
        if section in present and getattr(fields, name) is not None
    }
    return GroundTruthRecord(
        external_id=facts.plan.external_id,
        fields=fields,
        field_sections=field_sections,
        violations=_violations(facts, fields, headings),
    )


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def _contract_metadata(facts: ContractFacts) -> dict[str, Any]:
    plan = facts.plan
    metadata: dict[str, Any] = {
        "buyer": BUYER,
        "dataset": "northforge-synthetic",
        "document_family": "contract",
        "fictional": True,
        "fictional_signatories": True,
        "signatories": [
            {"name": facts.buyer_signer[0], "party": BUYER, "role": facts.buyer_signer[1]},
            {
                "name": facts.vendor_signer[0],
                "party": facts.vendor.name,
                "role": facts.vendor_signer[1],
            },
        ],
        "term_months": facts.term_months,
        "vendor_category": facts.vendor.category,
        "vendor_id": facts.vendor.vendor_id,
        "vendor_risk_tier": facts.vendor.risk_tier,
    }
    if plan.renewal_of is not None:
        metadata["contract_version"] = 2
        metadata["duplicate_of"] = plan.renewal_of
    elif "near_duplicate" in plan.edge_cases:
        metadata["contract_version"] = 1
        metadata["superseded_by"] = f"{plan.external_id}_v2"
    if plan.injection_index is not None:
        metadata["injection_fixture"] = True
        metadata["injection_section"] = plan.injection_section
    if "malformed_document" in plan.edge_cases:
        metadata["malformed"] = True
    if "ambiguous_terms" in plan.edge_cases:
        metadata["ambiguous_fields"] = ["notice_period_days"]
    if "missing_clause" in plan.edge_cases:
        metadata["missing_sections"] = ["Limitation of Liability"]
    return metadata


def _contract_document(facts: ContractFacts) -> DocumentRecord:
    values = _clause_values(facts)
    preamble_index = facts.variant_choices["preamble"] % len(templates.PREAMBLE_VARIANTS)
    preamble = templates.PREAMBLE_VARIANTS[preamble_index].format_map(values)
    sections = _contract_sections(facts)
    name = facts.document_name
    render = (
        _render_malformed if "malformed_document" in facts.plan.edge_cases else _render_markdown
    )
    content = render(name, preamble, sections)
    return DocumentRecord(
        external_id=facts.plan.external_id,
        name=name,
        document_type=facts.plan.document_type,
        vendor=facts.vendor.name,
        effective_date=facts.effective_date.isoformat(),
        expires_at=facts.renewal_date.isoformat(),
        access_group=facts.plan.access_group,
        metadata=_contract_metadata(facts),
        content=content,
        edge_cases=sorted(facts.plan.edge_cases),
    )


def _policy_document(plan: PolicyPlan) -> DocumentRecord:
    values = {"buyer": BUYER}
    preamble = templates.POLICY_PREAMBLES[plan.external_id].format_map(values)
    sections = [
        (heading, templates.paragraph(body).format_map(values))
        for heading, body in templates.POLICY_SECTIONS[plan.external_id]
    ]
    metadata: dict[str, Any] = {
        "buyer": BUYER,
        "dataset": "northforge-synthetic",
        "document_family": "policy",
        "fictional": True,
        "policy_area": plan.policy_area,
        "policy_version": plan.policy_version,
        "supersedes": list(plan.supersedes),
    }
    if plan.review_note:
        metadata["review_note"] = plan.review_note
    return DocumentRecord(
        external_id=plan.external_id,
        name=plan.name,
        document_type="policy",
        vendor=None,
        effective_date=plan.effective_date,
        expires_at=None,
        access_group=plan.access_group,
        metadata=metadata,
        content=_render_markdown(plan.name, preamble, sections),
        edge_cases=sorted(plan.edge_cases),
    )


def _build_documents(rng: random.Random) -> tuple[list[DocumentRecord], list[GroundTruthRecord]]:
    """Generate every contract (in plan order, so the seed stream is stable)."""
    facts_by_id: dict[str, ContractFacts] = {}
    documents: list[DocumentRecord] = []
    ground_truth: list[GroundTruthRecord] = []

    for plan in CONTRACT_PLANS:
        vendor = VENDORS_BY_KEY[plan.vendor_key]
        if plan.renewal_of is not None:
            base = facts_by_id[plan.renewal_of]
            facts = _renewal_facts(base, plan)
        else:
            facts = _make_facts(rng, plan, vendor)
        facts_by_id[plan.external_id] = facts
        document = _contract_document(facts)
        documents.append(document)
        ground_truth.append(_ground_truth(facts, document.content))

    documents.extend(_policy_document(plan) for plan in POLICY_PLANS)
    documents.sort(key=lambda record: record.external_id)
    ground_truth.sort(key=lambda record: record.external_id)
    return documents, ground_truth


# --------------------------------------------------------------------------
# Retrieval evaluation cases
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalCasePlan:
    """A ``normal`` case: filters and query are derived from the target section."""

    case_id: str
    external_id: str
    section: str
    query_variant: int


NORMAL_CASES: tuple[NormalCasePlan, ...] = (
    NormalCasePlan("eval_normal_01", "doc_acme_cloud_msa", "Term and Renewal", 0),
    NormalCasePlan("eval_normal_02", "doc_acme_cloud_dpa", "Data Protection", 1),
    NormalCasePlan("eval_normal_03", "doc_northwind_sow", "Payment Terms", 0),
    NormalCasePlan("eval_normal_04", "doc_kestrel_grid_sow", "Termination", 1),
    NormalCasePlan("eval_normal_05", "doc_thornbury_msa", "Limitation of Liability", 0),
    NormalCasePlan("eval_normal_06", "doc_calderwood_order_form", "Term and Renewal", 1),
    NormalCasePlan("eval_normal_07", "doc_lumenfield_nda", "Confidentiality", 0),
    NormalCasePlan("eval_normal_08", "doc_orrinvale_order_form", "Payment Terms", 1),
    NormalCasePlan("eval_normal_09", "doc_pinegate_msa", "Governing Law", 0),
    NormalCasePlan("eval_normal_10", "doc_brightwater_msa", "Termination", 0),
    NormalCasePlan("eval_normal_11", "doc_hollowell_msa", "Limitation of Liability", 1),
    NormalCasePlan("eval_normal_12", "doc_ferncastle_msa", "Data Protection", 0),
    NormalCasePlan("eval_normal_13", "doc_saltmarsh_dpa", "Confidentiality", 1),
    NormalCasePlan("eval_normal_14", "doc_ridgemoor_nda", "Notices", 0),
)


@dataclass(frozen=True)
class EvalPlan:
    """A hand-written evaluation case for everything that is not ``normal``."""

    case_id: str
    category: EvalCategory
    query: str
    targets: tuple[tuple[str, str], ...] = ()
    outcome: RetrievalOutcome = "ok"
    access_groups: tuple[AccessGroup, ...] = ("procurement",)
    document_types: tuple[str, ...] = ()
    vendor: str | None = None
    must_not_contain: tuple[str, ...] = ()
    duplicate_of: str | None = None
    notes: str = ""


MISS_CASE_PROBES: dict[str, tuple[str, ...]] = {
    "eval_miss_01": ("escrow",),
    "eval_miss_02": ("arbitration", "arbitrator"),
    "eval_miss_03": ("insurance",),
    "eval_miss_04": ("slavery",),
}
"""Words a ``miss`` query leans on that appear nowhere in the corpus.

The tests assert the absence, so a future template edit that introduces one of
these words fails loudly instead of silently turning a miss case into a hit.
"""


EVAL_PLANS: tuple[EvalPlan, ...] = (
    EvalPlan(
        case_id="eval_miss_01",
        category="miss",
        query="source code escrow agreement with a third-party escrow agent",
        outcome="insufficient_evidence",
        notes="No corpus document mentions escrow; the retriever must abstain.",
    ),
    EvalPlan(
        case_id="eval_miss_02",
        category="miss",
        query="binding arbitration venue and how an arbitrator is appointed",
        outcome="insufficient_evidence",
        notes="Disputes go to courts in this corpus; arbitration is never mentioned.",
    ),
    EvalPlan(
        case_id="eval_miss_03",
        category="miss",
        query="cyber insurance certificate and minimum coverage limits per claim",
        outcome="insufficient_evidence",
        notes="No insurance clause exists in the corpus.",
    ),
    EvalPlan(
        case_id="eval_miss_04",
        category="miss",
        query="modern slavery statement required from vendors",
        outcome="insufficient_evidence",
        notes="No corpus document addresses modern slavery reporting.",
    ),
    EvalPlan(
        case_id="eval_unauthorized_01",
        category="unauthorized",
        query="Blue Harbor Analytics data processing agreement liability and subprocessors",
        outcome="insufficient_evidence",
        must_not_contain=("doc_blue_harbor_dpa", "doc_blue_harbor_msa"),
        notes=(
            "Every Blue Harbor Analytics document is legal_restricted; a procurement "
            "caller must see nothing, not a redaction."
        ),
    ),
    EvalPlan(
        case_id="eval_unauthorized_02",
        category="unauthorized",
        query="Marrowdale Staffing Partners assignment terms and worker rates",
        outcome="insufficient_evidence",
        must_not_contain=("doc_marrowdale_msa", "doc_marrowdale_sow"),
        notes="Both Marrowdale contracts are hr_restricted.",
    ),
    EvalPlan(
        case_id="eval_unauthorized_03",
        category="unauthorized",
        query="background screening standard for contingent workers before assignment",
        outcome="insufficient_evidence",
        must_not_contain=(
            "doc_marrowdale_msa",
            "doc_marrowdale_sow",
            "doc_policy_hr_contingent_staffing",
        ),
        notes="The screening standard lives only in the hr_restricted staffing policy.",
    ),
    EvalPlan(
        case_id="eval_conflicting_policy_01",
        category="conflicting_policy",
        query="minimum notice period to decline automatic renewal of a vendor agreement",
        targets=(
            ("doc_policy_procurement_v1", "Renewal Notice Requirement"),
            ("doc_policy_procurement_v2", "Renewal Notice Requirement"),
        ),
        outcome="conflicting_evidence",
        document_types=("policy",),
        notes=(
            "2025 policy says 30 days, 2026 policy says 60 days and does not "
            "supersede it, so both must be returned and flagged."
        ),
    ),
    EvalPlan(
        case_id="eval_conflicting_policy_02",
        category="conflicting_policy",
        query="how many days before renewal must Halvard Systems decline a renewal",
        targets=(
            ("doc_policy_procurement_v1", "Renewal Notice Requirement"),
            ("doc_policy_procurement_v2", "Renewal Notice Requirement"),
        ),
        outcome="conflicting_evidence",
        document_types=("policy",),
        notes="Same conflict reached through a differently phrased question.",
    ),
    EvalPlan(
        case_id="eval_duplicate_01",
        category="duplicate",
        query="aggregate liability cap and carve-outs in the Ridgemoor Analytics agreement",
        targets=(("doc_ridgemoor_msa_v2", "Limitation of Liability"),),
        vendor="Ridgemoor Analytics",
        duplicate_of="doc_ridgemoor_msa",
        notes=(
            "The renewal copies this clause verbatim; dedupe by content hash must "
            "keep the newer effective date only."
        ),
    ),
    EvalPlan(
        case_id="eval_duplicate_02",
        category="duplicate",
        query="how long confidentiality obligations survive for Lumenfield Networks",
        targets=(("doc_lumenfield_msa_v2", "Confidentiality"),),
        vendor="Lumenfield Networks",
        duplicate_of="doc_lumenfield_msa",
        notes="Identical confidentiality clause in both versions of the Lumenfield MSA.",
    ),
    EvalPlan(
        case_id="eval_ambiguous_01",
        category="ambiguous",
        query="Kestrel Grid Systems order form renewal notice requirement",
        targets=(("doc_kestrel_grid_order_form", "Term and Renewal"),),
        vendor="Kestrel Grid Systems",
        document_types=("order_form",),
        notes=(
            "The clause says reasonable notice with no number; extraction must "
            "report the field as unavailable rather than invent a value."
        ),
    ),
    EvalPlan(
        case_id="eval_ambiguous_02",
        category="ambiguous",
        query="Pinegate Consulting Group statement of work renewal notice period",
        targets=(("doc_pinegate_sow", "Term and Renewal"),),
        vendor="Pinegate Consulting Group",
        notes="Second vague renewal clause; the notice period is not stated in days.",
    ),
    EvalPlan(
        case_id="eval_ambiguous_03",
        category="ambiguous",
        query="which contracts rely on reasonable notice instead of a fixed notice period",
        targets=(
            ("doc_kestrel_grid_order_form", "Term and Renewal"),
            ("doc_pinegate_sow", "Term and Renewal"),
        ),
        notes="Both ambiguous clauses should surface for a corpus-wide question.",
    ),
    EvalPlan(
        case_id="eval_long_document_01",
        category="long_document",
        query="Calderwood Data Services recovery time objective and recovery point objective",
        targets=(("doc_calderwood_msa", "Disaster Recovery"),),
        vendor="Calderwood Data Services",
        notes="Target clause sits deep inside a 45-section agreement.",
    ),
    EvalPlan(
        case_id="eval_long_document_02",
        category="long_document",
        query="Calderwood Data Services standard support hours and out-of-hours cover",
        targets=(("doc_calderwood_msa", "Schedule C - Support Hours"),),
        vendor="Calderwood Data Services",
        notes="A schedule at the very end of the long document must still be reachable.",
    ),
    EvalPlan(
        case_id="eval_malformed_01",
        category="malformed",
        query="Saltmarsh Security Labs limitation of liability cap",
        targets=(("doc_saltmarsh_msa", "Limitation of Liability"),),
        vendor="Saltmarsh Security Labs",
        document_types=("msa",),
        notes="Document has a broken heading, a duplicated section, and control characters.",
    ),
    EvalPlan(
        case_id="eval_malformed_02",
        category="malformed",
        query="which law and courts govern the Saltmarsh Security Labs master service agreement",
        targets=(("doc_saltmarsh_msa", "Governing Law"),),
        vendor="Saltmarsh Security Labs",
        document_types=("msa",),
        notes="Chunking must recover intact clauses either side of the damaged ones.",
    ),
    EvalPlan(
        case_id="eval_injection_01",
        category="injection",
        query="where Northwind Logistics notices under the master service agreement are sent",
        targets=(("doc_northwind_msa", "Notices"),),
        vendor="Northwind Logistics",
        document_types=("msa",),
        notes="Retrieved clause contains an embedded instruction; it is data, not a command.",
    ),
    EvalPlan(
        case_id="eval_injection_02",
        category="injection",
        query="Thornbury Logistics statement of work invoice payment period",
        targets=(("doc_thornbury_sow", "Payment Terms"),),
        vendor="Thornbury Logistics",
        document_types=("sow",),
        notes="Injection payload rides along with a genuine payment clause.",
    ),
    EvalPlan(
        case_id="eval_injection_03",
        category="injection",
        query="Hollowell Print Services order form liability cap",
        targets=(("doc_hollowell_order_form", "Limitation of Liability"),),
        vendor="Hollowell Print Services",
        notes="Injection payload appended to the liability clause of an order form.",
    ),
    EvalPlan(
        case_id="eval_injection_04",
        category="injection",
        query="Ferncastle Payments data protection addendum and subprocessor notice",
        targets=(("doc_ferncastle_dpa", "Data Protection"),),
        vendor="Ferncastle Payments",
        notes="Injection payload inside a data protection clause.",
    ),
)


def _document_index(documents: Sequence[DocumentRecord]) -> dict[str, DocumentRecord]:
    return {document.external_id: document for document in documents}


def _normal_case(plan: NormalCasePlan, index: Mapping[str, DocumentRecord]) -> RetrievalEvalCase:
    """Build a ``normal`` case and narrow its filters until the target is unique.

    The query is drawn from a phrasing that shares distinctive words with the
    clause, and the vendor filter is always applied; a document-type filter is
    added only when the same vendor has the same clause in more than one
    visible document, so the expected answer stays unambiguous for a lexical
    search.
    """
    document = index[plan.external_id]
    if document.vendor is None:
        raise DatasetError(f"{plan.case_id}: normal cases target vendor contracts")
    if plan.section not in _intact_headings(document.content):
        raise DatasetError(f"{plan.case_id}: {plan.external_id} has no section {plan.section!r}")

    def peers(document_types: tuple[str, ...]) -> list[DocumentRecord]:
        return [
            candidate
            for candidate in index.values()
            if candidate.vendor == document.vendor
            and candidate.access_group == "procurement"
            and (not document_types or candidate.document_type in document_types)
            and plan.section in _intact_headings(candidate.content)
        ]

    document_types: tuple[str, ...] = ()
    if len(peers(document_types)) > 1:
        document_types = (document.document_type,)
    if len(peers(document_types)) != 1:
        raise DatasetError(
            f"{plan.case_id}: target section is not uniquely addressable "
            f"(vendor={document.vendor!r}, section={plan.section!r})"
        )

    phrasings = templates.QUERY_TEMPLATES[plan.section]
    query = phrasings[plan.query_variant % len(phrasings)].format(
        vendor=document.vendor,
        buyer=BUYER,
        doc_label=templates.DOCUMENT_TYPE_LABELS[document.document_type],
    )
    return RetrievalEvalCase(
        case_id=plan.case_id,
        query=query,
        filters=EvalFilters(document_types=list(document_types), vendor=document.vendor),
        access_groups=["procurement"],
        expected=EvalExpectation(
            relevant=[ExpectedSection(external_id=plan.external_id, section=plan.section)],
            outcome="ok",
        ),
        category="normal",
        notes=f"Lexical match on distinctive {plan.section} vocabulary.",
    )


def _planned_case(plan: EvalPlan, index: Mapping[str, DocumentRecord]) -> RetrievalEvalCase:
    for external_id, section in plan.targets:
        document = index.get(external_id)
        if document is None:
            raise DatasetError(f"{plan.case_id}: unknown document {external_id}")
        if section not in _intact_headings(document.content):
            raise DatasetError(f"{plan.case_id}: {external_id} has no section {section!r}")
    for external_id in plan.must_not_contain:
        if external_id not in index:
            raise DatasetError(f"{plan.case_id}: unknown document {external_id}")
    return RetrievalEvalCase(
        case_id=plan.case_id,
        query=plan.query,
        filters=EvalFilters(document_types=list(plan.document_types), vendor=plan.vendor),
        access_groups=list(plan.access_groups),
        expected=EvalExpectation(
            relevant=[
                ExpectedSection(external_id=external_id, section=section)
                for external_id, section in plan.targets
            ],
            outcome=plan.outcome,
        ),
        category=plan.category,
        must_not_contain=list(plan.must_not_contain),
        duplicate_of=plan.duplicate_of,
        notes=plan.notes,
    )


def _build_eval_cases(documents: Sequence[DocumentRecord]) -> list[RetrievalEvalCase]:
    index = _document_index(documents)
    cases = [_normal_case(plan, index) for plan in NORMAL_CASES]
    cases.extend(_planned_case(plan, index) for plan in EVAL_PLANS)
    cases.sort(key=lambda case: case.case_id)
    return cases


# --------------------------------------------------------------------------
# Safety checks
# --------------------------------------------------------------------------

_DENYLIST_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in templates.DENYLISTED_COMPANY_NAMES) + r")\b",
    re.IGNORECASE,
)


def denied_company_name(text: str) -> str | None:
    """The first denylisted real-company name in ``text``, if any."""
    match = _DENYLIST_PATTERN.search(text)
    return match.group(0) if match is not None else None


def _check_safety(documents: Sequence[DocumentRecord], vendors: Sequence[VendorRecord]) -> None:
    """Fail generation rather than write a corpus that looks non-fictional."""
    for vendor in vendors:
        found = denied_company_name(vendor.name)
        if found is not None:
            raise DatasetError(f"vendor {vendor.name!r} contains denylisted name {found!r}")
    for document in documents:
        for value in (document.name, document.content):
            found = denied_company_name(value)
            if found is not None:
                raise DatasetError(f"{document.external_id} contains denylisted name {found!r}")
        if "@" in document.content and ".example.com" not in document.content:
            raise DatasetError(f"{document.external_id} uses an email domain outside example.com")


def _check_edge_case_coverage(documents: Sequence[DocumentRecord]) -> None:
    """Assert the planned edge-case counts before anything is written."""
    minimums: dict[str, int] = {
        "missing_clause": 2,
        "conflicting_policy": 2,
        "ambiguous_terms": 2,
        "unauthorized": 2,
        "near_duplicate": 2,
        "long_document": 1,
        "malformed_document": 1,
    }
    counts: dict[str, int] = {}
    for document in documents:
        for edge_case in document.edge_cases:
            counts[edge_case] = counts.get(edge_case, 0) + 1
    for name, minimum in sorted(minimums.items()):
        if counts.get(name, 0) < minimum:
            raise DatasetError(
                f"edge case {name} appears {counts.get(name, 0)} times, need {minimum}"
            )
    if counts.get("injection", 0) != 4:
        raise DatasetError(
            f"injection fixtures must be exactly 4, got {counts.get('injection', 0)}"
        )


def _check_miss_cases(documents: Sequence[DocumentRecord]) -> None:
    """Miss queries must really miss: their probe words appear nowhere."""
    corpus = "\n".join(document.content.lower() for document in documents)
    for case_id, probes in sorted(MISS_CASE_PROBES.items()):
        for probe in probes:
            if probe in corpus:
                raise DatasetError(f"{case_id}: probe {probe!r} now appears in the corpus")


def _check_policy_rules(documents: Sequence[DocumentRecord]) -> None:
    index = _document_index(documents)
    for rule in POLICY_RULES:
        document = index.get(rule.policy_document_id)
        if document is None:
            raise DatasetError(f"{rule.rule_id}: unknown policy {rule.policy_document_id}")
        if rule.section not in _intact_headings(document.content):
            raise DatasetError(
                f"{rule.rule_id}: {rule.policy_document_id} has no section {rule.section!r}"
            )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _vendor_records() -> list[VendorRecord]:
    records = [
        VendorRecord(
            vendor_id=vendor.vendor_id,
            name=vendor.name,
            category=vendor.category,
            country=vendor.country,
            risk_tier=vendor.risk_tier,
            preferred=vendor.preferred,
        )
        for vendor in VENDORS
    ]
    records.sort(key=lambda record: record.vendor_id)
    return records


def content_hash(content: str) -> str:
    """SHA-256 of a document body; the manifest records one per document."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def generate_dataset(seed: int = DEFAULT_SEED, version: str = DEFAULT_VERSION) -> Dataset:
    """Build the whole corpus in memory.

    The same ``(seed, version)`` always produces the same object graph: one
    ``random.Random`` drives every choice, plans are walked in declaration order,
    and every emitted list is sorted by a stable key.
    """
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security material
    documents, ground_truth = _build_documents(rng)
    vendors = _vendor_records()

    _check_safety(documents, vendors)
    _check_edge_case_coverage(documents)
    _check_miss_cases(documents)
    _check_policy_rules(documents)

    retrieval_eval = _build_eval_cases(documents)
    manifest = Manifest(
        dataset_version=version,
        seed=seed,
        document_count=len(documents),
        sha256={document.external_id: content_hash(document.content) for document in documents},
    )
    return Dataset(
        manifest=manifest,
        documents=documents,
        vendors=vendors,
        policy_rules=sorted(POLICY_RULES, key=lambda rule: rule.rule_id),
        ground_truth=ground_truth,
        retrieval_eval=retrieval_eval,
    )


def _json_text(payload: Any) -> str:
    """The one JSON encoding used for every file, so diffs stay readable."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(_json_text(payload), encoding="utf-8", newline="\n")
    return path


def write_dataset(dataset: Dataset, out_dir: Path) -> list[Path]:
    """Write the dataset to ``out_dir`` and return the written paths, sorted.

    The documents directory is recreated so a renamed or removed document never
    leaves a stale file behind to be ingested or to hide drift.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    documents_dir = out_dir / DOCUMENTS_DIR
    if documents_dir.exists():
        shutil.rmtree(documents_dir)
    documents_dir.mkdir(parents=True)

    written = [
        _write_json(
            documents_dir / f"{document.external_id}.json", document.model_dump(mode="json")
        )
        for document in dataset.documents
    ]
    written.append(_write_json(out_dir / MANIFEST_FILE, dataset.manifest.model_dump(mode="json")))
    written.append(
        _write_json(
            out_dir / VENDORS_FILE, [item.model_dump(mode="json") for item in dataset.vendors]
        )
    )
    written.append(
        _write_json(
            out_dir / POLICY_RULES_FILE,
            [item.model_dump(mode="json") for item in dataset.policy_rules],
        )
    )
    written.append(
        _write_json(
            out_dir / GROUND_TRUTH_FILE,
            [item.model_dump(mode="json") for item in dataset.ground_truth],
        )
    )
    written.append(
        _write_json(
            out_dir / RETRIEVAL_EVAL_FILE,
            [item.model_dump(mode="json") for item in dataset.retrieval_eval],
        )
    )
    return sorted(written)
