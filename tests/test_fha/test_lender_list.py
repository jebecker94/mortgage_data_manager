"""Tests for the HUD FHA-approved lender list scraper.

The fixture is real HTML trimmed from a Lender List Search results page
(apps.hud.gov/ll/code/getllst.cfm), containing three records that cover the
main parsing branches: a blank-county record, a fully populated multi-line
record, and a record with no areas approved (and therefore no mort_id).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.fha.lender_list import (
    BRONZE_SCHEMA,
    LENDER_LIST_STATES,
    build_bronze_lender_list,
    build_lender_list_params,
    parse_lender_list_html,
    parse_match_count,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lender_list_sample.html"


@pytest.fixture
def sample_html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


class TestBuildParams:
    def test_state_is_uppercased(self):
        params = build_lender_list_params("ca")
        assert params["lndstate"] == "CA"

    def test_matches_official_form_defaults(self):
        params = build_lender_list_params("WY")
        # Hidden constant in the official search form
        assert params["ldrtp"] == "05"
        # All Title and servicer/originator boxes checked = unfiltered pull
        for box in ("pbox1", "pbox2", *(f"sobox{i}" for i in range(1, 7))):
            assert params[box] == "on"

    def test_all_jurisdictions_present(self):
        assert len(LENDER_LIST_STATES) == 54
        assert {"DC", "PR", "VI", "GU"} <= set(LENDER_LIST_STATES)


class TestParseMatchCount:
    def test_count_extracted(self, sample_html):
        assert parse_match_count(sample_html) == 3

    def test_no_match_page_is_zero(self):
        assert parse_match_count("<html>NO Lenders match your criteria</html>") == 0

    def test_error_page_is_none(self):
        assert parse_match_count("<html>Server Error</html>") is None


class TestParseLenderList:
    def test_parses_all_records(self, sample_html):
        records = parse_lender_list_html(sample_html, "WY")
        assert len(records) == 3

    def test_full_record(self, sample_html):
        records = parse_lender_list_html(sample_html, "WY")
        rec = records[1]
        assert rec["lender_name"] == "BUFFALO FEDERAL BANK"
        assert rec["street"] == "714 MAIN ST"
        assert rec["city"] == "EVANSTON"
        assert rec["state"] == "WY"
        assert rec["zip_code"] == "82930"
        assert rec["county"] == "UINTA"
        assert rec["title_type"] == "Title II"
        assert rec["approval_date"] == date(2016, 11, 25)
        assert rec["areas_approved"] == 81
        assert rec["mort_id"] == "5807510019"
        assert rec["mortgagee_id"] == "58075"
        assert rec["branch_id"] == "10019"
        assert rec["ins_type"] == "2"
        assert rec["source_state"] == "WY"

    def test_blank_county_is_null(self, sample_html):
        records = parse_lender_list_html(sample_html, "WY")
        rec = records[0]
        assert rec["lender_name"] == "ADVANTAGE PLUS FEDERAL CREDIT UNION"
        assert rec["county"] is None

    def test_no_areas_record_has_no_mort_id(self, sample_html):
        records = parse_lender_list_html(sample_html, "WY")
        rec = records[2]
        assert rec["lender_name"] == "FIRST NORTHERN BANK OF WYOMING"
        assert rec["areas_approved"] == 0
        assert rec["mort_id"] is None
        assert rec["mortgagee_id"] is None
        # Contact fields still populated on no-areas records
        assert rec["telephone"] == "(307) 682-1195"
        assert rec["fax"] == "(307) 682-3688"
        assert rec["hecm"] is False
        assert rec["originates_203k"] is False

    def test_empty_page_yields_no_records(self):
        assert parse_lender_list_html("<html>NO Lenders match</html>", "WY") == []


class TestBuildBronze:
    def test_bronze_roundtrip(self, sample_html, tmp_path):
        snapshot_dir = tmp_path / "raw" / "20260603"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "lender_list_WY.html").write_text(sample_html, encoding="utf-8")

        out_path = build_bronze_lender_list(
            snapshot_date="20260603",
            raw_dir=tmp_path / "raw",
            bronze_dir=tmp_path / "bronze",
        )

        df = pl.read_parquet(out_path)
        assert len(df) == 3
        assert dict(df.schema) == BRONZE_SCHEMA
        assert df["snapshot_date"].unique().to_list() == [date(2026, 6, 3)]
        # Mortgagee IDs stay zero-padded strings
        ids = df["mortgagee_id"].drop_nulls().to_list()
        assert all(len(i) == 5 for i in ids)

    def test_latest_snapshot_selected(self, sample_html, tmp_path):
        for snap in ("20260101", "20260603"):
            snapshot_dir = tmp_path / "raw" / snap
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "lender_list_WY.html").write_text(sample_html, encoding="utf-8")

        out_path = build_bronze_lender_list(
            raw_dir=tmp_path / "raw",
            bronze_dir=tmp_path / "bronze",
        )
        assert out_path.name == "lender_list_20260603.parquet"

    def test_existing_output_skipped_without_overwrite(self, sample_html, tmp_path):
        snapshot_dir = tmp_path / "raw" / "20260603"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "lender_list_WY.html").write_text(sample_html, encoding="utf-8")

        first = build_bronze_lender_list(
            snapshot_date="20260603",
            raw_dir=tmp_path / "raw",
            bronze_dir=tmp_path / "bronze",
        )
        mtime = first.stat().st_mtime_ns
        second = build_bronze_lender_list(
            snapshot_date="20260603",
            raw_dir=tmp_path / "raw",
            bronze_dir=tmp_path / "bronze",
        )
        assert second == first
        assert second.stat().st_mtime_ns == mtime

    def test_missing_snapshot_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            build_bronze_lender_list(raw_dir=tmp_path / "empty")
