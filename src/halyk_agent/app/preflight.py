"""Application wrapper for dataset preflight / quarantine."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.preflight.models import SanitizedDatasetManifest
from halyk_agent.preflight.service import run_preflight


def run_dataset_preflight(dataset: Path, output: Path) -> SanitizedDatasetManifest:
    return run_preflight(dataset, output)
