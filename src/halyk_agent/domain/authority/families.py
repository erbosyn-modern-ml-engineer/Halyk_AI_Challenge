"""Document family grouping for version/authority chains (Stage 5C)."""

from __future__ import annotations

from collections import defaultdict

from halyk_agent.domain.authority.models import (
    DocumentClassification,
    DocumentFamily,
    DocumentMetadata,
    DocumentType,
)
from halyk_agent.domain.ids import deterministic_id


def _agreement_family_key(
    *,
    scenario_id: str,
    metadata: DocumentMetadata,
    classification: DocumentClassification,
) -> str:
    if metadata.agreement_number:
        return f"loan:{scenario_id}:{metadata.agreement_number}"
    # Same scenario loan agreements without explicit number share one family.
    return f"loan:{scenario_id}:unnumbered"


def build_families(
    *,
    classifications: tuple[DocumentClassification, ...],
    metadata_by_id: dict[str, DocumentMetadata],
) -> tuple[DocumentFamily, ...]:
    """
    Group documents into logical families.

    Loan agreements are grouped per scenario using agreement number when present.
    Auditor / KYC / AUP families are per scenario + type (not merged across types).
    """
    buckets: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    agreement_numbers: dict[tuple[str, str, str], str | None] = {}

    for item in classifications:
        if not item.scenario_ids:
            continue
        for scenario_id in item.scenario_ids:
            meta = metadata_by_id.get(item.document_id)
            if item.document_type is DocumentType.LOAN_AGREEMENT and meta is not None:
                key = _agreement_family_key(
                    scenario_id=scenario_id,
                    metadata=meta,
                    classification=item,
                )
                kind = "LOAN_AGREEMENT"
                agreement_numbers[(scenario_id, kind, key)] = meta.agreement_number
            elif item.document_type in {
                DocumentType.AUDITOR_REPORT,
                DocumentType.AGREED_UPON_PROCEDURES_REPORT,
                DocumentType.KYC_DOSSIER,
                DocumentType.GROUP_OR_CONSOLIDATED_REPORT,
                DocumentType.TREASURY_MEMO,
            }:
                kind = item.document_type.value
                key = f"{kind.lower()}:{scenario_id}"
                agreement_numbers[(scenario_id, kind, key)] = None
            else:
                continue
            buckets[(scenario_id, kind, key)].append(item.document_id)

    families: list[DocumentFamily] = []
    for (scenario_id, kind, key), doc_ids in sorted(buckets.items()):
        unique = tuple(sorted(set(doc_ids)))
        family_id = deterministic_id("doc-family-v1", scenario_id, kind, key, *unique)
        families.append(
            DocumentFamily(
                family_id=family_id,
                scenario_id=scenario_id,
                family_kind=kind,
                family_key=key,
                document_ids=unique,
                agreement_number=agreement_numbers.get((scenario_id, kind, key)),
            )
        )
    return tuple(families)
