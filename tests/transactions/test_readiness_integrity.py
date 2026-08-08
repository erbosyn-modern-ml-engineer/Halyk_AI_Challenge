"""Stage 5F selector/definition readiness hash integrity (Stage 6 pre-flight)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from halyk_agent.adapters.transactions.io import (
    TransactionIOError,
    verify_taxonomy_readiness_hashes,
)
from halyk_agent.app.transactions import TransactionServiceError, transactions_from_paths
from halyk_agent.domain.covenants.ast import MetricCategory
from halyk_agent.domain.transaction_taxonomy.models import (
    DefinitionReadinessEntry,
    SelectorCoverageEntry,
    SelectorReadinessStatus,
)


def _smoke_ready() -> Path:
    smoke = Path("work/smoke5f2/transactions")
    if not (smoke / "stage5f_manifest.json").exists():
        pytest.skip("smoke5f2 taxonomy artifacts required")
    return smoke


def _load_coverage(path: Path) -> list[SelectorCoverageEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [SelectorCoverageEntry.model_validate(item) for item in raw]


def _load_readiness(path: Path) -> list[DefinitionReadinessEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [DefinitionReadinessEntry.model_validate(item) for item in raw]


def test_valid_readiness_hashes_verify() -> None:
    smoke = _smoke_ready()
    manifest = json.loads((smoke / "stage5f_manifest.json").read_text(encoding="utf-8"))
    if "selector_coverage_hash" not in manifest:
        pytest.skip("pre-regeneration smoke lacks readiness hashes")
    verify_taxonomy_readiness_hashes(
        taxonomy_manifest=manifest,
        selector_coverage=_load_coverage(smoke / "selector_coverage.json"),
        definition_readiness=_load_readiness(smoke / "definition_readiness.json"),
    )


def test_mutated_p5_group_capex_coverage_detected(tmp_path: Path) -> None:
    smoke = _smoke_ready()
    manifest = json.loads((smoke / "stage5f_manifest.json").read_text(encoding="utf-8"))
    if "selector_coverage_hash" not in manifest:
        pytest.skip("pre-regeneration smoke lacks readiness hashes")
    coverage = _load_coverage(smoke / "selector_coverage.json")
    mutated = False
    out: list[SelectorCoverageEntry] = []
    for entry in coverage:
        if (
            not mutated
            and entry.scenario_id == "P5"
            and entry.category is MetricCategory.GROUP_CAPEX
            and entry.status is SelectorReadinessStatus.UNRESOLVED
        ):
            out.append(entry.model_copy(update={"status": SelectorReadinessStatus.TRUE_ZERO}))
            mutated = True
        else:
            out.append(entry)
    assert mutated
    with pytest.raises(TransactionIOError) as exc:
        verify_taxonomy_readiness_hashes(
            taxonomy_manifest=manifest,
            selector_coverage=out,
            definition_readiness=_load_readiness(smoke / "definition_readiness.json"),
        )
    assert exc.value.code == "TAXONOMY_READINESS_HASH_MISMATCH"


def test_mutated_p6_related_party_coverage_detected() -> None:
    smoke = _smoke_ready()
    manifest = json.loads((smoke / "stage5f_manifest.json").read_text(encoding="utf-8"))
    if "selector_coverage_hash" not in manifest:
        pytest.skip("pre-regeneration smoke lacks readiness hashes")
    coverage = _load_coverage(smoke / "selector_coverage.json")
    mutated = False
    out: list[SelectorCoverageEntry] = []
    for entry in coverage:
        if (
            not mutated
            and entry.scenario_id == "P6"
            and entry.status is SelectorReadinessStatus.UNRESOLVED
        ):
            out.append(entry.model_copy(update={"status": SelectorReadinessStatus.TRUE_ZERO}))
            mutated = True
        else:
            out.append(entry)
    assert mutated
    with pytest.raises(TransactionIOError) as exc:
        verify_taxonomy_readiness_hashes(
            taxonomy_manifest=manifest,
            selector_coverage=out,
            definition_readiness=_load_readiness(smoke / "definition_readiness.json"),
        )
    assert exc.value.code == "TAXONOMY_READINESS_HASH_MISMATCH"


def test_mutated_definition_readiness_detected() -> None:
    smoke = _smoke_ready()
    manifest = json.loads((smoke / "stage5f_manifest.json").read_text(encoding="utf-8"))
    if "definition_readiness_hash" not in manifest:
        pytest.skip("pre-regeneration smoke lacks readiness hashes")
    readiness = _load_readiness(smoke / "definition_readiness.json")
    assert readiness
    mutated = [
        readiness[0].model_copy(
            update={
                "status": (
                    SelectorReadinessStatus.UNRESOLVED
                    if readiness[0].status is SelectorReadinessStatus.READY
                    else SelectorReadinessStatus.READY
                )
            }
        ),
        *readiness[1:],
    ]
    with pytest.raises(TransactionIOError) as exc:
        verify_taxonomy_readiness_hashes(
            taxonomy_manifest=manifest,
            selector_coverage=_load_coverage(smoke / "selector_coverage.json"),
            definition_readiness=mutated,
        )
    assert exc.value.code == "TAXONOMY_READINESS_HASH_MISMATCH"


def test_overwrite_verification_failure_preserves_prior_output(tmp_path: Path) -> None:
    smoke_root = Path("work/smoke5f2")
    if not (smoke_root / "facts" / "fact_extraction_manifest.json").exists():
        pytest.skip("smoke5f2 artifacts required")

    bundle = tmp_path / "bundle"
    for name in ("routing", "covenants", "facts"):
        shutil.copytree(smoke_root / name, bundle / name)

    accepted = bundle / "facts" / "accepted_facts.jsonl"
    lines = accepted.read_text(encoding="utf-8").splitlines()
    mutated: list[str] = []
    changed = False
    for line in lines:
        if not line.strip():
            continue
        obj = json.loads(line)
        payload = obj.get("payload") or {}
        amount = payload.get("amount")
        if (
            not changed
            and isinstance(amount, dict)
            and amount.get("value") is not None
            and obj.get("fact_kind") == "ONE_TIME_ADD_BACK"
        ):
            amount["value"] = "999999.99"
            payload["amount"] = amount
            obj["payload"] = payload
            changed = True
        mutated.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    assert changed
    accepted.write_text("\n".join(mutated) + "\n", encoding="utf-8")

    out = tmp_path / "tx-out"
    out.mkdir()
    marker = out / "stage5f_manifest.json"
    marker.write_text('{"keep":true,"selector_coverage_hash":"x"}\n', encoding="utf-8")
    cov = out / "selector_coverage.json"
    cov.write_text("[]\n", encoding="utf-8")

    with pytest.raises(TransactionServiceError) as exc:
        transactions_from_paths(
            routing_dir=bundle / "routing",
            covenants_dir=bundle / "covenants",
            facts_dir=bundle / "facts",
            ledger_path=Path("agentic-bank-public/master_ledger_2025.csv"),
            output_dir=out,
            overwrite=True,
        )
    assert exc.value.code == "FACT_ARTIFACT_HASH_MISMATCH"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["keep"] is True
    assert cov.read_text(encoding="utf-8") == "[]\n"
