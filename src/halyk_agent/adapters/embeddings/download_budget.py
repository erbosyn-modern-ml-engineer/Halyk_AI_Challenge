"""Download budget gates for model prewarm (no silent large downloads)."""

from __future__ import annotations

import os
from typing import Any

from halyk_agent.adapters.embeddings.errors import EmbeddingValidationError
from halyk_agent.adapters.embeddings.model_registry import load_model_lock

# 500 MiB per artifact; 800 MiB stage total (Stage 4.2 policy).
DEFAULT_MAX_AUTO_DOWNLOAD_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_STAGE_TOTAL_BYTES = 800 * 1024 * 1024
APPROVAL_ENV = "HALYK_ALLOW_LARGE_MODEL_DOWNLOAD"

OPTIONAL_LARGE_STATUSES = frozenset({"optional_large_model"})


class LargeModelDownloadBlockedError(EmbeddingValidationError):
    """Raised when a large/optional model would download without approval."""


def _policy_from_lock() -> tuple[int, int]:
    try:
        # load_model_lock returns models list only; read raw for policy.
        import json

        from halyk_agent.adapters.embeddings.model_registry import default_model_lock_path

        payload = json.loads(default_model_lock_path().read_text(encoding="utf-8"))
        policy = payload.get("download_policy") if isinstance(payload, dict) else None
        if isinstance(policy, dict):
            return (
                int(policy.get("max_auto_download_bytes", DEFAULT_MAX_AUTO_DOWNLOAD_BYTES)),
                int(policy.get("max_stage_total_download_bytes", DEFAULT_MAX_STAGE_TOTAL_BYTES)),
            )
    except (OSError, ValueError, TypeError, EmbeddingValidationError):
        pass
    return DEFAULT_MAX_AUTO_DOWNLOAD_BYTES, DEFAULT_MAX_STAGE_TOTAL_BYTES


def large_model_approval_granted(*, explicit_approval: bool = False) -> bool:
    """True when env flag or explicit CLI approval is set."""
    if explicit_approval:
        return True
    raw = os.environ.get(APPROVAL_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def entry_is_optional_large(entry: dict[str, Any]) -> bool:
    status = str(entry.get("status") or "")
    if status in OPTIONAL_LARGE_STATUSES:
        return True
    return entry.get("requires_explicit_user_approval") is True


def approx_download_bytes(entry: dict[str, Any]) -> int | None:
    raw = entry.get("approx_download_bytes")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def assert_prewarm_allowed(
    logical_name: str,
    *,
    explicit_approval: bool = False,
) -> dict[str, Any]:
    """Refuse optional/large model prewarm unless explicitly approved.

    Returns the matching model-lock entry when allowed.
    """
    entries = {str(item["logical_name"]): item for item in load_model_lock()}
    try:
        entry = entries[logical_name]
    except KeyError as exc:
        raise EmbeddingValidationError(f"unknown logical model name {logical_name!r}") from exc

    max_auto, _max_total = _policy_from_lock()
    size = approx_download_bytes(entry)
    is_large = entry_is_optional_large(entry) or (size is not None and size > max_auto)

    if not is_large:
        return entry

    if large_model_approval_granted(explicit_approval=explicit_approval):
        return entry

    model_id = entry.get("repository_or_model_id", logical_name)
    size_msg = (
        f"~{size} bytes" if size is not None else "unknown size (marked optional_large_model)"
    )
    raise LargeModelDownloadBlockedError(
        f"refusing automatic download of {model_id} ({size_msg}). "
        f"Marked optional_large_model / requires_explicit_user_approval / not_preverified. "
        f"Set {APPROVAL_ENV}=1 or pass explicit approval to proceed. "
        f"Per-artifact budget is {max_auto} bytes."
    )


__all__ = [
    "APPROVAL_ENV",
    "DEFAULT_MAX_AUTO_DOWNLOAD_BYTES",
    "DEFAULT_MAX_STAGE_TOTAL_BYTES",
    "LargeModelDownloadBlockedError",
    "assert_prewarm_allowed",
    "entry_is_optional_large",
    "large_model_approval_granted",
]
