"""Regression tests: a failed FHA snapshot or sheet is counted, never dropped silently."""

from __future__ import annotations

from pathlib import Path

import pytest

from mortgage_data_manager.sources.fha import pipeline
from mortgage_data_manager.sources.fha.import_bronze import (
    _read_sf_frames,
    convert_fha_sf_snapshots,
)


class _OneBadSheetReader:
    """A workbook whose second loan-level sheet cannot be loaded."""

    sheet_names = ["Purchase", "Refinance"]

    def load_sheet(self, sheet: str):
        raise ValueError(f"cannot load {sheet}")


def test_failed_sheet_fails_the_snapshot() -> None:
    with pytest.raises(ValueError, match="'Purchase'"):
        _read_sf_frames(_OneBadSheetReader())  # type: ignore[arg-type]


def test_unreadable_workbook_is_counted(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "fha_sf_snapshot_20250101.xlsx").write_bytes(b"not a workbook")
    counts = convert_fha_sf_snapshots(raw, tmp_path / "bronze")
    assert counts == {"converted": 0, "skipped": 0, "failed": 1}
    assert not (tmp_path / "bronze" / "fha_sf_snapshot_20250101.parquet").exists()


def test_run_bronze_reports_the_dataset_failed(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "single_family"
    raw.mkdir(parents=True)
    (raw / "fha_sf_snapshot_20250101.xlsx").write_bytes(b"not a workbook")
    counts = pipeline.run_bronze(
        ["single-family"], raw_root=tmp_path / "raw", bronze_root=tmp_path / "bronze"
    )
    assert counts == {"processed": 1, "successful": 0, "failed": 1}
