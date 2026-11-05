"""Combined restores the v1.8 loan-age convention on v2.0 portfolio months, and only there."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.combined import _common, loan_performance
from mortgage_data_manager.sources.gnma import load

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "gnma"


@pytest.fixture
def silver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(load.GNMAConfig, "GNMA_CANONICAL_DIR", SCHEMAS / "canonical")
    monkeypatch.setattr(load.GNMAConfig, "GNMA_SILVER_DIR", tmp_path)

    def write(prefix: str, rt: str, month: str, row: dict) -> None:
        out = tmp_path / prefix / rt / f"{prefix}_{month}_{rt}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame([row]).write_parquet(out)

    loan = {"pool_id": "A", "disclosure_sequence_number": "1", "as_of_date": date(2026, 6, 1)}
    for month in ("202606", "202607"):  # the old files run past the cutover
        write("llmon1", "L", month, {**loan, "loan_age_in_months": 10})
        pool = {"cusip_number": "C", "issue_type": "X", "pool_type": "SF", "pool_issue_date": None}
        write("llmon1", "P", month, {"pool_id": "A", **pool})
    write(
        "llmon",
        "LL",
        "202607",
        {**loan, "issue_type": "X", "loan_age_in_months": 12, "remaining_loan_term_in_months": 348},
    )
    return tmp_path


def test_v20_months_step_back_one_month(silver: Path) -> None:
    assert _common._gnma_loan_months("portfolio") == ["202606", "202607"]
    cols = ["loan_age_in_months", "remaining_loan_term_in_months", "program"]
    old = loan_performance._gnma_portfolio_month("202606").select(cols).collect()
    new = loan_performance._gnma_portfolio_month("202607").select(cols).collect()
    assert old.row(0) == (10, None, "ginnie_i")  # v1.8 month: as published
    assert new.row(0) == (11, 349, "ginnie_i")  # v2.0 month: age - 1, remaining + 1
