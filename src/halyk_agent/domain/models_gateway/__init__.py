"""Stage 5E structured model gateway."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ExtractionState",
    "LlmGatewayConfig",
    "ModelCallRecord",
    "ProviderName",
    "StructuredExtractionRequest",
    "StructuredExtractionResult",
    "StructuredModelGateway",
]


def __getattr__(name: str) -> Any:
    if name in {
        "ExtractionState",
        "ModelCallRecord",
        "ProviderName",
        "StructuredExtractionRequest",
        "StructuredExtractionResult",
    }:
        from halyk_agent.domain.models_gateway import types as _types

        return getattr(_types, name)
    if name in {"LlmGatewayConfig", "StructuredModelGateway"}:
        from halyk_agent.domain.models_gateway import gateway as _gateway

        return getattr(_gateway, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
