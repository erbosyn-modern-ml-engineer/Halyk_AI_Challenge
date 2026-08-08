"""Stage 7 composition must not poison the final output directory with preflight files."""

from __future__ import annotations

from pathlib import Path

from halyk_agent.app import solve as solve_app


def test_raw_dataset_preflight_uses_temporary_sibling(tmp_path: Path, monkeypatch) -> None:
    dataset = tmp_path / "dataset"
    output = tmp_path / "result"
    dataset.mkdir()
    sentinel = object()
    observed: dict[str, Path] = {}

    def fake_preflight(dataset_arg: Path, preflight_dir: Path):
        assert dataset_arg == dataset
        observed["preflight"] = preflight_dir
        (preflight_dir / "sanitized_manifest.json").write_text("{}", encoding="utf-8")
        return sentinel

    def fake_solve(manifest, output_arg: Path, **_kwargs):
        assert manifest is sentinel
        assert output_arg == output
        assert not output_arg.exists() or not any(output_arg.iterdir())
        return {"submission": "ok", "run_id": "fixed"}

    monkeypatch.setattr(solve_app, "run_preflight", fake_preflight)
    monkeypatch.setattr(solve_app, "run_solve_from_manifest", fake_solve)
    result = solve_app.run_solve(dataset, output)
    assert result["submission"] == "ok"
    assert observed["preflight"].parent == output.parent
    assert not observed["preflight"].exists()
