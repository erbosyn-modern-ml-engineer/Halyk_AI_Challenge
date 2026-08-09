"""Lifecycle supersession must be a document-status banner, not covenant prose."""

# Multilingual covenant/banner literals are intentional.
# ruff: noqa: RUF001

from __future__ import annotations

from halyk_agent.domain.authority.classify import classify_document
from halyk_agent.domain.authority.metadata import extract_metadata
from halyk_agent.domain.authority.models import DocumentLifecycleStatus
from tests.authority.helpers import make_document, make_link

_AGREEMENT_HEAD = (
    "CONFIDENTIAL · ИСПОЛНИТЕЛЬНЫЙ ЭКЗЕМПЛЯР · ЗАЁМ № ACC-1000\n"
    "ДОГОВОР БАНКОВСКОГО ЗАЙМА\n"
    "Старший обеспеченный заём\n"
    "г. Алматы · от 1 января 2025 года\n"
)


def _classify(raw_text: str, *, artifact: str):
    doc = make_document(artifact=artifact, raw_text=raw_text)
    metadata = extract_metadata(doc)
    link = make_link(doc, scenario_ids=("S1",))
    return classify_document(doc, metadata=metadata, link=link).classification


def test_springing_else_branch_is_not_supersession() -> None:
    """ "…ограничение не применяется" is the ELSE arm of a springing covenant."""
    text = (
        _AGREEMENT_HEAD
        + "Пункт 6.1 Если Коэффициент долговой нагрузки превышает 3.00x, то Заёмщик "
        "обязуется не допускать превышения Капитальными затратами величины $2,500,000.00. "
        "Пока Коэффициент долговой нагрузки не превышает 3.00x, указанное ограничение "
        "Капитальных затрат не применяется."
    )
    classification = _classify(text, artifact="springing")
    assert classification.lifecycle_status is not DocumentLifecycleStatus.SUPERSEDED


def test_obsolete_asset_carve_out_is_not_supersession() -> None:
    """ "obsolete or worn assets" is ordinary disposal language."""
    text = (
        "CONFIDENTIAL · EXECUTION COPY · LOAN REFERENCE ACC-1000\n"
        "CREDIT AGREEMENT\nDated as of 1 January 2025\n"
        "The Borrower shall not dispose of fixed assets other than disposals of "
        "obsolete or worn assets replaced by assets of equivalent value."
    )
    classification = _classify(text, artifact="carveout")
    assert classification.lifecycle_status is not DocumentLifecycleStatus.SUPERSEDED


def test_status_banner_is_supersession() -> None:
    text = (
        "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ (2024 г.). Заменена и изложена в новой редакции "
        "действующим Договором текущего периода. НЕ ПРИМЕНЯЕТСЯ.\n" + _AGREEMENT_HEAD
    )
    classification = _classify(text, artifact="banner")
    assert classification.lifecycle_status is DocumentLifecycleStatus.SUPERSEDED


def test_english_status_banner_is_supersession() -> None:
    text = (
        "SUPERSEDED — PRIOR-YEAR AGREEMENT (2024). Amended and restated by the "
        "current-period Agreement. NOT OPERATIVE.\n"
        "CONFIDENTIAL · EXECUTION COPY · LOAN REFERENCE ACC-1000\nCREDIT AGREEMENT\n"
    )
    classification = _classify(text, artifact="enbanner")
    assert classification.lifecycle_status is DocumentLifecycleStatus.SUPERSEDED


def test_execution_copy_marks_current_executed() -> None:
    """An execution copy declares its status whether stamped as noun or participle."""
    text = (
        "CONFIDENTIAL · EXECUTION COPY · LOAN REFERENCE ACC-1000\n"
        "CREDIT AGREEMENT\nSenior Secured Credit Facility\nDated as of 1 January 2025\n"
        "The Borrower shall not permit its Net Leverage Ratio to exceed 3.00x."
    )
    classification = _classify(text, artifact="execcopy")
    assert classification.lifecycle_status is DocumentLifecycleStatus.CURRENT_EXECUTED


def test_metadata_marker_ignores_prose_occurrence() -> None:
    doc = make_document(
        artifact="prose",
        raw_text=_AGREEMENT_HEAD + "указанное ограничение Капитальных затрат не применяется.",
    )
    assert extract_metadata(doc).superseded_marker is None
