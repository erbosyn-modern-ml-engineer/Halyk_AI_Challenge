"""Parse cache acceptance tests."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.parsing.cache import (
    LocalParseCache,
    assert_no_pickle_files,
    cache_key,
)
from halyk_agent.adapters.parsing.limits import ParserLimits
from halyk_agent.adapters.parsing.pypdf_parser import (
    PyPdfDocumentParser,
    pypdf_parser_identity,
)
from tests.parsing.helpers import make_text_pdf


def _document(tmp_path: Path):
    data = make_text_pdf(["cached text content"])
    parser = PyPdfDocumentParser()
    doc = parser.parse_canonical(
        data,
        source_file="c.pdf",
        artifact_id="art",
        source_sha256=sha256_bytes(data),
    )
    return data, doc, pypdf_parser_identity(ParserLimits())


def test_cache_hit_returns_equivalent_document(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    hit = cache.get(source_sha256=sha256_bytes(data), parser=identity)
    assert hit is not None
    assert hit.model_dump(mode="json") == doc.model_dump(mode="json")


def test_parser_version_change_invalidates_cache(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    changed = identity.model_copy(update={"package_version": "0.0.0-changed"})
    assert cache.get(source_sha256=sha256_bytes(data), parser=changed) is None


def test_configuration_change_invalidates_cache(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    changed = identity.model_copy(update={"configuration_hash": "different"})
    assert cache.get(source_sha256=sha256_bytes(data), parser=changed) is None


def test_corrupt_cache_entry_ignored_and_replaced(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    key = cache_key(source_sha256=sha256_bytes(data), parser=identity)
    path = tmp_path / "cache" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    assert cache.get(source_sha256=sha256_bytes(data), parser=identity) is None
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    assert cache.get(source_sha256=sha256_bytes(data), parser=identity) is not None


def test_cache_writes_are_atomic_and_json_only(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    path = cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    assert path.suffix == ".json"
    assert not list((tmp_path / "cache").glob("*.pkl"))
    assert_no_pickle_files(tmp_path / "cache")


def test_no_pickle_files_used(tmp_path: Path) -> None:
    data, doc, identity = _document(tmp_path)
    cache = LocalParseCache(tmp_path / "cache")
    cache.put(doc, source_sha256=sha256_bytes(data), parser=identity)
    assert_no_pickle_files(tmp_path / "cache")
