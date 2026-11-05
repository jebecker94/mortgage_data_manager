"""Regression tests: a failed UMBS silver month is counted and never published."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from mortgage_data_manager.sources.umbs import cli
from mortgage_data_manager.sources.umbs.import_silver import run_silver_import


def _bronze(root: Path, gse: str, folder: str, name: str, frame: pl.DataFrame | None) -> None:
    out = root / gse / folder
    out.mkdir(parents=True, exist_ok=True)
    if frame is None:
        (out / name).write_bytes(b"not parquet")
    else:
        frame.write_parquet(out / name)


def test_correction_pair_failure_is_counted(tmp_path: Path) -> None:
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    good = pl.DataFrame({"Loan Identifier": ["1", "2"], "Channel": ["R", "9"]})
    _bronze(bronze, "FNMA", "FNM_ILLD", "FNM_ILLD_202001.parquet", good)
    _bronze(bronze, "FNMA", "FNM_ILLD", "FNM_ILLD_202002.parquet", None)

    results = run_silver_import(bronze, silver, gse="FNMA", kinds=["ILLD"])

    counts = results["FNMA/ILLD"]
    assert counts["wrote"] == 1
    assert counts["failed"] == 1
    out_dir = silver / "FNMA" / "ILLD"
    assert sorted(p.name for p in out_dir.iterdir()) == ["ILLD_202001.parquet"]


def test_perf_kind_failure_is_counted(tmp_path: Path) -> None:
    bronze, silver = tmp_path / "bronze", tmp_path / "silver"
    _bronze(bronze, "FNMA", "FNM_MLLD", "FNM_MLLD_202001.parquet", None)

    results = run_silver_import(bronze, silver, gse="FNMA", kinds=["MLLD"])

    assert results["FNMA/MLLD"] == {"months": 1, "skipped_existing": 0, "wrote": 0, "failed": 1}
    # Nothing under the final name and no temp file left behind.
    assert list((silver / "FNMA" / "MLLD").iterdir()) == []


def test_silver_cli_exits_nonzero_on_failure(monkeypatch) -> None:
    def fake_run_silver(**_kwargs) -> dict[str, dict[str, int]]:
        return {"FNMA/ILLD": {"wrote": 3, "skipped_existing": 0, "missing_pair": 0, "failed": 1}}

    monkeypatch.setattr(cli, "run_silver", fake_run_silver)
    result = CliRunner().invoke(cli.app, ["silver"])
    assert result.exit_code == 1
    assert "failed 1" in result.output
