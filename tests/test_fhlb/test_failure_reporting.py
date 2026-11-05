"""Regression tests: a failed FHLB year is counted as failed, never as skipped."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mortgage_data_manager.sources.fhlb import cli, import_bronze
from mortgage_data_manager.sources.fhlb.import_bronze import import_ama_bronze
from mortgage_data_manager.sources.fhlb.import_silver import import_ama_silver


def test_bronze_build_error_is_failed_not_skipped(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "2020-pudb.csv").write_text("x\n1\n")
    monkeypatch.setattr(import_bronze, "validate_against_raw", lambda *_a: [])

    def boom(*_args, **_kwargs):
        raise ValueError("injected")

    monkeypatch.setattr(import_bronze, "transform_named_canonical", boom)
    counts = import_ama_bronze([2020], tmp_path, tmp_path / "bronze")
    assert counts == {"loaded": 0, "skipped": 0, "failed": 1}
    assert not (tmp_path / "bronze" / "fhlb_ama_2020.parquet").exists()


def test_bronze_schema_mismatch_is_failed(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "2020-pudb.csv").write_text("x\n1\n")
    monkeypatch.setattr(import_bronze, "validate_against_raw", lambda *_a: ["new column"])
    counts = import_ama_bronze([2020], tmp_path, tmp_path / "bronze")
    assert counts == {"loaded": 0, "skipped": 0, "failed": 1}


def test_silver_unreadable_bronze_is_failed(tmp_path: Path) -> None:
    (tmp_path / "fhlb_ama_2020.parquet").write_bytes(b"not parquet")
    counts = import_ama_silver([2020, 2021], tmp_path, tmp_path / "silver")
    assert counts == {"loaded": 0, "skipped": 1, "failed": 1}


def test_cli_exits_nonzero_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "run_bronze", lambda *_a, **_k: {"ama": {"loaded": 3, "skipped": 0, "failed": 1}}
    )
    result = CliRunner().invoke(cli.app, ["bronze", "-t", "ama"])
    assert result.exit_code == 1
    assert "failed 1" in result.output
