"""Unit tests for the deterministic scale/enum expression helpers in ``combined.builders``.

These tiny per-value transforms are the load-bearing seams of the tri-agency harmonization: a
stray rescale (GNMA silver is canonical since the 2026-07 rebuild — the ``_dec`` helpers must
NOT re-divide), a swapped enum branch (the GSE and GNMA channel code spaces are disjoint), or a
missed sentinel silently corrupts the unified issuance/performance panels (tens of millions of
rows) with no type or schema error. Each is a string/number-in, value-out contract worth pinning.
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
    _gnma_rate_dec,
    _gnma_ratio_dec,
    _gnma_upb_dec,
    _purpose_letter_to_enum,
    _zbc_norm,
)


def _e(expr: pl.Expr):
    return pl.select(expr).item()


@pytest.mark.parametrize(("raw", "expected"), [("3.5", 3.5), ("4.305", 4.305), ("0", None)])
def test_gnma_rate_dec_passes_canonical_percent_through(raw, expected):
    # GNMA silver is CANONICAL (decoded at build) since the 2026-07 rebuild: rates already
    # arrive as real percent. The trap is the opposite of the old one — re-introducing the
    # retired /1000 fixed-point scaler here would silently shift every GNMA rate. 0 -> null.
    assert _e(_gnma_rate_dec(pl.lit(raw))) == expected


def test_gnma_ratio_and_upb_dec_do_not_rescale():
    # Same contract for ratios (int percent) and UPB (real USD): no /100, only 0/blank -> null.
    assert _e(_gnma_ratio_dec(pl.lit("95"))) == 95
    assert _e(_gnma_ratio_dec(pl.lit("0"))) is None
    assert _e(_gnma_upb_dec(pl.lit("12345.67"))) == pytest.approx(12345.67)
    assert _e(_gnma_upb_dec(pl.lit("0"))) is None


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


def test_gnma_purpose_official_decode():
    # Ginnie loan-level layout V1.0-V1.8: Refinance Type 2 = cash-out, 3 = streamlined;
    # Loan Purpose 3 / 4 = loan modification, 5 = re-performing. The builder once had
    # cash-out and streamline swapped and called code 3 construction.
    def gp(purpose: str, refi: str | None = None) -> str | None:
        return _e(_gnma_purpose(pl.lit(purpose), pl.lit(refi, dtype=pl.String)))

    assert gp("1") == "purchase"
    assert gp("2", "2") == "refi_cash_out"
    assert gp("2", "1") == "refi_rate_term"
    assert gp("2", "3") == "refi_rate_term"
    assert gp("2") == "refi_unspecified"
    for code in ("3", "4", "5"):
        assert gp(code) is None  # carried by is_modified_reissue, as the GSE "M" is


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
