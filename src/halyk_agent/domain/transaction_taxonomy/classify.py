"""Deterministic description→MetricCategory classification (precision > recall)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from halyk_agent.domain.covenants.ast import MetricCategory

# Strong exclusive rules. Multiple hits → CONFLICT (no silent priority).
_STRONG_RULES: tuple[tuple[str, MetricCategory, re.Pattern[str]], ...] = (
    (
        "SEVERANCE",
        MetricCategory.SEVERANCE_LIABILITY,
        re.compile(r"\bseverance\b", re.IGNORECASE),
    ),
    (
        "CAPITAL_TRANSFER_UNRESTRICTED",
        MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
        re.compile(
            r"capital\s+asset\s+transfer|"
            r"\btransfer(?:s|red)?\b.{0,60}\b(?:unrestricted\s+)?subsidiar|"
            r"\btransfer\s+of\b.{0,60}\bequipment\s+to\s+subsidiar",
            re.IGNORECASE,
        ),
    ),
    (
        "GROUP_CAPEX",
        MetricCategory.GROUP_CAPEX,
        re.compile(r"\bgroup\s+capex\b|\bconsolidated\s+capex\b", re.IGNORECASE),
    ),
    (
        "ONE_TIME_ADD_BACK",
        MetricCategory.ONE_TIME_ADD_BACKS,
        re.compile(r"\bone[-\s]?time\s+add[-\s]?back|\badd[-\s]?back\b", re.IGNORECASE),
    ),
    (
        "FINANCING_INFLOW",
        MetricCategory.FINANCING_INFLOWS,
        re.compile(
            r"\bdrawdown\b|\bfinancing\s+inflow\b|\bequity\s+injection\b|"
            r"\bcapital\s+contribution\b|\bloan\s+facility\s+draw",
            re.IGNORECASE,
        ),
    ),
    (
        "CAPEX",
        MetricCategory.CAPEX,
        re.compile(
            r"\bcapex\b|\bcapital\s+expenditure|\bequipment\s+purchase\b|"
            r"\bpurchase\s+of\b.{0,60}\bequipment\b|\bfixed\s+asset\b|"
            r"\bcapitalis(?:ed|ed)\b|\bhaul\s+truck\b|\bplant\s+machinery\b",
            re.IGNORECASE,
        ),
    ),
    (
        "INSURANCE",
        MetricCategory.INSURANCE_PREMIUMS,
        re.compile(r"\binsurance\b|\bpremium\b", re.IGNORECASE),
    ),
    (
        "INTEREST_EXPENSE",
        MetricCategory.INTEREST_EXPENSE,
        re.compile(
            r"\binterest\s+on\b|\brevolver\s+interest\b|\binterest\s+(?:charge|payment|expense|coupon)\b|"
            r"\binterest\s+on\s+bridge\b|\boverdraft\s+interest\b|\bcredit\s+facility\s+interest\b|"
            r"\bterm\s+loan\s+interest\b|\bquarterly\s+interest\b|\binterest\s+true-up\b",
            re.IGNORECASE,
        ),
    ),
    (
        "TAXES",
        MetricCategory.TAXES,
        re.compile(
            r"\bincome\s+tax\b|\bfranchise\s+tax\b|\bwithholding\s+tax\b|\bexcise\s+tax\b|"
            r"\bmineral\s+extraction\s+tax\b|\bprofit\s+tax\b|\bVAT\b|"
            r"\b(?:property|vehicle|municipal|emissions|environmental)\s+tax\b|"
            r"\bestimated\s+tax\b|\btax\s+(?:instalment|remittance|assessment|filing|audit|penalty|levy|payment)\b|"
            r"\bcustoms\s+duty\b.{0,20}\btax\b",
            re.IGNORECASE,
        ),
    ),
    (
        "LEASE",
        MetricCategory.LEASE_PAYMENTS,
        re.compile(r"\blease\b|\bleasing\b|\bleased\s+line\b", re.IGNORECASE),
    ),
    (
        "RENT",
        MetricCategory.RENT,
        re.compile(r"\brent\b|\brental\b", re.IGNORECASE),
    ),
    (
        "LABOR",
        MetricCategory.LABOR,
        re.compile(
            r"\bpayroll\b|\bsalary\b|\bwages\b|\blabor\b|\bstaff\b.{0,20}\bsettlement\b|"
            r"\bcrew\s+payroll\b|\brotational\s+staff\b",
            re.IGNORECASE,
        ),
    ),
    (
        "UTILITIES",
        MetricCategory.UTILITIES,
        re.compile(
            r"\butilities\b|\butility\b|\belectricity\b|\bdistrict\s+heating\b|"
            r"\bwater\s+supply\b|\bgas\s+supply\b|\bgenerator\s+electricity\b|"
            r"\bmunicipal\s+water\b|\bwaste\s+water\b|\bwater\s+charge\b|"
            r"\btelecom\s+services?\b|\btelecom\s+mobile\b|\bmobile\s+fleet\s+plan\b",
            re.IGNORECASE,
        ),
    ),
    (
        "REVENUE",
        MetricCategory.REVENUE,
        re.compile(
            r"\brevenue\b|\bsales\s+proceeds\b|\bsales\s+settlement\b|"
            r"\bsublet\s+rent\s+received\b|\brent\s+(?:deposit\s+returned|overpayment\s+refunded)\b|"
            r"\brent\s+free\s+period\s+credit\b|\binsurance\b.{0,40}\b(?:rebate|refund)\b|"
            r"\binterest\s+credited\b|\binterest\s+recovery\b|\binterest\s+income\b|"
            r"\binterest\s+rebate\b|"
            r"\b(?:excise\s+)?tax\s+(?:rebate|credit\s+received|overpayment\s+refunded)\b|"
            r"\bVAT\s+refund\b|\bservice\s+credit\s+received\b|"
            r"\badjustment\s+credit\b|\bexperience\s+refund\b|"
            r"\boverbilling\s+refund\b|\butility\s+(?:deposit\s+returned|rebate\s+received)\b",
            re.IGNORECASE,
        ),
    ),
)

_WEAK_OPEX = (
    "OPEX_WEAK",
    MetricCategory.OPEX,
    re.compile(
        r"\badvisory\b|\bretainer\b|\bconsulting\b|\bservicing\b|\bsupplies\b|"
        r"\bmarketing\b|\bsponsorship\b|\bmedia\b|\bmaintenance\b|\brepair\b|"
        r"\binspection\b|\bsurvey\b|\bmanagement\b|\bbroker\b|\bnewsletter\b|"
        r"\bcampaign\b|\bmaterials\b|\bsafety\s+systems\b|\bfidelity\s+bond\b|"
        r"\bcleaning\b|\bclearance\s+works\b|\bberth\b",
        re.IGNORECASE,
    ),
)

# Categories that appear in compiled selectors — for relevance triage.
SELECTOR_CATEGORIES: frozenset[MetricCategory] = frozenset(MetricCategory)


@dataclass(frozen=True, slots=True)
class ClassificationHit:
    status: str  # CLASSIFIED | UNRESOLVED | CONFLICT
    category: MetricCategory | None
    rule: str | None
    competing: tuple[str, ...] = ()


_INFLOW_MARKERS = re.compile(
    r"\b(?:rebate|refund(?:ed)?|experience\s+refund|credit\s+received|adjustment\s+credit|"
    r"overpayment\s+refunded|deposit\s+returned|free\s+period\s+credit)\b",
    re.IGNORECASE,
)


def classify_description(description: str) -> ClassificationHit:
    """Classify a ledger description with fail-closed conflict semantics."""
    text = description or ""
    strong_hits: list[tuple[str, MetricCategory]] = []
    for rule_id, category, pattern in _STRONG_RULES:
        if not pattern.search(text):
            continue
        # Expense categories yield to explicit inflow language handled by REVENUE.
        if rule_id in {
            "INSURANCE",
            "TAXES",
            "UTILITIES",
            "RENT",
            "LEASE",
        } and _INFLOW_MARKERS.search(text):
            continue
        if rule_id == "INTEREST_EXPENSE" and re.search(
            r"\binterest\s+(?:credited|recovery|income|rebate)\b", text, re.IGNORECASE
        ):
            continue
        strong_hits.append((rule_id, category))

    # Special collision: lease vs rent both present → CONFLICT (no arbitrary priority).
    # Already handled if both rules match.

    if len(strong_hits) > 1:
        # Contract-justified disambiguation (not arbitrary priority):
        # - capitalised interest is CAPEX (capitalization event)
        # - rent inflows / credits / refunds are REVENUE, not RENT expense
        rule_ids = {r for r, _ in strong_hits}
        if rule_ids == {"CAPEX", "INTEREST_EXPENSE"} and re.search(
            r"\bcapitalis(?:ed|ed)\b", text, re.IGNORECASE
        ):
            strong_hits = [("CAPEX_CAPITALISED_INTEREST", MetricCategory.CAPEX)]
        elif rule_ids == {"RENT", "REVENUE"}:
            strong_hits = [("REVENUE_RENT_INFLOW", MetricCategory.REVENUE)]
        elif rule_ids == {"TAXES", "REVENUE"}:
            strong_hits = [("REVENUE_TAX_INFLOW", MetricCategory.REVENUE)]
        elif rule_ids == {"UTILITIES", "REVENUE"}:
            strong_hits = [("REVENUE_UTILITY_CREDIT", MetricCategory.REVENUE)]
        elif rule_ids == {"INSURANCE", "REVENUE"}:
            strong_hits = [("REVENUE_INSURANCE_INFLOW", MetricCategory.REVENUE)]
        elif rule_ids == {"LEASE", "REVENUE"}:
            strong_hits = [("REVENUE_LEASE_INFLOW", MetricCategory.REVENUE)]

    if len(strong_hits) > 1:
        # Collapse duplicate category from multiple rules into single category if same.
        categories = {c for _, c in strong_hits}
        if len(categories) == 1:
            only = next(iter(categories))
            return ClassificationHit(
                status="CLASSIFIED",
                category=only,
                rule="+".join(sorted(r for r, _ in strong_hits)),
            )
        return ClassificationHit(
            status="CONFLICT",
            category=None,
            rule=None,
            competing=tuple(sorted(r for r, _ in strong_hits)),
        )

    if len(strong_hits) == 1:
        rule_id, category = strong_hits[0]
        return ClassificationHit(status="CLASSIFIED", category=category, rule=rule_id)

    # Weak OPEX only when no strong hit.
    rule_id, category, pattern = _WEAK_OPEX
    if pattern.search(text):
        return ClassificationHit(status="CLASSIFIED", category=category, rule=rule_id)

    return ClassificationHit(status="UNRESOLVED", category=None, rule=None)
