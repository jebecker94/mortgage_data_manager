"""Tests for the UMBS raw file-name parser and coverage report."""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.sources.umbs.download import (
    RAW_FEEDS,
    get_manual_download_instructions,
    raw_coverage,
    raw_file_month,
)


def test_raw_file_month() -> None:
    assert raw_file_month("FNM_ILLD_202507.zip") == "2025-07"
    assert raw_file_month("FNM_ILLD_202507_REV.zip") == "2025-07"
    assert raw_file_month("FNM_MF_202108_2.zip") == "2021-08"
    assert raw_file_month("FNM_GN_MEGA_202512.txt") == "2025-12"
    assert raw_file_month("fu261006.zip") == "2026-10"
    assert raw_file_month("fd210806.zip") == "2021-08"
    assert raw_file_month("notes.pdf") is None


def test_raw_coverage_reports_gaps_and_republications(tmp_path: Path) -> None:
    illd = tmp_path / "FNMA" / "FNM_ILLD"
    illd.mkdir(parents=True)
    for name in ("FNM_ILLD_201906.zip", "FNM_ILLD_201908.zip", "FNM_ILLD_201908_REV.zip"):
        (illd / name).touch()

    cov = raw_coverage(tmp_path)
    assert cov.height == len(RAW_FEEDS)
    row = cov.filter(cov["folder"] == "FNM_ILLD").row(0, named=True)
    assert row["files"] == 3
    assert (row["first_month"], row["last_month"]) == ("2019-06", "2019-08")
    assert row["missing_months"] == ["2019-07"]
    assert row["multi_file_months"] == ["2019-08"]
    absent = cov.filter(cov["folder"] == "FU").row(0, named=True)
    assert absent["files"] == 0 and absent["missing_months"] == []


def test_instructions_name_every_feed() -> None:
    text = get_manual_download_instructions()
    for feed in RAW_FEEDS:
        assert f"raw/{feed.gse}/{feed.folder}/" in text
