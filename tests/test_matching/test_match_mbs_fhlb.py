"""Unit tests for match-key construction and tolerance validation in the MBS<->FHLB matcher.

These are silent-corruption regression guards for three deterministic decisions where a bug
produces a plausible-but-wrong crosswalk that no type checker catches:

* **The FNMA-vs-GNMA scale asymmetry.** ``prepare_fnma_for_matching`` treats FNMA ILLD values as
  already in percent/dollars and applies **no** division, so an interest rate of ``4.5`` stays
  ``4.5``. The GNMA path instead carries raw fixed-point values (``3750`` basis points) into the
  prepared frame and defers the ``/1000`` (rate), ``/100`` (balance/LTV/DTI) scaling to
  ``match_gnma_fhlb``. If a refactor "helpfully" divided the FNMA side, every rate/balance filter
  would silently miss — so the ``match_fnma_fhlb`` filter test pins that ``4.5`` matches ``4.5``
  while a mis-scaled ``4500`` matches nothing.
* **The categorical match keys.** State -> FIPS, ``origination_year`` (FNMA ``First Payment Date %
  10000``; GNMA ``First Payment Date`` string sliced), FNMA loan-purpose ``P/R/C -> 1/2/6`` and
  GNMA agency ``F/V/R -> 1/2/3`` are hand-rolled remaps; a wrong code makes distinct loan
  populations join or fail to join.
* **The two-stage tolerance invariant.** ``MatchingTolerances.__post_init__`` guarantees each
  stricter post-match tolerance is ``<=`` its candidate tolerance; an inverted pair must raise so a
  mis-specified config fails loudly instead of quietly widening the final filter.

All expected values were observed by running the functions on these fixtures in the venv.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_mbs_fhlb.fnma_match import (
    match_fnma_fhlb,
    prepare_fnma_for_matching,
)
from mortgage_data_manager.matching.match_mbs_fhlb.gnma_match import (
    MatchingTolerances,
    prepare_gnma_for_matching,
)


def _fnma_raw(
    *,
    loan_id: int = 111,
    state: str = "CA",
    first_payment_date: int = 22024,
    rate: float = 4.5,
    purpose: str = "P",
) -> pl.DataFrame:
    """One-row raw FNMA ILLD frame carrying exactly the columns prepare_fnma reads."""
    return pl.DataFrame(
        {
            "Loan Identifier": [loan_id],
            "Origination Mortgage Loan Amount": [200000.0],
            "Origination Interest Rate": [rate],
            "Origination Loan-To-Value (LTV)": [80.0],
            "Origination Debt-To-Income Ratio": [36.0],
            "Origination Loan Term": [360],
            "Property State": [state],
            "First Payment Date": [first_payment_date],
            "Number of Units": [1],
            "Number of Borrowers": [1],
            "Origination Loan Purpose": [purpose],
            "Seller Name": ["FEDERAL HOME LOAN BANK OF X"],
        }
    )


def _gnma_raw() -> pl.DataFrame:
    """Four-row raw GNMA silver frame (agencies F/V/R/X) with the columns prepare_gnma reads."""
    return pl.DataFrame(
        {
            "Disclosure Sequence Number": ["1001", "1002", "1003", "1004"],
            "Agency (Agency Loan Type)": ["F", "V", "R", "X"],
            "Loan Purpose": [1, 2, 3, 1],
            "Refinance Type": ["N", "R", "C", "N"],
            "First Payment Date": ["20200115", "20210201", "20191210", "20220301"],
            "Loan Interest Rate": [3750, 4125, 3000, 5000],
            "Original Principal Balance": [20000000, 30000000, 15000000, 25000000],
            "Original Loan Term": [360, 360, 180, 360],
            "Loan To Value": [9650, 9000, 8000, 9900],
            "Combined LTV": [9650, 9000, 8000, 9900],
            "Total Debt Expense Ratio": [4200, 3600, 3000, 4500],
            "Credit Score": [720, 680, 700, 750],
            "Number of Borrowers": [1, 2, 1, 1],
            "First Time Home Buyer": ["Y", "N", "Y", "N"],
            "Property Type": [1, 2, 1, 1],
            "State": ["CA", "TX", "NY", "FL"],
        }
    )


def test_prepare_fnma_builds_keys_without_scaling() -> None:
    # CA->6, TX->48, unrecognized state -> null; year = First Payment Date % 10000
    # (22024 -> 2024, 122023 -> 2023); interest rate is passed through UNCHANGED (no /1000).
    raw = pl.concat(
        [
            _fnma_raw(loan_id=111, state="CA", first_payment_date=22024, rate=4.5, purpose="P"),
            _fnma_raw(loan_id=222, state="TX", first_payment_date=122023, rate=6.25, purpose="C"),
            _fnma_raw(loan_id=333, state="ZZ", first_payment_date=32020, rate=3.75, purpose="R"),
        ],
        how="diagonal_relaxed",
    )
    out = prepare_fnma_for_matching(raw)

    assert out["state_fips"].to_list() == [6, 48, None]
    assert out["origination_year"].to_list() == [2024, 2023, 2020]
    # No scale division on the FNMA side: the values are already in percent.
    assert out["interest_rate"].to_list() == pytest.approx([4.5, 6.25, 3.75])


@pytest.mark.parametrize(
    ("purpose_str", "expected_code"),
    [("P", 1), ("R", 2), ("C", 6), ("X", None)],
)
def test_prepare_fnma_loan_purpose_remap(purpose_str: str, expected_code: int | None) -> None:
    # FNMA P/R/C map to FHLB-space 1/2/6; anything unrecognized -> null (never a silent 0).
    out = prepare_fnma_for_matching(_fnma_raw(purpose=purpose_str))
    assert out["loan_purpose"].to_list() == [expected_code]


def test_prepare_gnma_agency_keys_and_deferred_scaling() -> None:
    out = prepare_gnma_for_matching(_gnma_raw())

    # Agency F/V/R -> 1/2/3, unknown agency -> null.
    assert out["agency_code"].to_list() == [1, 2, 3, None]
    # State -> FIPS on the GNMA side too.
    assert out["state_fips"].to_list() == [6, 48, 36, 12]
    # origination_year comes from the First Payment Date string sliced to YYYY (no orig-date col).
    assert out["origination_year"].to_list() == [2020, 2021, 2019, 2022]
    # Asymmetry vs FNMA: the GNMA prepared rate stays RAW fixed-point; the /1000 scaling is
    # deferred to match_gnma_fhlb. A regression that scaled here would double-scale at match time.
    assert out["interest_rate"].to_list() == pytest.approx([3750.0, 4125.0, 3000.0, 5000.0])


def _fnma_prepared(rate: float) -> pl.DataFrame:
    """A single prepared FNMA row that genuinely matches ``_fhlb_prepared`` when rate == 4.5."""
    return pl.DataFrame(
        {
            "fnma_loan_id": pl.Series([111], dtype=pl.Int64),
            "original_balance": pl.Series([200000.0], dtype=pl.Float64),
            "interest_rate": pl.Series([rate], dtype=pl.Float64),
            "ltv": pl.Series([80.0], dtype=pl.Float64),
            "dti": pl.Series([36.0], dtype=pl.Float64),
            "original_term": pl.Series([360], dtype=pl.Int64),
            "property_units": pl.Series([1], dtype=pl.Int64),
            "num_borrowers": pl.Series([1], dtype=pl.Int64),
            "loan_purpose": pl.Series([1], dtype=pl.Int64),
            "origination_year": pl.Series([2024], dtype=pl.Int64),
            "state_fips": pl.Series([6], dtype=pl.Int64),
        }
    )


def _fhlb_prepared() -> pl.DataFrame:
    """A single prepared FHLB row, values already in percent/dollars (rate 4.5, $200k)."""
    return pl.DataFrame(
        {
            "fhlb_loan_id": pl.Series([999], dtype=pl.Int64),
            "state_fips": pl.Series([6], dtype=pl.Int64),
            "ltv": pl.Series([80.0], dtype=pl.Float64),
            "note_year": pl.Series([2024], dtype=pl.Int64),
            "loan_purpose": pl.Series([1], dtype=pl.Int64),
            "original_term": pl.Series([360], dtype=pl.Int64),
            "num_borrowers": pl.Series([1], dtype=pl.Int64),
            "property_units": pl.Series([1], dtype=pl.Int64),
            "interest_rate": pl.Series([4.5], dtype=pl.Float64),
            "note_amount": pl.Series([200000.0], dtype=pl.Float64),
            "dti": pl.Series([36.0], dtype=pl.Float64),
            "fhlb_bank": pl.Series(["Chicago"], dtype=pl.String),
        }
    )


def test_match_fnma_fhlb_no_scale_filter() -> None:
    # Both sides already in percent -> the rate tolerance (0.5) passes and mutual-best 1:1 dedup
    # yields exactly one pair.
    matched = match_fnma_fhlb(_fnma_prepared(4.5), _fhlb_prepared())
    assert len(matched) == 1
    assert matched["fnma_loan_id"].to_list() == [111]
    assert matched["fhlb_loan_id"].to_list() == [999]

    # Anti-regression: if the FNMA rate were left as raw basis points (4500) instead of percent,
    # |4500 - 4.5| = 4495.5 blows the 0.5 tolerance and the pair silently vanishes.
    assert len(match_fnma_fhlb(_fnma_prepared(4500.0), _fhlb_prepared())) == 0


def test_matching_tolerances_defaults_hold_invariant() -> None:
    t = MatchingTolerances()
    # Defaults construct and every post-match tolerance is <= its candidate tolerance.
    assert t.post_rate_tolerance <= t.rate_tolerance
    assert t.post_balance_tolerance <= t.balance_tolerance
    assert t.post_ltv_tolerance <= t.ltv_tolerance
    assert t.post_dti_tolerance <= t.dti_tolerance
    assert t.apply_post_match_filter is True
    # An equal boundary is allowed (validation is <=, not strict <).
    assert MatchingTolerances(rate_tolerance=0.125, post_rate_tolerance=0.125).post_rate_tolerance == 0.125


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rate_tolerance": 0.1, "post_rate_tolerance": 0.5},
        {"balance_tolerance": 100.0, "post_balance_tolerance": 200.0},
        {"ltv_tolerance": 0.5, "post_ltv_tolerance": 1.0},
        {"dti_tolerance": 0.5, "post_dti_tolerance": 1.0},
    ],
)
def test_matching_tolerances_reject_inverted_pair(kwargs: dict[str, float]) -> None:
    # A stricter-than-candidate post tolerance must fail loudly, not silently widen the filter.
    with pytest.raises(ValueError, match="must be <="):
        MatchingTolerances(**kwargs)
