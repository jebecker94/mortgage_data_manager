"""Regression: HMDA loans silver skips partitions on disk and overwrites a year atomically.

The loans builders used to have no skip guard, and ``--overwrite`` deleted the whole
activity year before the new one was written, so a failed rebuild left the year
missing (or holding only the releases written before the failure). These tests drive
the year writer directly with injected failures: the previous year must survive any
failure byte for byte, with no temp tree left behind.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.hmda import import_silver
from mortgage_data_manager.sources.hmda.import_silver import _write_year

#: A dataset with no pinned row counts, so the manifest check is a no-op unless patched.
_DATASET = "unpinned_test_dataset"


def _frame(year: int, file_type: str, value: int) -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "activity_year": pl.Series([year, year], dtype=pl.Int16),
            "file_type": [file_type, file_type],
            "value": [value, value + 1],
        }
    )


def _year(tmp_path: Path, *file_types: str) -> Path:
    """An existing silver year 2020 holding one old file per release."""
    root = tmp_path / "post2018"
    for ft in file_types:
        part = root / "activity_year=2020" / f"file_type={ft}"
        part.mkdir(parents=True)
        _frame(2020, ft, 0).collect().write_parquet(part / "00000000.parquet")
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_failed_overwrite_leaves_previous_year_intact(tmp_path, monkeypatch):
    root = _year(tmp_path, "a", "c")
    before = _snapshot(root)

    def short_write(dataset, year, file_type, actual, source):
        if file_type == "c":  # the second release fails its row-count check
            raise ValueError("truncated write")

    monkeypatch.setattr(import_silver, "check_expected_row_count", short_write)
    frames = [_frame(2020, "a", 100), _frame(2020, "c", 200)]
    with pytest.raises(ValueError, match="truncated"):
        _write_year(frames, root, 2020, _DATASET, overwrite=True)

    assert _snapshot(root) == before
    assert [p.name for p in root.iterdir()] == ["activity_year=2020"]


def test_failed_sink_mid_write_leaves_previous_year_intact(tmp_path):
    root = _year(tmp_path, "a")
    before = _snapshot(root)

    def boom(s: pl.Series) -> pl.Series:
        raise RuntimeError("disk full")

    broken = _frame(2020, "a", 100).with_columns(
        pl.col("value").map_batches(boom, return_dtype=pl.Int64)
    )
    with pytest.raises(Exception, match="disk full"):
        _write_year([broken], root, 2020, _DATASET, overwrite=True)
    assert _snapshot(root) == before


def test_overwrite_replaces_year_and_drops_release_without_bronze(tmp_path):
    root = _year(tmp_path, "a", "c")
    written = _write_year([_frame(2020, "a", 100)], root, 2020, _DATASET, overwrite=True)
    assert written == 1
    year_dir = root / "activity_year=2020"
    assert [p.name for p in year_dir.iterdir()] == ["file_type=a"]
    assert pl.read_parquet(year_dir / "file_type=a" / "00000000.parquet")["value"].to_list() == [
        100,
        101,
    ]


def test_without_overwrite_existing_partition_is_skipped(tmp_path):
    root = _year(tmp_path, "a")
    old = (root / "activity_year=2020" / "file_type=a" / "00000000.parquet").read_bytes()
    frames = [_frame(2020, "a", 100), _frame(2020, "c", 200)]
    assert _write_year(frames, root, 2020, _DATASET, overwrite=False) == 1
    year_dir = root / "activity_year=2020"
    assert (year_dir / "file_type=a" / "00000000.parquet").read_bytes() == old
    assert pl.read_parquet(year_dir / "file_type=c" / "00000000.parquet")["value"].to_list() == [
        200,
        201,
    ]


def test_rows_of_another_year_raise_instead_of_landing_elsewhere(tmp_path):
    root = tmp_path / "post2018"
    with pytest.raises(ValueError, match="outside activity_year=2020"):
        _write_year([_frame(2021, "a", 1)], root, 2020, _DATASET, overwrite=False)
    assert not root.exists() or not any(root.rglob("*.parquet"))
