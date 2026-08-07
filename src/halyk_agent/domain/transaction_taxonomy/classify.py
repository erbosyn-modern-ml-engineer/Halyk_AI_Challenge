"""Deterministic description→MetricCategory classification (precision > recall)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from halyk_agent.domain.covenants.ast import MetricCategory

# Credit/refund/rebate markers — not sufficient for REVENUE by themselves.
_CREDIT_MARKERS = re.compile(
    r"\b(?:rebate|refund(?:ed)?|credited?|credit\s+received|overpayment\s+refunded|"
    r"deposit\s+returned|budget\s+returned|overbilling|experience\s+refund|"
    r"free\s+period\s+credit|true-up|"
    r"(?:rent|sublet|utility|insurance|tax|vat|telecom)\s+received)\b",
    re.IGNORECASE,
)

# Interest income / credited interest is financing cash inflow, never covenant REVENUE.
_INTEREST_INCOME = re.compile(
    r"\binterest\s+(?:income|credited|recovery|earned)\b|"
    r"\b(?:treasury|deposit|current\s+account)\s+interest\b",
    re.IGNORECASE,
)

# Genuine customer/operating revenue primitives (not cash-inflow synonyms).
_REVENUE_PRIMITIVES = re.compile(
    r"\b(?:customer\s+(?:revenue|payment|receipt|settlement|invoice)|"
    r"sales?\s+(?:revenue|proceeds|settlement|invoice|collection)|"
    r"service\s+revenue|product\s+revenue|contract\s+revenue|"
    r"invoice\s+collection|operating\s+revenue|\brevenue\b)\b",
    re.IGNORECASE,
)

# Strong exclusive rules. Multiple hits → CONFLICT (no silent priority).
_STRONG_RULES: tuple[tuple[str, MetricCategory, re.Pattern[str]], ...] = (
    (
        "SEVERANCE",
        MetricCategory.SEVERANCE_LIABILITY,
        re.compile(r"\bseverance\b", re.IGNORECASE),
    ),
    (
        # Explicit unrestricted wording only — never infer from bare "subsidiary".
        "CAPITAL_TRANSFER_UNRESTRICTED",
        MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
        re.compile(
            r"(?:capital\s+asset\s+transfer|\btransfer(?:s|red)?\b|\btransfer\s+of\b)"
            r".{0,80}\bunrestricted\s+subsidiar",
            re.IGNORECASE,
        ),
    ),
    (
        # Plain capital transfer to a subsidiary (status UNKNOWN until facts prove otherwise).
        "CAPITAL_TRANSFER_SUBSIDIARY",
        MetricCategory.CAPEX,
        re.compile(
            r"(?:\btransfer(?:s|red)?\b|\btransfer\s+of\b).{0,80}\bsubsidiar",
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
        re.compile(r"\bone[-\s]?time\s+add[-\s]?back\b", re.IGNORECASE),
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
            r"\bcapitalis(?:ed|ed)\b|\bplant\s+machinery\b",
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
            r"\binterest\s+on\b|\brevolver\s+interest\b|"
            r"\binterest\s+(?:charge|payment|expense|coupon|true-up)\b|"
            r"\boverdraft\s+interest\b|\bcredit\s+facility\s+interest\b|"
            r"\bterm\s+loan\s+interest\b|\bquarterly\s+interest\b",
            re.IGNORECASE,
        ),
    ),
    (
        "TAXES",
        MetricCategory.TAXES,
        re.compile(
            r"\b(?:income|franchise|withholding|excise|profit|property|vehicle|"
            r"municipal|emissions|environmental)\s+tax\b|"
            r"\bVAT\b|\bestimated\s+tax\b|"
            r"\btax\s+(?:instalment|remittance|assessment|filing|audit|penalty|levy|payment)\b|"
            r"\bcustoms\s+duty\b",
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
            r"\bpayroll\b|\bsalary\b|\bwages\b|\blabor\b|"
            r"\bstaff\b.{0,30}\b(?:settlement|payroll|compensation)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "UTILITIES",
        MetricCategory.UTILITIES,
        re.compile(
            r"\butilities\b|\butility\b|\belectricity\b|\bdistrict\s+heating\b|"
            r"\bwater\s+(?:supply|charge)\b|\bgas\s+supply\b|\bwaste\s+water\b|"
            r"\btelecom\b",
            re.IGNORECASE,
        ),
    ),
    (
        "REVENUE",
        MetricCategory.REVENUE,
        _REVENUE_PRIMITIVES,
    ),
)

_WEAK_OPEX = (
    "OPEX_WEAK",
    MetricCategory.OPEX,
    re.compile(
        r"\badvisory\b|\bretainer\b|\bconsulting\b|\bservicing\b|\bsupplies\b|"
        r"\bmarketing\b|\bad\s+campaign\b|\badvertis|\bsponsorship\b|\bmedia\b|"
        r"\bmaintenance\b|\brepair\b|\binspection\b|\bsurvey\b|\bmanagement\b|"
        r"\bbroker\b|\bcleaning\b|\bclearance\b",
        re.IGNORECASE,
    ),
)

# Expense-family recovery for credit/refund language (not customer revenue).
_EXPENSE_CREDIT_FAMILIES: tuple[tuple[str, MetricCategory, re.Pattern[str]], ...] = (
    (
        "INSURANCE_CREDIT",
        MetricCategory.INSURANCE_PREMIUMS,
        re.compile(r"\binsurance\b|\bpremium\b", re.IGNORECASE),
    ),
    (
        "TAX_CREDIT",
        MetricCategory.TAXES,
        re.compile(r"\btax\b|\bVAT\b|\bexcise\b|\bcustoms\b", re.IGNORECASE),
    ),
    (
        "UTILITY_CREDIT",
        MetricCategory.UTILITIES,
        re.compile(
            r"\butility\b|\belectricity\b|\bwater\b|\btelecom\b|\bheating\b",
            re.IGNORECASE,
        ),
    ),
    (
        "RENT_CREDIT",
        MetricCategory.RENT,
        re.compile(r"\brent\b|\brental\b|\bsublet\b", re.IGNORECASE),
    ),
    (
        "LEASE_CREDIT",
        MetricCategory.LEASE_PAYMENTS,
        re.compile(r"\blease\b|\bleasing\b", re.IGNORECASE),
    ),
    (
        "LABOR_CREDIT",
        MetricCategory.LABOR,
        re.compile(r"\bpayroll\b|\bsalary\b|\bwages\b|\blabor\b|\bstaff\b", re.IGNORECASE),
    ),
    (
        "INTEREST_CREDIT",
        MetricCategory.INTEREST_EXPENSE,
        re.compile(r"\binterest\b", re.IGNORECASE),
    ),
    (
        "OPEX_CREDIT",
        MetricCategory.OPEX,
        re.compile(
            r"\bmarketing\b|\bad\s+campaign\b|\badvertis|\badvisory\b|"
            r"\bservicing\b|\bsupplies\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ClassificationHit:
    status: str  # CLASSIFIED | UNRESOLVED | CONFLICT
    category: MetricCategory | None
    rule: str | None
    competing: tuple[str, ...] = ()


def _classify_credit_family(text: str) -> ClassificationHit | None:
    """Map refund/rebate/credit rows to originating expense family — never REVENUE."""
    if _INTEREST_INCOME.search(text):
        return ClassificationHit(
            status="CLASSIFIED",
            category=MetricCategory.FINANCING_INFLOWS,
            rule="INTEREST_INCOME_NOT_REVENUE",
        )
    if not _CREDIT_MARKERS.search(text):
        return None
    hits: list[tuple[str, MetricCategory]] = []
    for rule_id, category, pattern in _EXPENSE_CREDIT_FAMILIES:
        if pattern.search(text):
            hits.append((rule_id, category))
    if len(hits) == 1:
        rule_id, category = hits[0]
        return ClassificationHit(status="CLASSIFIED", category=category, rule=rule_id)
    if len(hits) > 1:
        cats = {c for _, c in hits}
        if len(cats) == 1:
            only = next(iter(cats))
            return ClassificationHit(
                status="CLASSIFIED",
                category=only,
                rule="+".join(sorted(r for r, _ in hits)),
            )
        # Prefer more specific expense families over OPEX_CREDIT.
        non_opex = [(r, c) for r, c in hits if c is not MetricCategory.OPEX]
        if len({c for _, c in non_opex}) == 1:
            rule_id, category = non_opex[0]
            return ClassificationHit(status="CLASSIFIED", category=category, rule=rule_id)
        return ClassificationHit(
            status="CONFLICT",
            category=None,
            rule=None,
            competing=tuple(sorted(r for r, _ in hits)),
        )
    return ClassificationHit(
        status="UNRESOLVED",
        category=None,
        rule="CREDIT_WITHOUT_EXPENSE_FAMILY",
    )


def classify_description(description: str) -> ClassificationHit:
    """Classify a ledger description with fail-closed conflict semantics."""
    text = description or ""

    # Interest income / credits/refunds/rebates are never covenant REVENUE.
    credit_hit = _classify_credit_family(text)
    if credit_hit is not None:
        if credit_hit.rule == "INTEREST_INCOME_NOT_REVENUE":
            return credit_hit
        if _CREDIT_MARKERS.search(text):
            if credit_hit.status == "UNRESOLVED" and _REVENUE_PRIMITIVES.search(text):
                return ClassificationHit(
                    status="UNRESOLVED",
                    category=None,
                    rule="CREDIT_BLOCKS_REVENUE",
                )
            return credit_hit

    strong_hits: list[tuple[str, MetricCategory]] = []
    for rule_id, category, pattern in _STRONG_RULES:
        if not pattern.search(text):
            continue
        if rule_id == "REVENUE" and (_CREDIT_MARKERS.search(text) or _INTEREST_INCOME.search(text)):
            continue
        if rule_id == "INTEREST_EXPENSE" and _INTEREST_INCOME.search(text):
            continue
        # Bare subsidiary transfer handled by CAPITAL_TRANSFER_SUBSIDIARY (CAPEX primary);
        # skip unrestricted rule unless unrestricted present (pattern already requires it).
        strong_hits.append((rule_id, category))

    if len(strong_hits) > 1:
        rule_ids = {r for r, _ in strong_hits}
        # capitalised interest → CAPEX
        if rule_ids >= {"CAPEX", "INTEREST_EXPENSE"} and re.search(
            r"\bcapitalis(?:ed|ed)\b", text, re.IGNORECASE
        ):
            strong_hits = [("CAPEX_CAPITALISED_INTEREST", MetricCategory.CAPEX)]
        # unrestricted transfer beats bare subsidiary transfer
        elif "CAPITAL_TRANSFER_UNRESTRICTED" in rule_ids:
            strong_hits = [
                (
                    "CAPITAL_TRANSFER_UNRESTRICTED",
                    MetricCategory.CAPITAL_ASSET_TRANSFERS_TO_UNRESTRICTED_SUBS,
                )
            ]
        elif rule_ids >= {"CAPITAL_TRANSFER_SUBSIDIARY", "CAPEX"}:
            strong_hits = [("CAPITAL_TRANSFER_SUBSIDIARY", MetricCategory.CAPEX)]
        # "leased line" is a lease product; prefer LEASE over UTILITIES(telecom).
        elif rule_ids >= {"LEASE", "UTILITIES"} and re.search(
            r"\bleased\s+line\b", text, re.IGNORECASE
        ):
            strong_hits = [("LEASE_LINE", MetricCategory.LEASE_PAYMENTS)]

    if len(strong_hits) > 1:
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

    rule_id, category, pattern = _WEAK_OPEX
    if pattern.search(text):
        return ClassificationHit(status="CLASSIFIED", category=category, rule=rule_id)

    return ClassificationHit(status="UNRESOLVED", category=None, rule=None)
