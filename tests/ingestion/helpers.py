"""Helpers for building synthetic competition archives in tests."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from openpyxl import Workbook


def write_zip(path: Path, files: dict[str, bytes | str]) -> Path:
    """Create a ZIP archive from a mapping of member path -> content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return path


def write_zip_with_info(path: Path, infos: list[tuple[zipfile.ZipInfo, bytes]]) -> Path:
    """Create a ZIP with fully controlled ZipInfo metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for info, data in infos:
            zf.writestr(info, data)
    return path


def make_xlsx_bytes(
    sheets: dict[str, list[list[object]]],
) -> bytes:
    """Create an in-memory XLSX workbook."""
    workbook = Workbook()
    default = workbook.active
    assert default is not None
    workbook.remove(default)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def sample_transactions_csv() -> str:
    return (
        "transaction_id,amount,currency,status,occurred_at,contract_id,invoice_id\n"
        "txn-1,10.50,KZT,posted,2024-01-01T00:00:00Z,c-1,inv-1\n"
        "txn-2,20.00,KZT,posted,2024-01-02T00:00:00Z,c-1,inv-2\n"
    )


def sample_submission_json() -> str:
    return json.dumps(
        {
            "submission": {
                "case_id": "case-1",
                "answer": "APPROVE",
                "prediction": {"result": "ok"},
            }
        },
        indent=2,
    )


def sample_scoring_json() -> str:
    return json.dumps(
        {
            "evaluation": {
                "scoring": True,
                "metric": "f1",
                "rules": ["r1"],
                "criteria": ["c1"],
            }
        }
    )
