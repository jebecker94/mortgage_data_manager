"""Regression tests: a failed FHFA dataset-year is counted, never reported as written."""

from __future__ import annotations

import zipfile
from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.fhfa import import_bronze
from mortgage_data_manager.sources.fhfa.import_bronze import save_to_bronze
from mortgage_data_manager.sources.fhfa.import_silver import save_to_silver


def test_bronze_member_failure_fails_the_year(tmp_path: Path, monkeypatch) -> None:
    # One GSE's member parses, the other raises: the year must fail as a whole
    # rather than write the surviving GSE alone under the final name.
    with zipfile.ZipFile(tmp_path / "2010_enterprise_pudb.zip", "w") as zf:
        zf.writestr("fnma_sf2010c_loans.txt", "x")
        zf.writestr("fhlmc_sf2010c_loans.txt", "x")
    calls = iter([pl.DataFrame({"a": [1]}), ValueError("bad member")])

    def fake_load(*_args, **_kwargs) -> pl.DataFrame:
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(import_bronze, "load_fixed_width_with_master_from_buffer", fake_load)
    counts = save_to_bronze("sf_c", [2010], raw_dir=tmp_path, output_dir=tmp_path / "bronze")
    assert counts == {"written": 0, "skipped": 0, "missing": 0, "failed": 1}
    assert not (tmp_path / "bronze" / "sf_c" / "sf_c_2010.parquet").exists()


def test_bronze_corrupt_csv_zip_is_counted(tmp_path: Path) -> None:
    (tmp_path / "2025_pudb_sf_ctf.zip").write_bytes(b"not a zip")
    counts = save_to_bronze("sf_c", [2025, 2026], raw_dir=tmp_path, output_dir=tmp_path / "b")
    assert counts == {"written": 0, "skipped": 0, "missing": 1, "failed": 1}


def test_silver_unreadable_bronze_is_counted(tmp_path: Path) -> None:
    bronze = tmp_path / "bronze"
    bronze.mkdir()
    (bronze / "sf_c_2020.parquet").write_bytes(b"not parquet")
    counts = save_to_silver("sf_c", [2020], bronze_dir=bronze, silver_dir=tmp_path / "silver")
    assert counts == {"written": 0, "skipped": 0, "missing": 0, "failed": 1}
    assert not (tmp_path / "silver" / "sf_c_2020.parquet").exists()


def test_unknown_dataset_still_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown dataset"):
        save_to_silver("nope", [2020], bronze_dir=tmp_path, silver_dir=tmp_path)
