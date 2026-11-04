"""Tests for ``core.harmonized``: the decode tables and every consumer's map built from them.

Each consumer pin evaluates the consumer's own expression on its whole code domain, junk
codes included, and compares it with the literal map the consumer carried before it read
the shared tables. A table edit that would move a matcher's join key fails here first.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.core import harmonized as h
from mortgage_data_manager.matching.match_fhfa_mbs import inputs as fhfa_inputs
from mortgage_data_manager.matching.match_fhfa_mbs.matching_arm import FHLMC_ARM, FNMA_ARM
from mortgage_data_manager.matching.match_hmda_gnma import matching_fha as gnma_fha
from mortgage_data_manager.matching.match_hmda_gnma import matching_va_usda as gnma_va
from mortgage_data_manager.matching.match_hmda_gse import matching_direct as gse_direct

_JUNK = ["", "9", "X", None]


def _map(
    expr: pl.Expr, values: list, dtype: pl.DataType | type[pl.DataType], column: str = "x"
) -> dict:
    df = pl.DataFrame({column: pl.Series(values, dtype=dtype)})
    out = df.select(pl.col(column), expr.alias("_y"))
    return dict(zip(out[column].to_list(), out["_y"].to_list(), strict=True))


# --- the tables -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [(720, 720.0), (300, 300.0), (850, 850.0), (299, None), (9999, None), ("abc", None)],
)
def test_credit_score_inclusive_band_and_sentinels(score, expected):
    assert pl.select(h.credit_score(pl.lit(score))).item() == expected


def test_gse_and_gnma_channel_code_spaces_are_disjoint():
    # GSE uses letters, GNMA digits, and they do NOT line up: swapping the tables would
    # silently relabel every loan's origination channel.
    gse = _map(h.decode(pl.col("x"), h.GSE_CHANNEL), ["R", "B", "C", "T", *_JUNK], pl.String)
    assert gse == {
        "R": "retail",
        "B": "broker",
        "C": "correspondent",
        "T": "tpo",
        **dict.fromkeys(_JUNK),
    }
    gnma = _map(h.decode(pl.col("x"), h.GNMA_TPO_CHANNEL), ["1", "2", "3", *_JUNK], pl.String)
    assert gnma == {"1": "broker", "2": "correspondent", "3": "retail", **dict.fromkeys(_JUNK)}


def test_gnma_loan_purpose_official_decode():
    # Ginnie loan-level layout V1.0-V1.8: Refinance Type 2 = cash-out, 3 = streamlined;
    # Loan Purpose 3 / 4 = loan modification, 5 = re-performing. The builder once had
    # cash-out and streamline swapped and called code 3 construction.
    def gp(purpose: str, refi: str | None = None) -> str | None:
        return pl.select(h.gnma_loan_purpose(pl.lit(purpose), pl.lit(refi, dtype=pl.String))).item()

    assert gp("1") == "purchase"
    assert gp("2", "2") == "refi_cash_out"
    assert gp("2", "1") == "refi_rate_term"
    assert gp("2", "3") == "refi_rate_term"
    assert gp("2") == "refi_unspecified"
    for code in ("3", "4", "5"):
        assert gp(code) is None  # carried by is_modified_reissue, as the GSE "M" is


def test_decode_accepts_enum_levels_missing_a_table_code():
    # Fannie's purpose Enum has no N; a bare replace_strict rejects the table's N key.
    fnma = pl.Enum(["C", "P", "R"])
    got = _map(h.decode(pl.col("x"), h.GSE_LOAN_PURPOSE), ["C", "P", "R", None], fnma)
    assert got == {"C": "refi_cash_out", "P": "purchase", "R": "refi_rate_term", None: None}


def test_labels_are_the_declared_vocabularies():
    assert set(h.GSE_LOAN_PURPOSE.values()) | set(h.HMDA_LOAN_PURPOSE.values()) <= set(
        h.LOAN_PURPOSES
    )
    assert set(h.PURPOSE_CLASS) == set(h.LOAN_PURPOSES)
    assert set(h.GSE_OCCUPANCY.values()) == set(h.HMDA_OCCUPANCY.values()) == set(h.OCCUPANCIES)
    assert set(h.GSE_CHANNEL.values()) | set(h.GNMA_TPO_CHANNEL.values()) <= set(h.CHANNELS)
    for codes in h.FHFA_CHANNEL_CODE.values():
        assert set(codes) <= set(h.CHANNELS)


def test_invert_refuses_a_table_that_is_not_one_to_one():
    assert h.invert(h.GSE_OCCUPANCY) == {"principal": "P", "second_home": "S", "investment": "I"}
    with pytest.raises(ValueError, match="not one-to-one"):
        h.invert(h.GSE_LOAN_PURPOSE)  # R and N are both rate-term


# --- consumer pins --------------------------------------------------------------------


def test_fhfa_mbs_codes():
    # FHFA codes a Fannie correspondent loan 9 and a Freddie one 3; Freddie's T has no code.
    assert FNMA_ARM.channel_codes == {"R": 1, "B": 2, "C": 9}
    assert FHLMC_ARM.channel_codes == {"R": 1, "B": 2, "C": 3}
    purpose = h.compose(h.GSE_LOAN_PURPOSE, h.FHFA_LOAN_PURPOSE_CODE)
    # The Fannie SF leg had no N entry; Fannie publishes C/P/R only, so N cannot reach it.
    assert purpose == {"P": 1, "C": 7, "R": 2, "N": 2}
    assert h.compose(h.GSE_OCCUPANCY, h.OCCUPANCY_CODE) == {"P": 1, "S": 2, "I": 3}
    assert h.compose(h.GSE_PROPERTY_TYPE, h.FHFA_PROPERTY_TYPE_CODE) == {
        "SF": 1,
        "PU": 1,
        "CO": 1,
        "CP": 1,
        "MH": 2,
    }
    got = _map(
        fhfa_inputs._fhfa_code("x", h.GSE_LOAN_PURPOSE, h.FHFA_LOAN_PURPOSE_CODE),
        ["P", "C", "R", "N", "M", *_JUNK],
        pl.String,
    )
    assert got == {"P": 1, "C": 7, "R": 2, "N": 2, "M": None, **dict.fromkeys(_JUNK)}


def test_hmda_gse_direct_occupancy():
    got = _map(
        gse_direct._occupancy_unified(pl.col("x"), hmda=True), [1, 2, 3, 4, 0, None], pl.Int64
    )
    assert got == {1: "P", 2: "S", 3: "I", 4: None, 0: None, None: None}


def test_hmda_gnma_purpose_keys():
    hmda = [1, 2, 4, 5, 31, 32, None]
    got = _map(gnma_fha._hmda_purpose_cat(), hmda, pl.Int64, "loan_purpose")
    assert got == {1: "P", 2: "O", 4: "O", 5: "O", 31: "R", 32: "R", None: "O"}
    got = _map(gnma_va._h_purpose_block(), hmda, pl.Int64, "loan_purpose")
    assert got == {1: "P", 2: None, 4: None, 5: None, 31: "RT", 32: "RC", None: None}
    gnma = ["1", "2", "3", "4", "5", *_JUNK]
    df = pl.DataFrame({"gnma_purpose": gnma}).select(gnma_fha._gnma_purpose_cat())
    assert df.to_series().to_list() == ["P", "R", None, None, None, None, None, None, None]
    pairs = [(p, r) for p in ["1", "2", "3", "", None] for r in ["1", "2", "3", "", None]]
    df = pl.DataFrame(pairs, schema={"g_purpose": pl.String, "g_refi": pl.String}, orient="row")
    blocks = df.select(gnma_va._g_purpose_block()).to_series().to_list()
    expected = {("1", r): "P" for r in ["1", "2", "3", "", None]}
    expected |= {("2", "2"): "RC", ("2", "1"): "RT", ("2", "3"): "RT"}
    assert blocks == [expected.get(pair) for pair in pairs]
