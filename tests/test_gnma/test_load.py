"""Tests for the GNMA loan-level loaders: feed routing at the cutovers, program, dedup."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.gnma import load

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "gnma"


@pytest.fixture(autouse=True)
def _repo_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another test reloads the config module with repointed environment variables.
    monkeypatch.setattr(load.GNMAConfig, "GNMA_CANONICAL_DIR", SCHEMAS / "canonical")


def _write(root: Path, prefix: str, rt: str, month: str, rows: list[dict]) -> None:
    out = root / prefix / rt / f"{prefix}_{month}_{rt}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(out)


def _loan(dsn: str, pool: str, month: str, **kw) -> dict:
    as_of = date(int(month[:4]), int(month[4:]), 1)
    return {"pool_id": pool, "disclosure_sequence_number": dsn, "as_of_date": as_of, **kw}


def _pool(pool: str, issue_type: str, cusip: str = "C1") -> dict:
    return {
        "pool_id": pool,
        "cusip_number": cusip,
        "issue_type": issue_type,
        "pool_type": "SF",
        "pool_issue_date": date(2026, 1, 1),
    }


def test_portfolio_switches_feed_at_cutover(tmp_path: Path) -> None:
    for prefix, pool, it in (("llmon1", "A", "X"), ("llmon2", "B", "M")):
        for month in ("202606", "202607"):  # the old files overlap the cutover month
            _write(tmp_path, prefix, "L", month, [_loan(f"{prefix}-{month}", pool, month)])
            _write(tmp_path, prefix, "P", month, [_pool(pool, it)])
    _write(
        tmp_path,
        "llmon",
        "LL",
        "202607",
        [
            _loan("new-1", "A", "202607", issue_type="X"),
            _loan("new-2", "B", "202607", issue_type="C"),
        ],
    )

    out = load.scan_portfolio(silver_dir=tmp_path).collect().sort("disclosure_sequence_number")

    assert out["disclosure_sequence_number"].to_list() == [
        "llmon1-202606",
        "llmon2-202606",
        "new-1",
        "new-2",
    ]
    assert out["program"].to_list() == ["ginnie_i", "ginnie_ii", "ginnie_i", "ginnie_ii"]
    assert out["issue_type"].to_list() == ["X", "M", "X", "C"]  # from P before the cutover
    assert out.schema == pl.Schema({**load.loan_level_schema("llmon"), "program": pl.String})


def test_new_issuance_switches_feed_at_cutover(tmp_path: Path) -> None:
    for month in ("202607", "202608"):
        _write(tmp_path, "dailyllmni", "L", month, [_loan(f"old-{month}", "A", month)])
        _write(tmp_path, "dailyllmni", "P", month, [_pool("A", "C")])
    _write(tmp_path, "dailyllmnipd", "LL", "202608", [_loan("new-202608", "A", "202608")])

    out = load.scan_new_issuance(silver_dir=tmp_path).collect()
    assert sorted(out["disclosure_sequence_number"]) == ["new-202608", "old-202607"]
    only_new = load.scan_new_issuance(min_month="202608", silver_dir=tmp_path).collect()
    assert only_new["disclosure_sequence_number"].to_list() == ["new-202608"]


def test_disagreeing_pool_records_give_no_pool_fields(tmp_path: Path) -> None:
    _write(tmp_path, "dailyllmni", "L", "202601", [_loan("1", "A", "202601")])
    _write(tmp_path, "dailyllmni", "P", "202601", [_pool("A", "C", "C1"), _pool("A", "C", "C2")])

    out = load.scan_new_issuance(silver_dir=tmp_path).collect()
    assert out.height == 1
    assert out["cusip_number"].to_list() == [None]


def test_purpose_filter(tmp_path: Path) -> None:
    rows = [_loan(str(i), "A", "202601", loan_purpose=p) for i, p in enumerate("12345")]
    _write(tmp_path, "dailyllmni", "L", "202601", rows)
    out = load.scan_new_issuance(loan_purposes=("1", "2"), silver_dir=tmp_path).collect()
    assert sorted(out["loan_purpose"]) == ["1", "2"]


def test_no_files_in_range_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load.scan_portfolio(silver_dir=tmp_path)


def test_one_row_per_loan_collapses_copies_and_drops_conflicts() -> None:
    lf = pl.LazyFrame(
        {
            "dsn": ["a", "a", "b", "b", "c"],
            "rate": [5.0, 5.0, 5.0, 6.0, 7.0],
        }
    )
    out = load.one_row_per_loan(lf, "dsn").collect().sort("dsn")
    assert out.to_dict(as_series=False) == {"dsn": ["a", "c"], "rate": [5.0, 7.0]}


def test_latest_issuer_name_drops_two_names_on_latest_date(tmp_path: Path) -> None:
    d1, d2 = date(2026, 1, 1), date(2026, 2, 1)
    rows = {
        "issuer_id": ["1", "1", "2", "2", "2"],
        "issuer_name": ["Old ", "New", "X", "Y", "Z"],
        "record_as_of_date": [d1, d2, d1, d2, d2],
    }
    out_file = tmp_path / "issrinfo" / "ALL" / "issrinfo_202602_ALL.parquet"
    out_file.parent.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(out_file)

    out = load.scan_latest_issuer_names(silver_dir=tmp_path).collect()
    assert out.to_dict(as_series=False) == {"issuer_id": ["1"], "issuer_name": ["New"]}
