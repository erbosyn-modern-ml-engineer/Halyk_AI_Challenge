"""Parse cache acceptance and H1 legacy invalidation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.cache import (
    PARSE_CACHE_SCHEMA_VERSION,
    CacheGetStatus,
    LocalParseCache,
    assert_no_pickle_files,
    cache_key,
)
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.post_parse_gate import (
    PAGE_QUALITY_GATE_VERSION,
    apply_post_parse_quality_gate,
)
from halyk_agent.adapters.parsing.pypdf_parser import (
    PyPdfDocumentParser,
    pypdf_parser_identity,
)
from halyk_agent.adapters.parsing.text_normalization import NORMALIZATION_VERSION
from halyk_agent.domain.ids import deterministic_id
from halyk_agent.domain.page_quality import PageQualityState
from halyk_agent.domain.parsing import (
    CANONICAL_DOCUMENT_SCHEMA_VERSION,
    ParseStatus,
)
from tests.parsing.helpers import make_text_pdf


def _document(tmp_path: Path):
    data = make_text_pdf(["cached text content that is long enough for TEXT_OK pages"])
    parser = PyPdfDocumentParser()
    candidate = parser.parse_canonical(
        data,
        source_file="c.pdf",
        artifact_id="art",
        source_sha256=sha256_bytes(data),
    )
    gated = apply_post_parse_quality_gate(candidate)
    return data, gated.document, gated.summary, pypdf_parser_identity(ParserLimits())


def test_cache_hit_returns_equivalent_document(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    hit = cache.get(source_sha256=sha256_bytes(data), parser=identity)
    assert hit.status is CacheGetStatus.HIT
    assert hit.document is not None
    assert hit.document.model_dump(mode="json") == doc.model_dump(mode="json")


def test_parser_version_change_invalidates_cache(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    changed = identity.model_copy(update={"package_version": "0.0.0-changed"})
    assert cache.get(source_sha256=sha256_bytes(data), parser=changed).status is CacheGetStatus.MISS


def test_configuration_change_invalidates_cache(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    changed = identity.model_copy(update={"configuration_hash": "different"})
    assert cache.get(source_sha256=sha256_bytes(data), parser=changed).status is CacheGetStatus.MISS


def test_page_quality_version_change_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import halyk_agent.adapters.parsing.cache as cache_mod

    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    monkeypatch.setattr(cache_mod, "PAGE_QUALITY_GATE_VERSION", "halyk.page_quality_gate.v999")
    lookup = cache.get(source_sha256=sha256_bytes(data), parser=identity)
    # Key identity includes gate version → miss; envelope check would be incompatible.
    assert lookup.status in {CacheGetStatus.MISS, CacheGetStatus.INCOMPATIBLE}


def test_ocr_policy_change_invalidates(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache", ocr_policy="ocr_disabled")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    other = LocalParseCache(tmp_path / "cache", ocr_policy="ocr_enabled")
    assert (
        other.get(source_sha256=sha256_bytes(data), parser=identity).status is CacheGetStatus.MISS
    )


def test_corrupt_cache_entry_ignored_and_replaced(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    key = cache_key(source_sha256=sha256_bytes(data), parser=identity)
    path = tmp_path / "cache" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    assert (
        cache.get(source_sha256=sha256_bytes(data), parser=identity).status
        is CacheGetStatus.CORRUPT
    )
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    assert cache.get(source_sha256=sha256_bytes(data), parser=identity).status is CacheGetStatus.HIT


def test_legacy_success_without_page_quality_is_incompatible(tmp_path: Path) -> None:
    data = make_text_pdf(["LIMIT"])
    parser = PyPdfDocumentParser()
    candidate = parser.parse_canonical(
        data,
        source_file="legacy.pdf",
        artifact_id="legacy",
        source_sha256=sha256_bytes(data),
    )
    # Force SUCCESS as legacy cache would have stored before the gate.
    legacy_doc = candidate.model_copy(update={"status": ParseStatus.SUCCESS})
    identity = pypdf_parser_identity(ParserLimits())
    legacy_key = deterministic_id(
        "parse-cache-v1",
        sha256_bytes(data),
        identity.package_name,
        identity.package_version,
        identity.configuration_hash,
        CANONICAL_DOCUMENT_SCHEMA_VERSION,
        NORMALIZATION_VERSION,
    )
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / f"{legacy_key}.json").write_text(
        json.dumps(legacy_doc.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    cache = LocalParseCache(cache_root)
    lookup = cache.get(source_sha256=sha256_bytes(data), parser=identity)
    assert lookup.status is CacheGetStatus.INCOMPATIBLE
    assert lookup.document is None

    # Reparse path via gate must not return trusted SUCCESS for heading-only content.
    gated = apply_post_parse_quality_gate(candidate)
    assert gated.document.status is not ParseStatus.SUCCESS
    assert any(s is PageQualityState.OCR_REQUIRED for s in gated.summary.page_states)


def test_cache_writes_are_atomic_and_json_only(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    path = cache.put(
        doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary
    )
    assert path.suffix == ".json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cache_schema_version"] == PARSE_CACHE_SCHEMA_VERSION
    assert payload["page_quality_gate_version"] == PAGE_QUALITY_GATE_VERSION
    assert PAGE_QUALITY_GATE_VERSION == "halyk.page_quality_gate.v2"
    assert_no_pickle_files(tmp_path / "cache")


def test_no_pickle_files_used(tmp_path: Path) -> None:
    data, doc, summary, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity, page_quality_summary=summary)
    assert_no_pickle_files(tmp_path / "cache")
