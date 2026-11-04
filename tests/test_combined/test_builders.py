"""Unit tests for the deterministic scale/enum expression helpers of the combined builders.

These tiny per-value transforms are the load-bearing seams of the tri-agency harmonization: a
stray rescale (GNMA silver is canonical since the 2026-07 rebuild — the decode helpers must
NOT re-divide), a swapped enum branch (the GSE and GNMA channel code spaces are disjoint), or a
missed sentinel silently corrupts the unified issuance/performance panels (tens of millions of
rows) with no type or schema error. Each is a string/number-in, value-out contract worth pinning.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from mortgage_data_manager.combined._common import (
    _date_to_yyyymmdd,
    _gnma_num,
)
from mortgage_data_manager.combined.loan_issuance import (
    _fhlmc_quarter_from_key,
    _gnma_ratio_dec,
    _one_umbs_row_per_loan,
)
from mortgage_data_manager.combined.loan_performance import (
    _event_type_expr,
    _gnma_event_type,
    _zbc_norm,
)


def _e(expr: pl.Expr):
    return pl.select(expr).item()


@pytest.mark.parametrize(("raw", "expected"), [("3.5", 3.5), ("4.305", 4.305), ("0", None)])
def test_gnma_num_passes_canonical_percent_through(raw, expected):
    # GNMA silver is CANONICAL (decoded at build) since the 2026-07 rebuild: rates already
    # arrive as real percent. The trap is the opposite of the old one — re-introducing the
    # retired /1000 fixed-point scaler here would silently shift every GNMA rate. 0 -> null.
    assert _e(_gnma_num(pl.lit(raw))) == expected


def test_gnma_ratio_and_upb_do_not_rescale():
    # Same contract for ratios (int percent) and UPB (real USD): no /100, only 0/blank -> null.
    assert _e(_gnma_ratio_dec(pl.lit("95"))) == 95
    assert _e(_gnma_ratio_dec(pl.lit("0"))) is None
    assert _e(_gnma_num(pl.lit("12345.67"))) == pytest.approx(12345.67)
    assert _e(_gnma_num(pl.lit("0"))) is None
    # a paid-off pool's current balance is a real zero, not a blank
    assert _e(_gnma_num(pl.lit("0"), keep_zero=True)) == 0.0


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("F21Q11275813", "2021Q1"),
        ("F99Q31234567", "1999Q3"),
        ("A05Q21234567", "2005Q2"),
        ("X1", None),
    ],
)
def test_fhlmc_quarter_from_key_century_and_arm_prefix(key, expected):
    # 1999 keys once decoded to 2099, and ARM (A-prefixed) keys to null.
    assert _e(_fhlmc_quarter_from_key(pl.lit(key))) == expected


def test_zbc_norm_unifies_fnma_string_and_fhlmc_int():
    assert _e(_zbc_norm(pl.lit("1"))) == "01"
    assert _e(_zbc_norm(pl.lit(3))) == "03"  # FHLMC integer path
    assert _e(_zbc_norm(pl.lit("  2 "))) == "02"  # strip then zero-pad


def test_event_type_maps():
    assert _e(_event_type_expr(pl.lit("01"))) == "prepaid_or_matured"
    assert _e(_event_type_expr(pl.lit("02"))) == "third_party_sale"
    assert _e(_event_type_expr(pl.lit("09"))) == "reo_disposition"
    assert _e(_gnma_event_type(pl.lit("1"))) == "voluntary_prepay"
    assert _e(_gnma_event_type(pl.lit("2"))) == "dq_buyout"
    assert _e(_gnma_event_type(pl.lit("3"))) == "foreclosure_reo"
    assert _e(_gnma_event_type(pl.lit("9"))) is None


def test_date_to_yyyymmdd_avoids_int8_overflow():
    # Per-component Int32 cast guards against the Int8 month*100 overflow the docstring flags.
    assert _e(_date_to_yyyymmdd(pl.lit(date(2021, 11, 5)))) == 20211105
    assert _e(_date_to_yyyymmdd(pl.lit(date(2019, 6, 1)))) == 20190601  # the UMBS/MBS boundary
    assert _e(_date_to_yyyymmdd(pl.lit(None, dtype=pl.Date))) is None


def test_one_umbs_row_per_loan_nulls_disputed_fields_and_drops_compared_conflicts():
    rows = pl.LazyFrame(
        {
            "loan_key_umbs": ["A", "B", "B", "C", "C", "D", "D"],
            "_umbs_priority": [0, 0, 0, 0, 0, 0, 2],
            "Security Identifier": ["s1", "s1", "s2", "s3", "s4", "s5", "s6"],
            "Mortgage Loan Amount": [100.0, 200.0, 200.0, 300.0, 301.0, 400.0, 999.0],
        }
    )
    got = _one_umbs_row_per_loan(rows, ["Mortgage Loan Amount"]).collect().sort("loan_key_umbs")
    # A: one row. B: two securities, same amount -> kept, CUSIP nulled. C: disagrees on a
    # compared field -> excluded. D: the reissue (rank 2) loses to the original outright.
    assert got["loan_key_umbs"].to_list() == ["A", "B", "D"]
    assert got["Security Identifier"].to_list() == ["s1", None, "s5"]
    assert got["Mortgage Loan Amount"].to_list() == [100.0, 200.0, 400.0]
