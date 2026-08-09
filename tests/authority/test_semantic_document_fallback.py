from __future__ import annotations

from halyk_agent.config import Settings
from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.engine import run_authority
from halyk_agent.domain.authority.metadata import extract_metadata
from halyk_agent.domain.authority.models import (
    AuthorityDomain,
    DocumentLifecycleStatus,
    DocumentType,
)
from halyk_agent.domain.authority.semantic_classifier import classify_unresolved_documents
from halyk_agent.domain.models_gateway.semantic_json import SemanticJsonResult, SemanticJsonState
from tests.authority.helpers import make_document, make_link


class _FakeGateway:
    def __init__(self, payload: dict[str, str]) -> None:
        self.payload = payload

    def propose(self, **kwargs) -> SemanticJsonResult:
        return SemanticJsonResult(
            state=SemanticJsonState.RESOLVED,
            payload=self.payload,
            reason_code="OK",
            model_called=True,
        )


def test_unknown_document_can_receive_exact_quote_enum_proposal() -> None:
    text = "Credit Monitoring Pack\nExecuted lending contract for borrower Orion LLP"
    doc = make_document(raw_text=text)
    link = make_link(doc)
    meta = extract_metadata(doc)
    deterministic = classify_document(doc, metadata=meta, link=link).classification
    assert deterministic.document_type is DocumentType.UNKNOWN

    batch = classify_unresolved_documents(
        documents=(doc,),
        deterministic={doc.document_id: deterministic},
        metadata={doc.document_id: meta},
        settings=Settings(semantic_fallback_enabled=True),
        gateway=_FakeGateway(
            {
                "document_type": "LOAN_AGREEMENT",
                "lifecycle_status": "CURRENT_EXECUTED",
                "confidence": "HIGH",
                "source_quote": "Executed lending contract",
                "reason": "explicit executed lending contract marker",
            }
        ),
    )
    assert doc.document_id in batch.overrides

    report = run_authority(
        documents=(doc,),
        document_links=(link,),
        routing_manifest={"test": True},
        semantic_overrides=batch.overrides,
    )
    classification = report.classifications[0]
    assert classification.document_type is DocumentType.LOAN_AGREEMENT
    assert classification.lifecycle_status is DocumentLifecycleStatus.CURRENT_EXECUTED
    assert classification.rule_id == "DEEPSEEK_SEMANTIC_FALLBACK"
    assert AuthorityDomain.COVENANT_TERMS in classification.authority_domains
    assert classification.evidence_span_ids


def test_semantic_document_proposal_rejects_non_source_quote() -> None:
    doc = make_document(raw_text="Unclassified finance pack")
    link = make_link(doc)
    meta = extract_metadata(doc)
    deterministic = classify_document(doc, metadata=meta, link=link).classification
    batch = classify_unresolved_documents(
        documents=(doc,),
        deterministic={doc.document_id: deterministic},
        metadata={doc.document_id: meta},
        settings=Settings(semantic_fallback_enabled=True),
        gateway=_FakeGateway(
            {
                "document_type": "AUDITOR_REPORT",
                "lifecycle_status": "FINAL",
                "confidence": "HIGH",
                "source_quote": "Independent Auditor's Report",
                "reason": "guessed title",
            }
        ),
    )
    assert batch.overrides == {}
    assert batch.diagnostics[0]["reason"] == "SOURCE_QUOTE_NOT_EXACT"
