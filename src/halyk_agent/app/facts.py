"""Fact extraction application service (Stage 5E)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from halyk_agent.adapters.covenants.io import CovenantIOError
from halyk_agent.adapters.facts.io import (
    FactIOError,
    definitions_file_hash,
    load_authority_decisions,
    load_authority_manifest_hash,
    load_covenant_definitions,
    write_fact_outputs,
)
from halyk_agent.adapters.routing.io import load_ledger_csv
from halyk_agent.app.ocr import load_parsed_documents
from halyk_agent.config import Settings, get_settings
from halyk_agent.domain.fact_extraction.engine import run_fact_extraction
from halyk_agent.domain.fact_extraction.models import FactExtractionReport
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.models_gateway.gateway import LlmGatewayConfig, StructuredModelGateway
from halyk_agent.domain.models_gateway.types import ProviderName
from halyk_agent.domain.routing.models import LedgerRow


class FactServiceError(Exception):
    def __init__(self, message: str, *, code: str = "FACT_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _publish_staged(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(stage_dir.iterdir()):
        dest = output_dir / path.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        os.replace(path, dest)


def _replace_published_outputs(stage_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    existing = list(output_dir.iterdir())
    if existing:
        backup_dir = Path(tempfile.mkdtemp(prefix=".facts-prev-", dir=str(output_dir.parent)))
        for child in existing:
            os.replace(child, backup_dir / child.name)
    try:
        _publish_staged(stage_dir, output_dir)
    except Exception:
        if backup_dir is not None:
            for child in list(output_dir.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in backup_dir.iterdir():
                os.replace(child, output_dir / child.name)
        raise
    finally:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def assert_no_gt_access(path: Path) -> None:
    """Security helper — Stage 5E must not require GT files."""
    name = path.name.casefold()
    if "ground_truth" in name or name.endswith("answer_key.json"):
        raise FactServiceError(
            "ground truth access forbidden in Stage 5E",
            code="GT_FORBIDDEN",
        )
    _ = sha256_text(json.dumps({"ok": True}))


def _build_gateway(
    *,
    allow_network_models: bool,
    settings: Settings,
    cache_dir: Path | None,
) -> StructuredModelGateway | None:
    if not allow_network_models:
        return None
    max_external = settings.llm_max_external_attempts
    if settings.llm_max_calls is not None:
        max_external = settings.llm_max_calls
    max_thinking = settings.llm_max_thinking_escalations
    if settings.llm_escalation_max_calls is not None:
        max_thinking = settings.llm_escalation_max_calls
    cfg = LlmGatewayConfig(
        primary_provider=settings.llm_primary_provider,
        primary_model=settings.llm_primary_model,
        escalation_provider=settings.llm_escalation_provider,
        escalation_model=settings.llm_escalation_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_external_attempts=max_external,
        max_thinking_escalations=max_thinking,
        max_concurrency=settings.llm_max_concurrency,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        provider_revision=settings.llm_provider_revision,
        cache_dir=cache_dir,
        allow_network=True,
    )
    return StructuredModelGateway(config=cfg)


def facts_from_paths(
    *,
    authority_dir: Path,
    covenants_dir: Path,
    parsed_dir: Path,
    output_dir: Path,
    ledger_path: Path | None = None,
    overwrite: bool = False,
    allow_network_models: bool = False,
    settings: Settings | None = None,
) -> FactExtractionReport:
    """
    Application boundary: authority + covenants + parsed docs → fact extraction.

    Does not open ground-truth files. Does not mutate ledgers. Does not compute actuals.
    """
    settings = settings or get_settings()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FactServiceError(
            f"output directory not empty (use --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    for path in (authority_dir, covenants_dir, parsed_dir):
        assert_no_gt_access(path)
    if ledger_path is not None:
        assert_no_gt_access(ledger_path)

    authority_dir = authority_dir.resolve()
    covenants_dir = covenants_dir.resolve()
    parsed_dir = parsed_dir.resolve()

    definitions_path = covenants_dir / "covenant_definitions.jsonl"
    if not definitions_path.is_file():
        # Allow passing the file itself
        if covenants_dir.is_file():
            definitions_path = covenants_dir
        else:
            raise FactServiceError(
                f"covenant_definitions.jsonl missing under {covenants_dir}",
                code="MISSING_DEFINITIONS",
            )

    try:
        decisions = load_authority_decisions(authority_dir / "authority_decisions.jsonl")
        authority_hash = load_authority_manifest_hash(authority_dir / "authority_manifest.json")
        definitions = load_covenant_definitions(definitions_path)
        definitions_hash = definitions_file_hash(definitions_path)
        _, documents = load_parsed_documents(parsed_dir)
    except (FactIOError, CovenantIOError) as exc:
        raise FactServiceError(exc.message, code=getattr(exc, "code", "FACT_INPUT")) from exc
    except Exception as exc:
        raise FactServiceError(str(exc), code="FACT_INPUT") from exc

    ledger_rows: tuple[LedgerRow, ...] | None = None
    if ledger_path is not None:
        ledger_path = ledger_path.resolve()
        if not ledger_path.is_file():
            raise FactServiceError(f"ledger missing: {ledger_path}", code="MISSING_LEDGER")
        ledger_rows = load_ledger_csv(ledger_path)

    cache_dir = output_dir / ".model_cache" if allow_network_models else None
    gateway = _build_gateway(
        allow_network_models=allow_network_models,
        settings=settings,
        cache_dir=cache_dir,
    )

    report = run_fact_extraction(
        definitions=definitions,
        decisions=decisions,
        documents=tuple(documents),
        ledger_rows=ledger_rows,
        allow_network_models=allow_network_models,
        model_gateway=gateway,
        authority_manifest_hash=authority_hash,
        covenant_definitions_hash=definitions_hash,
    )

    stage_parent = output_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".facts-stage-", dir=str(stage_parent)))
    try:
        write_fact_outputs(report, stage_dir)
        if output_dir.exists() and any(output_dir.iterdir()):
            _replace_published_outputs(stage_dir, output_dir)
        else:
            _publish_staged(stage_dir, output_dir)
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
    return report


def print_facts_summary(report: FactExtractionReport) -> None:
    m = report.manifest
    print("fact extraction complete")
    print(f"scenarios={m.scenario_count}")
    print(f"requirements={m.requirement_count}")
    print(f"semantic_required={m.semantic_required_count}")
    print(f"source_triggered={m.source_triggered_count}")
    print(f"speculative={m.speculative_count}")
    print(f"candidates={m.candidate_count}")
    print(f"accepted={m.accepted_count}")
    print(f"rejected={m.rejected_count}")
    print(f"unresolved={m.unresolved_count}")
    print(f"needs_model={m.needs_model_count}")
    print(f"confirmed_none={m.confirmed_none_count}")
    print(f"conflicts={m.conflict_count}")
    print(f"model_calls={m.model_call_count}")
    print(f"deterministic_accepted={m.deterministic_accepted_count}")
    print(f"llm_accepted={m.llm_accepted_count}")


def report_to_json(report: FactExtractionReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def probe_models(*, allow_network: bool, settings: Settings | None = None) -> dict[str, object]:
    """Probe configured LLM providers without calling HTTP unless allow_network is set.

    Even with allow_network, probe itself never performs HTTP — it only reports readiness.
    """
    settings = settings or get_settings()
    gateway = StructuredModelGateway(
        config=LlmGatewayConfig(
            primary_provider=settings.llm_primary_provider,
            primary_model=settings.llm_primary_model,
            escalation_provider=settings.llm_escalation_provider,
            escalation_model=settings.llm_escalation_model,
            allow_network=allow_network,
        )
    )
    result = gateway.probe()
    result["primary_provider_enum_ok"] = settings.llm_primary_provider in {
        p.value for p in ProviderName
    }
    return result
