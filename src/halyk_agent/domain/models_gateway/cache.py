"""Disk cache for structured model extraction results (no secrets)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from halyk_agent.domain.fact_extraction.constants import MODEL_CACHE_VERSION
from halyk_agent.domain.ids import deterministic_id, sha256_text
from halyk_agent.domain.models_gateway.types import (
    StructuredExtractionRequest,
    StructuredExtractionResult,
)


def cache_key(
    *,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    requirement_id: str,
    source_sha: str,
    window_hash: str,
    gen_config: dict[str, Any],
) -> str:
    config_hash = sha256_text(json.dumps(gen_config, sort_keys=True, ensure_ascii=False))
    return deterministic_id(
        MODEL_CACHE_VERSION,
        provider,
        model,
        prompt_version,
        schema_version,
        requirement_id,
        source_sha,
        window_hash,
        config_hash,
    )


class DiskExtractionCache:
    """Filesystem cache storing structured payloads only."""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def _path(self, key: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / f"{key}.json"

    def get(
        self,
        key: str,
        *,
        expected: StructuredExtractionRequest,
    ) -> StructuredExtractionResult | None:
        path = self._path(key)
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        meta = data.get("meta") or {}
        if meta.get("requirement_id") != expected.requirement_id:
            return None
        if meta.get("source_sha256") != expected.source_sha256:
            return None
        if meta.get("window_hash") != expected.window_hash:
            return None
        if meta.get("prompt_version") != expected.prompt_version:
            return None
        if meta.get("schema_version") != expected.schema_version:
            return None
        if meta.get("cache_version") != MODEL_CACHE_VERSION:
            return None
        try:
            return StructuredExtractionResult.model_validate(data.get("result"))
        except Exception:
            return None

    def put(
        self,
        key: str,
        *,
        request: StructuredExtractionRequest,
        result: StructuredExtractionResult,
        provider: str,
        model: str,
        gen_config: dict[str, Any] | None = None,
    ) -> None:
        path = self._path(key)
        if path is None:
            return
        # Never persist reasoning_content — strip if a caller accidentally embeds it.
        result_data = result.model_dump(mode="json")
        if isinstance(result_data.get("payload"), dict):
            result_data["payload"].pop("reasoning_content", None)
            result_data["payload"].pop("reasoning", None)
        payload = {
            "meta": {
                "cache_version": MODEL_CACHE_VERSION,
                "provider": provider,
                "model": model,
                "requirement_id": request.requirement_id,
                "source_sha256": request.source_sha256,
                "window_hash": request.window_hash,
                "prompt_version": request.prompt_version,
                "schema_version": request.schema_version,
                "gen_config": gen_config or {},
            },
            "result": result_data,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(path)
