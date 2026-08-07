"""Covenant compilation application service (Stage 5D)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from halyk_agent.adapters.covenants.io import (
    CovenantIOError,
    load_authority_decisions,
    load_authority_manifest_hash,
    write_covenant_outputs,
)
from halyk_agent.adapters.routing.io import load_template_answers
from halyk_agent.app.ocr import load_parsed_documents
from halyk_agent.domain.covenants.engine import run_covenant_compile
from halyk_agent.domain.covenants.models import CovenantReport
from halyk_agent.domain.ids import sha256_text


class CovenantServiceError(Exception):
    def __init__(self, message: str, *, code: str = "COVENANT_ERROR") -> None:
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
        backup_dir = Path(tempfile.mkdtemp(prefix=".covenant-prev-", dir=str(output_dir.parent)))
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


def covenant_from_paths(
    *,
    authority_dir: Path,
    parsed_dir: Path,
    template_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> CovenantReport:
    """
    Application boundary: authority + template + parsed docs → covenant definitions.

    Does not open ground-truth files. Does not call LLMs. Does not calculate actuals.
    """
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise CovenantServiceError(
            f"output directory not empty (use --overwrite): {output_dir}",
            code="OUTPUT_EXISTS",
        )

    authority_dir = authority_dir.resolve()
    parsed_dir = parsed_dir.resolve()
    template_path = template_path.resolve()
    try:
        decisions = load_authority_decisions(authority_dir / "authority_decisions.jsonl")
        authority_hash = load_authority_manifest_hash(authority_dir / "authority_manifest.json")
        template_answers = load_template_answers(template_path)
        _, documents = load_parsed_documents(parsed_dir)
    except CovenantIOError as exc:
        raise CovenantServiceError(exc.message, code=exc.code) from exc
    except Exception as exc:
        raise CovenantServiceError(str(exc), code="COVENANT_INPUT") from exc

    report = run_covenant_compile(
        template_answers=template_answers,
        decisions=decisions,
        documents=tuple(documents),
        authority_manifest_hash=authority_hash,
    )

    stage_dir = Path(tempfile.mkdtemp(prefix=".covenant-stage-", dir=str(output_dir.parent)))
    try:
        write_covenant_outputs(report, stage_dir)
        if output_dir.exists() and any(output_dir.iterdir()):
            _replace_published_outputs(stage_dir, output_dir)
        else:
            _publish_staged(stage_dir, output_dir)
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
    return report


def print_covenant_summary(report: CovenantReport) -> None:
    m = report.manifest
    print("covenant compile complete")
    print(f"scenarios={m.scenario_count}")
    print(f"cells={m.cell_count}")
    print(f"definitions={m.definition_count}")
    print(f"failures={m.failure_count}")
    print(f"authoritative_docs={m.authoritative_covenant_docs}")


def report_to_json(report: CovenantReport) -> str:
    return report.model_dump_json(indent=2) + "\n"


def assert_no_gt_access(path: Path) -> None:
    """Security helper used by tests — Stage 5D must not require GT files."""
    name = path.name.casefold()
    if "ground_truth" in name or name.endswith("answer_key.json"):
        raise CovenantServiceError("ground truth access forbidden in Stage 5D", code="GT_FORBIDDEN")
    _ = sha256_text(json.dumps({"ok": True}))
