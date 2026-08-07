"""Account extraction tests."""

from __future__ import annotations

from halyk_agent.domain.ocr import TextOrigin
from halyk_agent.domain.routing.accounts import extract_account_identities, is_prefix_collision
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
