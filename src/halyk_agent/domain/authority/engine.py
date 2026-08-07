"""Pure deterministic Stage 5C authority engine."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.constants import (
    AUTHORITY_ALGORITHM_VERSION,
    AUTHORITY_RULE_VERSION,
    AUTHORITY_SCHEMA_VERSION,
    TAXONOMY_RULE_VERSION,
)
from halyk_agent.domain.authority.families import build_families
from halyk_agent.domain.authority.metadata import extract_metadata_bundle
from halyk_agent.domain.authority.models import (
    AuthorityDomain,
    AuthorityEvidenceAssertion,
    AuthorityManifest,
    AuthorityReport,
    AuthorityStatus,
    DocumentClassification,
    DocumentMetadata,
    DocumentType,
)
from halyk_agent.domain.authority.resolve import resolve_authority
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.parsing import CanonicalDocument
from halyk_agent.domain.routing.models import DocumentEntityLink, RoutingManifest


def _hash_json(payload: Mapping[str, Any] | list[Any] | str) -> str:
    if isinstance(payload, str):
        return sha256_text(payload)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def _build_evidence(
    *,
    classifications: tuple[DocumentClassification, ...],
    metadata_by_id: dict[str, DocumentMetadata],
    spans_by_id: dict[str, EvidenceSpan],
    documents_by_id: dict[str, CanonicalDocument],
) -> tuple[AuthorityEvidenceAssertion, ...]:
    assertions: list[AuthorityEvidenceAssertion] = []
    for item in classifications:
        if item.document_type is DocumentType.UNKNOWN and not item.evidence_span_ids:
            continue
        doc = documents_by_id.get(item.document_id)
        if doc is None or not doc.source_sha256:
            continue
        scenario_id = item.scenario_ids[0] if len(item.scenario_ids) == 1 else None
        meta = metadata_by_id.get(item.document_id)
        meta_span_ids = set(meta.evidence_span_ids) if meta is not None else set()
        for span_id in item.evidence_span_ids:
            span = spans_by_id.get(span_id)
            if span is None:
                continue
            kind = "METADATA_SIGNAL" if span_id in meta_span_ids else "TAXONOMY_LIFECYCLE"
            rule_id = item.rule_id if kind == "TAXONOMY_LIFECYCLE" else "RULE_METADATA_EVIDENCE"
            reason_code = (
                item.reason_code if kind == "TAXONOMY_LIFECYCLE" else "METADATA_EVIDENCE_SPAN"
            )
            assertions.append(
                AuthorityEvidenceAssertion(
                    assertion_id=deterministic_id(
                        "authority-evidence-v1",
                        item.document_id,
                        span_id,
                        rule_id,
                        reason_code,
                    ),
                    document_id=item.document_id,
                    scenario_id=scenario_id,
                    assertion_kind=kind,
                    document_type=item.document_type,
                    lifecycle_status=item.lifecycle_status,
                    authority_domain=item.authority_domains[0]
                    if item.authority_domains
                    else AuthorityDomain.NONE,
                    rule_id=rule_id,
                    reason_code=reason_code,
                    evidence_span_id=span_id,
                    raw_quote=span.quote,
                    page_number=span.page_number,
                    text_origin=span.text_origin.value,
                    source_sha256=doc.source_sha256,
                    ocr_backend_identity=span.ocr_backend_identity,
                )
            )
        if meta is not None:
            for span_id in meta.evidence_span_ids:
                if span_id in item.evidence_span_ids:
                    continue
                span = spans_by_id.get(span_id)
                if span is None:
                    continue
                assertions.append(
                    AuthorityEvidenceAssertion(
                        assertion_id=deterministic_id(
                            "authority-evidence-v1",
                            item.document_id,
                            span_id,
                            "RULE_METADATA_EVIDENCE",
                            "METADATA_EVIDENCE_SPAN",
                        ),
                        document_id=item.document_id,
                        scenario_id=scenario_id,
                        assertion_kind="METADATA_SIGNAL",
                        document_type=item.document_type,
                        lifecycle_status=item.lifecycle_status,
                        authority_domain=item.authority_domains[0]
                        if item.authority_domains
                        else AuthorityDomain.NONE,
                        rule_id="RULE_METADATA_EVIDENCE",
                        reason_code="METADATA_EVIDENCE_SPAN",
                        evidence_span_id=span_id,
                        raw_quote=span.quote,
                        page_number=span.page_number,
                        text_origin=span.text_origin.value,
                        source_sha256=doc.source_sha256,
                        ocr_backend_identity=span.ocr_backend_identity,
                    )
                )
    by_id = {a.assertion_id: a for a in assertions}
    return tuple(sorted(by_id.values(), key=lambda a: a.assertion_id))


def run_authority(
    *,
    documents: tuple[CanonicalDocument, ...],
    document_links: tuple[DocumentEntityLink, ...],
    routing_manifest: RoutingManifest | Mapping[str, Any],
    identity_evidence_hash: str = "",
    parsed_input_identity: Mapping[str, Any] | None = None,
) -> AuthorityReport:
    """
    Classify routed documents and resolve per-scenario authority domains.

    Consumes Stage 5B document links + OCR-enriched canonical documents.
    Does not rediscover raw datasets or read ground truth.
    """
    if isinstance(routing_manifest, RoutingManifest):
        routing_hash = _hash_json(routing_manifest.model_dump(mode="json"))
        routing_payload = routing_manifest.model_dump(mode="json")
    else:
        routing_payload = dict(routing_manifest)
        routing_hash = _hash_json(routing_payload)

    docs_sorted = tuple(sorted(documents, key=lambda d: d.document_id))
    documents_by_id = {d.document_id: d for d in docs_sorted}
    links_by_doc = {link.document_id: link for link in document_links}

    metadata_list: list[DocumentMetadata] = []
    classifications: list[DocumentClassification] = []
    all_spans: list[EvidenceSpan] = []

    for document in docs_sorted:
        meta_bundle = extract_metadata_bundle(document)
        meta = meta_bundle.metadata
        metadata_list.append(meta)
        all_spans.extend(meta_bundle.spans)
        link = links_by_doc.get(document.document_id)
        bundle = classify_document(document, metadata=meta, link=link)
        classifications.append(bundle.classification)
        all_spans.extend(bundle.spans)

    metadata_by_id = {m.document_id: m for m in metadata_list}

    families = build_families(
        classifications=tuple(classifications),
        metadata_by_id=metadata_by_id,
    )

    scenario_ids = sorted(
        {scenario_id for link in document_links for scenario_id in link.scenario_ids}
    )
    decisions, conflicts = resolve_authority(
        classifications=tuple(classifications),
        metadata_by_id=metadata_by_id,
        families=families,
        scenario_ids=tuple(scenario_ids),
    )

    spans_by_id = {span.id: span for span in all_spans}
    evidence = _build_evidence(
        classifications=tuple(classifications),
        metadata_by_id=metadata_by_id,
        spans_by_id=spans_by_id,
        documents_by_id=documents_by_id,
    )

    unknown_count = sum(1 for c in classifications if c.document_type is DocumentType.UNKNOWN)
    classified_count = len(classifications) - unknown_count
    missing_count = sum(1 for d in decisions if d.status is AuthorityStatus.MISSING_AUTHORITY)

    canonical_hash = sha256_text(
        json.dumps(
            [
                {
                    "document_id": d.document_id,
                    "source_sha256": d.source_sha256,
                    "document_version_id": d.document_version_id,
                }
                for d in docs_sorted
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )

    manifest = AuthorityManifest(
        schema_version=AUTHORITY_SCHEMA_VERSION,
        routing_manifest_hash=routing_hash,
        canonical_documents_hash=canonical_hash,
        identity_evidence_hash=identity_evidence_hash,
        taxonomy_rule_version=TAXONOMY_RULE_VERSION,
        authority_rule_version=AUTHORITY_RULE_VERSION,
        authority_algorithm_version=AUTHORITY_ALGORITHM_VERSION,
        document_count=len(docs_sorted),
        classified_count=classified_count,
        unknown_count=unknown_count,
        decision_count=len(decisions),
        conflict_count=len(conflicts),
        missing_authority_count=missing_count,
        family_count=len(families),
        evidence_count=len(evidence),
        parsed_input_identity=dict(parsed_input_identity or {}),
    )

    return AuthorityReport(
        manifest=manifest,
        metadata=tuple(sorted(metadata_list, key=lambda m: m.document_id)),
        classifications=tuple(sorted(classifications, key=lambda c: c.document_id)),
        families=families,
        decisions=decisions,
        conflicts=conflicts,
        evidence=evidence,
        spans=tuple(sorted(all_spans, key=lambda s: s.id)),
    )


def has_structural_authority_failure(report: AuthorityReport) -> bool:
    """True when any required domain has an unresolved conflict."""
    return any(d.status is AuthorityStatus.UNRESOLVED for d in report.decisions)
