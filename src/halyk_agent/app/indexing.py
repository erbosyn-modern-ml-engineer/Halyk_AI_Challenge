"""Indexing application service: Stage 3 parse output → retrieval index."""

from __future__ import annotations

import json
import os
from pathlib import Path

from halyk_agent.adapters.chunking.structure_chunker import (
    ChunkerConfig,
    StructureAwareChunker,
    build_chunk_manifest,
    build_chunker_identity,
)
from halyk_agent.adapters.embeddings.cache import LocalEmbeddingCache
from halyk_agent.adapters.embeddings.model_registry import (
    FAST_EMBEDDING_LOGICAL_NAME,
    FULL_EMBEDDING_LOGICAL_NAME,
)
from halyk_agent.adapters.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)
from halyk_agent.adapters.retrieval.local.hybrid import LocalHybridRetriever
from halyk_agent.config import Settings, get_settings
from halyk_agent.contracts.retrieval import EmbeddingProvider
from halyk_agent.domain.chunking import RetrievalChunk
from halyk_agent.domain.evidence import EvidenceSpan
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.parsing import CanonicalDocument, ParseBatchReport, ParseStatus
from halyk_agent.domain.retrieval import IndexIdentity, IndexReport


class IndexingError(Exception):
    """Typed indexing failure without secret leakage."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _dump_json(model: object) -> str:
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _logical_embedding_name(profile: str, embedding_model: str | None) -> str:
    if embedding_model:
        aliases = {
            "fast": FAST_EMBEDDING_LOGICAL_NAME,
            "full": FULL_EMBEDDING_LOGICAL_NAME,
            "intfloat/multilingual-e5-small": FAST_EMBEDDING_LOGICAL_NAME,
            "BAAI/bge-m3": FULL_EMBEDDING_LOGICAL_NAME,
        }
        return aliases.get(embedding_model, embedding_model)
    return FAST_EMBEDDING_LOGICAL_NAME if profile == "fast" else FULL_EMBEDDING_LOGICAL_NAME


async def index_parsed_directory(
    parsed_dir: Path,
    output_dir: Path,
    *,
    profile: str,
    overwrite: bool = False,
    include_partial: bool = False,
    embedding_model: str | None = None,
    batch_size: int = 16,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> IndexReport:
    """Build chunks + embeddings + FAST/FULL index from Stage 3 parse output."""
    _ = settings or get_settings()
    profile_norm = profile.lower().strip()
    if profile_norm not in {"fast", "full"}:
        raise IndexingError("profile must be fast or full")

    parsed_dir = parsed_dir.resolve()
    output_dir = output_dir.resolve()
    report_path = parsed_dir / "parse_report.json"
    documents_dir = parsed_dir / "documents"
    evidence_path = parsed_dir / "evidence_catalog.jsonl"
    if not report_path.is_file() or not documents_dir.is_dir():
        raise IndexingError("invalid parsed input: missing parse_report.json or documents/")

    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise IndexingError("output directory is not empty; pass --overwrite")

    output_dir.mkdir(parents=True, exist_ok=True)
    report_text = report_path.read_text(encoding="utf-8")
    ParseBatchReport.model_validate_json(report_text)
    source_hash = sha256_text(report_text)

    evidence: list[EvidenceSpan] = []
    if evidence_path.is_file():
        for line in evidence_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                evidence.append(EvidenceSpan.model_validate_json(line))

    documents: list[CanonicalDocument] = []
    skipped_notes: list[str] = []
    failures: list[str] = []
    for path in sorted(documents_dir.glob("*.json")):
        doc = CanonicalDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if doc.status in {ParseStatus.FAILED, ParseStatus.ENCRYPTED}:
            skipped_notes.append(f"{doc.artifact_id}:{doc.status.value}")
            failures.append(f"{doc.artifact_id}:{doc.status.value}")
            continue
        if doc.status is ParseStatus.PARTIAL and not include_partial:
            skipped_notes.append(f"{doc.artifact_id}:PARTIAL")
            failures.append(f"{doc.artifact_id}:PARTIAL_requires_include_partial")
            continue
        documents.append(doc)

    if not documents:
        raise IndexingError("no indexable documents found")

    chunker_cfg = ChunkerConfig()
    chunker = StructureAwareChunker(chunker_cfg)
    chunker_identity = build_chunker_identity(chunker_cfg)
    all_chunks: list[RetrievalChunk] = []
    for doc in documents:
        doc_evidence = [
            span
            for span in evidence
            if span.document_id == doc.document_id
            and span.document_version_id == doc.document_version_id
        ]
        all_chunks.extend(chunker.chunk_document(doc, doc_evidence))

    if not all_chunks:
        raise IndexingError("chunker produced no chunks")

    manifest = build_chunk_manifest(
        all_chunks,
        chunker_identity=chunker_identity,
        source_parse_report_hash=source_hash,
    )
    _atomic_write_text(
        output_dir / "chunks.jsonl",
        "\n".join(
            json.dumps(c.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            for c in sorted(all_chunks, key=lambda item: item.id)
        )
        + "\n",
    )
    _atomic_write_text(output_dir / "chunk_manifest.json", _dump_json(manifest))

    logical_name = _logical_embedding_name(profile_norm, embedding_model)
    provider: EmbeddingProvider = embedding_provider or (
        SentenceTransformerEmbeddingProvider.from_logical_name(
            logical_name,
            batch_size=batch_size,
            cache=LocalEmbeddingCache(output_dir / "embeddings"),
        )
    )
    model_identity = provider.identity()
    vectors = await provider.embed_documents([c.retrieval_text for c in all_chunks])
    embeddings = {
        chunk.id: list(vector.values) for chunk, vector in zip(all_chunks, vectors, strict=True)
    }

    index_identity = IndexIdentity(
        profile=profile_norm,
        chunk_manifest_hash=manifest.chunks_sha256,
        chunker_identity=chunker_identity,
        embedding_model=model_identity,
        lexical_configuration={
            "backend": "sqlite_fts5" if profile_norm == "fast" else "postgres_simple"
        },
        rrf_configuration={"rrf_k": 60},
        reranker_model=None,
    )

    if profile_norm == "fast":
        LocalHybridRetriever().build_index(
            all_chunks,
            embeddings,
            model_identity=model_identity,
            index_identity=index_identity,
            db_path=output_dir / "local_index.sqlite",
        )
    else:
        from halyk_agent.adapters.retrieval.postgres.hybrid import (
            PostgresHybridRetriever,
        )

        dsn = os.environ.get("HALYK_POSTGRES_DSN")
        if not dsn:
            raise IndexingError("HALYK_POSTGRES_DSN is required for FULL indexing")
        await PostgresHybridRetriever().build_index(
            all_chunks,
            embeddings,
            model_identity=model_identity,
            index_identity=index_identity,
            dsn=dsn,
        )

    report = IndexReport(
        profile=profile_norm,
        chunk_count=len(all_chunks),
        indexed_lexically=len(all_chunks),
        indexed_vectors=len(embeddings),
        skipped_chunks=skipped_notes,
        failures=failures,
        embedding_model=model_identity,
        index_identity=index_identity,
    )
    _atomic_write_text(output_dir / "index_report.json", _dump_json(report))
    _atomic_write_text(
        output_dir / "retrieval_summary.md",
        "\n".join(
            [
                "# Retrieval index summary",
                "",
                f"- Profile: `{profile_norm}`",
                f"- Chunks: {report.chunk_count}",
                f"- Indexed lexically: {report.indexed_lexically}",
                f"- Indexed vectors: {report.indexed_vectors}",
                f"- Skipped notes: {len(report.skipped_chunks)}",
                f"- Embedding: `{model_identity.model_id}` @ `{model_identity.revision[:12]}`",
                "",
            ]
        ),
    )
    return report
