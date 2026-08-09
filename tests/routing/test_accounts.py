"""Account extraction tests."""

from __future__ import annotations

from halyk_agent.domain.ocr import TextOrigin
from halyk_agent.domain.routing.accounts import (
    build_account_vocabulary,
    extract_account_identities,
    is_prefix_collision,
)
from tests.routing.helpers import make_document


def test_exact_complete_token_no_prefix_collision() -> None:
    doc = make_document(raw_text="Account ACC-7801 and related ACC-7801-08 note.")
    bundle = extract_account_identities(doc)
    norms = {a.account_id_normalized for a in bundle.accounts}
    assert "ACC-7801" in norms
    assert "ACC-7801-08" in norms
    assert is_prefix_collision("ACC-7801", "ACC-7801-08")
    assert not is_prefix_collision("ACC-7801", "ACC-7801")


def test_ocr_derived_account_requires_provenance() -> None:
    trusted = make_document(
        artifact="ocr1",
        raw_text="Счёт ACC-7808",
        text_origin="OCR",
        ocr_backend="tesseract_cli",
    )
    ok = extract_account_identities(trusted)
    assert len(ok.accounts) == 1
    assert ok.accounts[0].text_origin == TextOrigin.OCR.value
    assert ok.spans[0].ocr_backend_identity == "tesseract_cli"

    untrusted = make_document(
        artifact="ocr2",
        raw_text="Счёт ACC-7809",
        text_origin="OCR",
        ocr_backend=None,
    )
    bad = extract_account_identities(untrusted)
    assert bad.accounts == ()
    assert any(d.code.value == "OCR_IDENTITY_LOW_TRUST" for d in bad.diagnostics)


def test_malformed_account_diagnosed() -> None:
    doc = make_document(raw_text="bad token ACC-ABCD appears here")
    bundle = extract_account_identities(doc)
    assert bundle.accounts == ()
    assert any(d.code.value == "ACCOUNT_ID_MALFORMED" for d in bundle.diagnostics)


def test_opaque_non_acc_identifier_extracted_from_vocabulary() -> None:
    """F. An observed ledger identifier is recognized whatever its prefix."""
    doc = make_document(raw_text="Расчётный счёт SATCOM-X91 обслуживается Кредитором.")
    assert extract_account_identities(doc).accounts == ()

    vocabulary = build_account_vocabulary(["SATCOM-X91", "ACC-7001"])
    bundle = extract_account_identities(doc, vocabulary=vocabulary)
    assert [a.account_id_normalized for a in bundle.accounts] == ["SATCOM-X91"]
    assert bundle.accounts[0].evidence_span_id
    assert bundle.spans


def test_vocabulary_matching_is_exact_and_complete_token() -> None:
    """No substring or fuzzy hits: neighbouring tokens must not bleed."""
    vocabulary = build_account_vocabulary(["TELE-4471", "ACC-7801", "ACC-7801-08"])
    doc = make_document(
        raw_text=(
            "Valid TELE-4471 here; TELE-44712 and XTELE-4471 and TELE-447 are other tokens. "
            "Both ACC-7801 and ACC-7801-08 stay distinct."
        )
    )
    bundle = extract_account_identities(doc, vocabulary=vocabulary)
    assert [a.account_id_normalized for a in bundle.accounts] == [
        "ACC-7801",
        "ACC-7801-08",
        "TELE-4471",
    ]


def test_vocabulary_identifier_not_reported_as_malformed() -> None:
    """An observed identifier outruns the legacy ACC-family shape heuristic."""
    doc = make_document(raw_text="Account ACC-ABCD is the declared account.")
    vocabulary = build_account_vocabulary(["ACC-ABCD"])
    bundle = extract_account_identities(doc, vocabulary=vocabulary)
    assert [a.account_id_normalized for a in bundle.accounts] == ["ACC-ABCD"]
    assert not any(d.code.value == "ACCOUNT_ID_MALFORMED" for d in bundle.diagnostics)
