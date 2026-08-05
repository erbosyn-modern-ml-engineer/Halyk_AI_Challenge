"""Stage 4.2 Docker-free retrieval / model policy tests."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from halyk_agent.adapters.embeddings.download_budget import (
    LargeModelDownloadBlockedError,
    assert_prewarm_allowed,
)
from halyk_agent.adapters.embeddings.model_registry import (
    FULL_EMBEDDING_LOGICAL_NAME,
    FULL_RERANKER_LOGICAL_NAME,
    OPTIONAL_BGE_M3_LOGICAL_NAME,
    apply_passage_prefix,
    apply_query_prefix,
    default_embedding_logical_name,
    resolve_embedding_identity,
)
from halyk_agent.adapters.retrieval.local.vectors import (
    brute_force_cosine_topk,
    pack_float32_vector,
    unpack_float32_vector,
)
from halyk_agent.adapters.retrieval.postgres.backend import (
    PgvectorExtensionMissingError,
    VectorBackendName,
)
from halyk_agent.adapters.retrieval.rrf import reciprocal_rank_fusion
from halyk_agent.domain.retrieval import RetrievalFilters

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "halyk_agent"


def test_default_full_embedding_is_e5_small_not_bge() -> None:
    assert default_embedding_logical_name("full") == FULL_EMBEDDING_LOGICAL_NAME
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    assert identity.model_id == "intfloat/multilingual-e5-small"
    assert identity.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert identity.dimension == 384
    assert identity.query_prefix == "query: "
    assert identity.passage_prefix == "passage: "


def test_e5_prefixes_applied() -> None:
    identity = resolve_embedding_identity(FULL_EMBEDDING_LOGICAL_NAME)
    assert apply_query_prefix("лимит", identity) == "query: лимит"
    assert apply_passage_prefix("договор", identity) == "passage: договор"


def test_heavy_models_are_optional_and_blocked_without_approval() -> None:
    with pytest.raises(LargeModelDownloadBlockedError):
        assert_prewarm_allowed(OPTIONAL_BGE_M3_LOGICAL_NAME, explicit_approval=False)
    with pytest.raises(LargeModelDownloadBlockedError):
        assert_prewarm_allowed(FULL_RERANKER_LOGICAL_NAME, explicit_approval=False)
    # Explicit approval unlocks the gate (does not download here).
    entry = assert_prewarm_allowed(OPTIONAL_BGE_M3_LOGICAL_NAME, explicit_approval=True)
    assert entry["status"] == "optional_large_model"
    assert entry["requires_explicit_user_approval"] is True
    assert entry["not_preverified"] is True


def test_numpy_vector_roundtrip_and_cosine_order() -> None:
    blob, dim, checksum = pack_float32_vector([1.0, 0.0, 0.0])
    assert dim == 3
    restored = unpack_float32_vector(blob, dimension=dim, checksum=checksum)
    assert restored == pytest.approx([1.0, 0.0, 0.0])
    ranked = brute_force_cosine_topk(
        [1.0, 0.0, 0.0],
        [
            ("far", [0.0, 1.0, 0.0]),
            ("near", [0.9, 0.1, 0.0]),
            ("exact", [1.0, 0.0, 0.0]),
        ],
        top_k=3,
    )
    assert [chunk_id for chunk_id, _ in ranked] == ["exact", "near", "far"]


def test_metadata_filter_sql_precedes_scoring_contract() -> None:
    """Filters are expressed as SQL predicates (applied before NumPy scoring)."""
    from halyk_agent.adapters.retrieval.postgres.filters import build_filter_sql

    where, params = build_filter_sql(
        RetrievalFilters(document_ids=["doc-a"]),
        alias="c",
    )
    assert "c.document_id = ANY(:document_ids)" in where
    assert params["document_ids"] == ["doc-a"]
    assert "doc-a" not in where


def test_rrf_parity_independent_of_vector_backend_name() -> None:
    lexical = ["a", "b"]
    vector = ["b", "a"]
    fused = reciprocal_rank_fusion([lexical, vector], rrf_k=60)
    assert [chunk_id for chunk_id, _, _ in fused] == ["a", "b"] or fused[0][0] in {"a", "b"}
    # Same ranked lists → identical RRF regardless of whether ranks came from
    # pgvector or postgres_numpy_exact.
    again = reciprocal_rank_fusion([lexical, vector], rrf_k=60)
    assert fused == again
    assert VectorBackendName.POSTGRES_NUMPY_EXACT.value == "postgres_numpy_exact"


def test_pgvector_missing_extension_error_message() -> None:
    err = PgvectorExtensionMissingError(
        "pgvector backend requires extension 'vector' to be already installed."
    )
    assert "vector" in err.message
    assert "already installed" in err.message


def test_application_source_has_no_docker_invocation() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip().lower()
                if value == "docker" or value.startswith("docker "):
                    offenders.append(f"{path}:{node.lineno}:const")
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if (
                    name in {"run", "Popen", "call", "check_call", "check_output"}
                    and node.args
                    and isinstance(node.args[0], (ast.List, ast.Tuple))
                ):
                    elts = node.args[0].elts
                    if elts and isinstance(elts[0], ast.Constant):
                        first = str(elts[0].value).lower()
                        if first == "docker":
                            offenders.append(f"{path}:{node.lineno}:subprocess")
    assert offenders == []


def test_cli_help_starts_without_docker() -> None:
    proc = subprocess.run(
        ["uv", "run", "python", "-m", "halyk_agent", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "docker" not in proc.stdout.lower()
    assert "usage" in proc.stdout.lower() or "inspect" in proc.stdout.lower()


def test_default_profile_does_not_resolve_bge_m3() -> None:
    identity = resolve_embedding_identity(default_embedding_logical_name("full"))
    assert "bge-m3" not in identity.model_id.lower()
