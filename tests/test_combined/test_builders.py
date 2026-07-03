"""Unit tests for the deterministic scale/enum expression helpers in ``combined.builders``.

These tiny per-value transforms are the load-bearing seams of the tri-agency harmonization: a
wrong scale factor (GNMA rate is ``/1000``, not ``/100``), a swapped enum branch (the GSE and
GNMA channel code spaces are disjoint), or a missed sentinel silently corrupts the unified
issuance/performance panels (tens of millions of rows) with no type or schema error. Each is a
string/number-in, value-out contract worth pinning.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from mortgage_data_manager.combined.builders import (
    _channel_to_enum,
    _date_to_yyyymmdd,
    _event_type_expr,
    _fico_clean,
    _gnma_event_type,
    _gnma_purpose,
    _gnma_rate,
    _gnma_ratio,
    _gnma_upb,
    _purpose_letter_to_enum,
    _zbc_norm,
)


def _e(expr: pl.Expr):
    return pl.select(expr).item()


@pytest.mark.parametrize(("raw", "expected"), [("03500", 3.5), ("04305", 4.305), ("00000", None)])
def test_gnma_rate_divides_by_1000(raw, expected):
    # The documented trap: GNMA fixed-point rate is /1000 (3.5%), NOT /100; 0 is a null sentinel.
    assert _e(_gnma_rate(pl.lit(raw))) == expected


def test_gnma_ratio_and_upb_scaling():
    assert _e(_gnma_ratio(pl.lit("9500"))) == 95  # LTV/CLTV/DTI /100
    assert _e(_gnma_ratio(pl.lit("0"))) is None
    assert _e(_gnma_upb(pl.lit("1234567"))) == pytest.approx(12345.67)  # UPB cents /100
    assert _e(_gnma_upb(pl.lit("0"))) is None


@pytest.mark.parametrize(
    ("score", "expected"),
    [(720, 720.0), (300, 300.0), (850, 850.0), (299, None), (9999, None)],
)
def test_fico_clean_inclusive_band_and_sentinels(score, expected):
    # Inclusive [300, 850]; out-of-band and 9999 sentinel -> null.
    assert _e(_fico_clean(pl.lit(score))) == expected


def test_channel_enum_code_spaces_are_disjoint():
    # GSE uses letters, GNMA uses digits, and they do NOT line up: swapping the branch would
    # silently relabel every loan's origination channel.
    assert _e(_channel_to_enum(pl.lit("R"), gnma=False)) == "retail"
    assert _e(_channel_to_enum(pl.lit("B"), gnma=False)) == "broker"
    assert _e(_channel_to_enum(pl.lit("1"), gnma=True)) == "broker"
    assert _e(_channel_to_enum(pl.lit("3"), gnma=True)) == "retail"


def test_purpose_letter_enum():
    assert _e(_purpose_letter_to_enum(pl.lit("P"))) == "purchase"
    assert _e(_purpose_letter_to_enum(pl.lit("C"))) == "refi_cash_out"
    assert _e(_purpose_letter_to_enum(pl.lit("R"))) == "refi_rate_term"
    assert _e(_purpose_letter_to_enum(pl.lit("X"))) is None


def test_gnma_purpose_curated_defaults():
    def gp(purpose: str) -> str | None:
        return _e(_gnma_purpose(pl.lit(purpose), pl.lit(None, dtype=pl.String)))

    assert gp("1") == "purchase"
    assert gp("3") == "construction"  # curated default for GNMA code 3
    assert gp("4") == "other"         # curated default for GNMA codes 4/5
    assert gp("5") == "other"


def test_zbc_norm_unifies_fnma_string_and_fhlmc_int():
    assert _e(_zbc_norm(pl.lit("1"))) == "01"
    assert _e(_zbc_norm(pl.lit(3))) == "03"       # FHLMC integer path
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
