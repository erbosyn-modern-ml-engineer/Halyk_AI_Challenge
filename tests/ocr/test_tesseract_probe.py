"""Stage 5A.4.1 — Tesseract probe / tessdata discovery regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from halyk_agent.adapters.ocr.probe import (
    discover_tessdata_dir,
    languages_from_tessdata_dir,
    normalize_language_token,
    parse_list_langs_output,
    probe_tesseract_cli,
)
from halyk_agent.domain.ocr import OcrBackendKind


def _fake_install(tmp_path: Path, *, langs: tuple[str, ...] = ("eng", "rus", "kaz", "osd")) -> Path:
    root = tmp_path / "Programs" / "Tesseract-OCR"
    root.mkdir(parents=True)
    exe = root / "tesseract.exe"
    exe.write_bytes(b"MZ")
    tessdata = root / "tessdata"
    tessdata.mkdir()
    for lang in langs:
        (tessdata / f"{lang}.traineddata").write_bytes(b"data")
    return exe


def test_discover_tessdata_beside_arbitrary_user_exe(tmp_path: Path) -> None:
    exe = _fake_install(tmp_path)
    found = discover_tessdata_dir(str(exe))
    assert found is not None
    assert found == (exe.parent / "tessdata").resolve()
    langs = languages_from_tessdata_dir(found)
    assert set(langs) >= {"eng", "rus", "kaz"}


def test_no_program_files_assumption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _fake_install(tmp_path)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    found = discover_tessdata_dir(str(exe))
    assert found is not None
    assert "Program Files" not in str(found)


def test_parse_list_langs_ignores_header_and_parses_lines() -> None:
    stdout = (
        'List of available languages in "C:\\Apps\\Tesseract-OCR/tessdata/" (4):\n'
        "eng\n"
        "kaz\n"
        "osd\n"
        "rus\n"
    )
    assert parse_list_langs_output(stdout, "") == ["eng", "kaz", "osd", "rus"]


def test_slash_normalization_of_path_like_lang_entries() -> None:
    assert normalize_language_token("tessdata/eng") == "eng"
    assert normalize_language_token(r"tessdata\eng") == "eng"
    assert normalize_language_token("/path/tessdata/eng") == "eng"
    assert normalize_language_token('List of available languages in "x" (4):') is None


def test_invalid_tessdata_prefix_does_not_hide_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _fake_install(tmp_path)
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path / "missing-prefix"))
    found = discover_tessdata_dir(str(exe))
    assert found == (exe.parent / "tessdata").resolve()


def test_unset_tessdata_prefix_uses_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _fake_install(tmp_path)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    found = discover_tessdata_dir(str(exe))
    assert found is not None
    assert "eng" in languages_from_tessdata_dir(found)


def test_missing_kaz_not_offline_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _fake_install(tmp_path, langs=("eng", "rus", "osd"))
    monkeypatch.setattr(
        "halyk_agent.adapters.ocr.probe.discover_tesseract_executable",
        lambda: str(exe),
    )

    def _fake_run(cmd: list[str], *, timeout: float = 5.0, env=None):  # type: ignore[no-untyped-def]
        if "--version" in cmd:
            return 0, "tesseract v5.4.0.20240606\n", ""
        if "--list-langs" in cmd:
            return 0, "List of available languages (3):\neng\nrus\nosd\n", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("halyk_agent.adapters.ocr.probe._run_bounded", _fake_run)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    avail = probe_tesseract_cli()
    assert avail.kind is OcrBackendKind.TESSERACT_CLI
    assert avail.installed is True
    assert avail.offline_ready is False
    assert "kaz" in avail.missing_languages


def test_subprocess_failure_no_fake_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = _fake_install(tmp_path)
    monkeypatch.setattr(
        "halyk_agent.adapters.ocr.probe.discover_tesseract_executable",
        lambda: str(exe),
    )

    def _fake_run(cmd: list[str], *, timeout: float = 5.0, env=None):  # type: ignore[no-untyped-def]
        if "--version" in cmd:
            return 1, "", "boom"
        return 1, "", "boom"

    monkeypatch.setattr("halyk_agent.adapters.ocr.probe._run_bounded", _fake_run)
    avail = probe_tesseract_cli()
    assert avail.offline_ready is False
    assert "tesseract_version" in avail.missing_components
    assert any("version failed" in n for n in avail.notes)


def test_probe_does_not_force_empty_tessdata_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: empty TESSDATA_PREFIX made Windows Tesseract report 0 langs."""
    exe = _fake_install(tmp_path)
    monkeypatch.setattr(
        "halyk_agent.adapters.ocr.probe.discover_tesseract_executable",
        lambda: str(exe),
    )
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    seen_env: dict[str, str] | None = None

    def _fake_run(cmd: list[str], *, timeout: float = 5.0, env=None):  # type: ignore[no-untyped-def]
        nonlocal seen_env
        seen_env = env
        if "--version" in cmd:
            return 0, "tesseract v5.4.0.20240606\n", ""
        return 0, "List of available languages (4):\neng\nkaz\nosd\nrus\n", ""

    monkeypatch.setattr("halyk_agent.adapters.ocr.probe._run_bounded", _fake_run)
    avail = probe_tesseract_cli()
    assert seen_env is not None
    assert "TESSDATA_PREFIX" not in seen_env or seen_env.get("TESSDATA_PREFIX", "").strip()
    assert avail.offline_ready is True
    assert set(avail.missing_languages) == set()
    assert set(avail.installed_languages) >= {"eng", "rus", "kaz"}
