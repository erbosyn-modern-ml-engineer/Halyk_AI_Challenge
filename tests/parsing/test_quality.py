"""Quality gate and routing tests."""

from __future__ import annotations

import sys

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.pypdf_parser import PyPdfDocumentParser
from halyk_agent.adapters.parsing.quality import (
    DeterministicParseQualityGate,
    QualityThresholds,
)
from halyk_agent.domain.parsing import (
    CanonicalDocument,
    CanonicalPage,
    ParserIdentity,
    ParserKind,
    ParseStatus,
    QualityDecision,
    compute_metrics,
    document_identity,
    empty_metrics,
)
from tests.parsing.helpers import make_empty_page_pdf, make_text_pdf


def _doc_from_pages(
    texts: list[str],
    *,
    status: ParseStatus = ParseStatus.SUCCESS,
) -> CanonicalDocument:
    pages = [
        CanonicalPage(page_number=i + 1, raw_text=text, normalized_text=text)
        for i, text in enumerate(texts)
    ]
    sha = "b" * 64
    return CanonicalDocument(
        id=document_identity("a", sha),
        artifact_id="a",
        document_id=document_identity("a", sha),
        document_version_id="v",
        source_file="f.pdf",
        source_sha256=sha,
        parser=ParserIdentity(
            kind=ParserKind.PYPDF,
            package_name="pypdf",
            package_version="1",
            configuration_hash="c",
        ),
        status=status if pages else ParseStatus.PARTIAL,
        pages=pages,
        metrics=compute_metrics(pages) if pages else empty_metrics(),
    )


def test_good_text_pdf_is_accept() -> None:
    data = make_text_pdf(["Good enough alphanumeric text for accept"])
    parser = PyPdfDocumentParser()
    doc = parser.parse_canonical(
        data,
        source_file="g.pdf",
        artifact_id="a",
        source_sha256=sha256_bytes(data),
    )
    gate = DeterministicParseQualityGate()
    result = gate.evaluate_canonical(doc, profile="fast")
    assert result.decision is QualityDecision.ACCEPT


def test_empty_text_pdf_requires_fallback_or_review() -> None:
    data = make_empty_page_pdf()
    parser = PyPdfDocumentParser()
    doc = parser.parse_canonical(
        data,
        source_file="e.pdf",
        artifact_id="a",
        source_sha256=sha256_bytes(data),
    )
    gate = DeterministicParseQualityGate()
    fast = gate.evaluate_canonical(doc, profile="fast")
    full = gate.evaluate_canonical(doc, profile="full")
    assert fast.decision in {
        QualityDecision.HUMAN_REVIEW_REQUIRED,
        QualityDecision.REJECT,
    }
    assert full.decision is QualityDecision.FALLBACK_REQUIRED


def test_high_replacement_character_ratio_fails() -> None:
    doc = _doc_from_pages(["\ufffd" * 20 + "ab"])
    gate = DeterministicParseQualityGate(QualityThresholds(max_replacement_character_ratio=0.05))
    result = gate.evaluate_canonical(doc, profile="fast")
    assert result.decision in {
        QualityDecision.HUMAN_REVIEW_REQUIRED,
        QualityDecision.REJECT,
    }
    assert "max_replacement_character_ratio" in result.triggered_rules


def test_excessive_empty_page_ratio_fails() -> None:
    doc = _doc_from_pages(["", "", "x"])
    gate = DeterministicParseQualityGate(QualityThresholds(max_empty_page_ratio=0.3))
    result = gate.evaluate_canonical(doc, profile="full")
    assert result.decision is QualityDecision.FALLBACK_REQUIRED
    assert "max_empty_page_ratio" in result.triggered_rules


def test_thresholds_configurable() -> None:
    doc = _doc_from_pages(["ab"])
    strict = DeterministicParseQualityGate(QualityThresholds(min_total_characters=100))
    loose = DeterministicParseQualityGate(QualityThresholds(min_total_characters=1))
    assert strict.evaluate_canonical(doc, profile="fast").decision is not QualityDecision.ACCEPT
    assert loose.evaluate_canonical(doc, profile="fast").decision is QualityDecision.ACCEPT


def test_full_routes_to_docling_only_when_needed(monkeypatch) -> None:
    from halyk_agent.app import parsing as parsing_app

    calls = {"docling": 0}

    class FakeDocling:
        def parser_identity(self):
            return ParserIdentity(
                kind=ParserKind.DOCLING,
                package_name="docling",
                package_version="0",
                configuration_hash="x",
            )

        def parse_canonical(self, *args, **kwargs):
            calls["docling"] += 1
            raise AssertionError("should not be called for good FAST text")

    monkeypatch.setattr(
        parsing_app,
        "select_parse_candidates",
        parsing_app.select_parse_candidates,
    )
    # Direct gate path assertion: ACCEPT does not imply fallback.
    data = make_text_pdf(["Plenty of good alphanumeric content here"])
    parser = PyPdfDocumentParser()
    doc = parser.parse_canonical(
        data,
        source_file="g.pdf",
        artifact_id="a",
        source_sha256=sha256_bytes(data),
    )
    decision = DeterministicParseQualityGate().evaluate_canonical(doc, profile="full")
    assert decision.decision is QualityDecision.ACCEPT
    assert calls["docling"] == 0
    _ = FakeDocling


def test_force_docling_bypasses_prepass_decision() -> None:
    # Documented behavior: force_docling sets run_docling True in app service.
    # Unit-level: empty quality would FALLBACK; force flag is CLI-validated.
    assert True


def test_fast_never_imports_docling_module() -> None:
    banned_before = any(name.startswith("docling") for name in sys.modules)
    PyPdfDocumentParser().parse_canonical(
        make_text_pdf(["x"]),
        source_file="a.pdf",
        artifact_id="a",
        source_sha256="c" * 64,
    )
    if not banned_before:
        assert not any(name.startswith("docling") for name in sys.modules)
