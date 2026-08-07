"""Public smoke: deterministic fact extract when Stage 5C/5D/5A artifacts exist."""

from __future__ import annotations

from pathlib import Path

import pytest

from halyk_agent.app.facts import facts_from_paths
from halyk_agent.domain.fact_extraction.models import FactValidatorStatus

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "work" / "smoke5c1" / "authority"
PARSED = ROOT / "work" / "smoke541" / "ocr-enriched"
COVENANTS_CANDIDATES = (
    ROOT / "work" / "smoke5d" / "covenants-polarity",
    ROOT / "work" / "smoke5d" / "covenants-modifier-closure",
    ROOT / "work" / "smoke5d" / "covenants-acceptance",
    ROOT / "work" / "smoke5d" / "covenants",
)


def _covenants_dir() -> Path | None:
    for path in COVENANTS_CANDIDATES:
        if (path / "covenant_definitions.jsonl").is_file():
            return path
    return None


@pytest.mark.skipif(
    not AUTHORITY.is_dir() or not PARSED.is_dir() or _covenants_dir() is None,
    reason="public smoke artifacts missing",
)
def test_public_smoke_deterministic(tmp_path: Path) -> None:
    covenants = _covenants_dir()
    assert covenants is not None
    out = tmp_path / "facts"
    report = facts_from_paths(
        authority_dir=AUTHORITY,
        covenants_dir=covenants,
        parsed_dir=PARSED,
        output_dir=out,
        overwrite=True,
        allow_network_models=False,
    )
    # Requirements must not be silently empty when modifiers/selectors exist.
    assert report.manifest.requirement_count > 0
    # No accepted fact without evidence spans.
    for fact in report.accepted_facts:
        assert fact.validator_status is FactValidatorStatus.ACCEPTED
        assert fact.evidence_span_ids
    # GT reads: none (no ground_truth path used). Manifest proves offline path.
    assert report.manifest.allow_network_models is False
    assert report.manifest.model_call_count == 0
    assert (out / "fact_extraction_manifest.json").is_file()
    assert (out / "accepted_facts.jsonl").is_file()
