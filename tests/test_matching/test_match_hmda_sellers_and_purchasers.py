"""Unit tests for the seller-purchaser matcher's config seams and file-type selection.

``ToleranceConfig.to_dict`` must retain ``tolerance == 0`` entries (0 is a meaningful EXACT
constraint, not "no filter"), ``MatchColumnsConfig.to_list`` must emit the capital-P
``i_Purchase`` join key, and ``select_best_file_type`` must prefer the full snapshot (``c``)
over the redacted MLAR (``e``) — each is a silent round-voiding seam.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config.round_config import (
    MatchColumnsConfig,
    ToleranceConfig,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.utils import (
    keep_uniques,
    numeric_matches,
    perform_fee_matches,
    select_best_file_type,
)


def test_tolerance_config_retains_zero_but_drops_none():
    assert ToleranceConfig().to_dict() == {}
    assert ToleranceConfig(income=1000, interest_rate=0.0625).to_dict() == {
        "income": 1000,
        "interest_rate": 0.0625,
    }
    # 0 is an EXACT-match tolerance and MUST survive (a truthiness filter would delete it).
    assert ToleranceConfig(conforming_loan_limit=0, loan_term=0).to_dict() == {
        "conforming_loan_limit": 0,
        "loan_term": 0,
    }


def test_match_columns_config_uses_capital_p_i_purchase():
    assert MatchColumnsConfig().to_list() == ["loan_type", "loan_amount", "occupancy_type"]
    # add_purchase_indicator emits "i_Purchase"; a lowercase drift would void rounds 4-8.
    assert MatchColumnsConfig(i_purchase=True).to_list()[-1] == "i_Purchase"


def test_select_best_file_type_prefers_full_snapshot_over_mlar():
    def best(types: list[str]) -> list[str]:
        lf = pl.LazyFrame({"file_type": types, "v": list(range(len(types)))})
        return select_best_file_type(lf).collect()["file_type"].unique().to_list()

    assert best(["c", "e", "c"]) == ["c"]  # full snapshot beats modified LAR
    assert best(["e", "e"]) == ["e"]       # MLAR fallback when nothing better exists
    assert best(["b", "c", "a"]) == ["a"]  # priority a > b > c


def test_select_best_file_type_passthrough_without_column():
    lf = pl.LazyFrame({"v": [1, 2]})
    assert select_best_file_type(lf).collect().columns == ["v"]


# ---------------------------------------------------------------------------
# numeric_matches — tolerance gate with NULL passthrough
#
# The tolerance filter keeps a pair when |col_s - col_p| <= tolerance, but a NULL
# on EITHER side must pass through (a masked/exempt fee or income can't disqualify
# a pair). A regression that dropped the ``is_null()`` legs — or flipped ``<=`` to
# ``<`` — would silently shrink every round's crosswalk with no type error.
# ---------------------------------------------------------------------------


def test_numeric_matches_tolerance_and_null_passthrough():
    lf = pl.LazyFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "income_s": [100.0, 100.0, 100.0, 100.0, None],
            "income_p": [105.0, 120.0, 110.0, None, 100.0],
        }
    )
    survivors = sorted(numeric_matches(lf, {"income": 10.0}).collect()["id"].to_list())
    # id 1 within tol (|5|<=10) kept; id 2 over tol (|20|>10) dropped; id 3 boundary
    # (|10|==10) kept; id 4 (100, None) and id 5 (None, 100) both kept — null passthrough.
    assert survivors == [1, 3, 4, 5]


@pytest.mark.parametrize(
    ("income_s", "income_p", "kept"),
    [
        (100.0, 105.0, True),  # within tolerance
        (100.0, 110.0, True),  # boundary: |diff| == tolerance is inclusive
        (100.0, 120.0, False),  # over tolerance -> dropped
        (100.0, None, True),  # null purchaser passes through
        (None, 100.0, True),  # null seller passes through
    ],
)
def test_numeric_matches_boundary_and_null(income_s, income_p, kept):
    lf = pl.LazyFrame(
        {"income_s": [income_s], "income_p": [income_p]},
        schema={"income_s": pl.Float64, "income_p": pl.Float64},
    )
    n_kept = numeric_matches(lf, {"income": 10.0}).collect().height
    assert n_kept == (1 if kept else 0)


# ---------------------------------------------------------------------------
# keep_uniques — cardinality / collision gate
#
# A purchaser must map to exactly one seller (count over HMDAIndex_p == 1). Under
# strict one-to-one a seller must also map to exactly one purchaser. With
# one_to_one=False, a seller matched to TWO purchasers survives only when one leg
# is a secondary sale (purchaser_type_p > 4). Getting these counts wrong quietly
# admits many-to-one garbage into the crosswalk.
# ---------------------------------------------------------------------------


def _cardinality_fixture() -> pl.LazyFrame:
    # S2 -> {P2, P3}: seller matched to two purchasers (P2 leg is a secondary sale, type 8).
    # P4 -> {S3, S4}: purchaser matched to two sellers -> both legs fail the purchaser gate.
    return pl.LazyFrame(
        {
            "HMDAIndex_s": ["S1", "S2", "S2", "S3", "S4"],
            "HMDAIndex_p": ["P1", "P2", "P3", "P4", "P4"],
            "purchaser_type_p": [1, 8, 1, 1, 1],
        }
    )


def test_keep_uniques_strict_one_to_one():
    out = keep_uniques(_cardinality_fixture(), one_to_one=True).collect()
    pairs = sorted(zip(out["HMDAIndex_s"].to_list(), out["HMDAIndex_p"].to_list()))
    # Only S1<->P1 is one-to-one on both sides. S2 fans out to two purchasers (dropped);
    # P4 is claimed by two sellers (dropped by the purchaser==1 gate).
    assert pairs == [("S1", "P1")]


def test_keep_uniques_allows_two_purchasers_only_with_secondary_sale():
    # WITH a secondary-sale leg (purchaser_type_p=8 > 4): the S2 double-match survives.
    out = keep_uniques(_cardinality_fixture(), one_to_one=False).collect()
    pairs = sorted(zip(out["HMDAIndex_s"].to_list(), out["HMDAIndex_p"].to_list()))
    assert pairs == [("S1", "P1"), ("S2", "P2"), ("S2", "P3")]

    # WITHOUT any secondary-sale leg (both purchaser_type_p <= 4): the S2 double-match is dropped.
    no_secondary = pl.LazyFrame(
        {
            "HMDAIndex_s": ["S1", "S2", "S2"],
            "HMDAIndex_p": ["P1", "P2", "P3"],
            "purchaser_type_p": [1, 1, 3],
        }
    )
    out2 = keep_uniques(no_secondary, one_to_one=False).collect()
    pairs2 = sorted(zip(out2["HMDAIndex_s"].to_list(), out2["HMDAIndex_p"].to_list()))
    assert pairs2 == [("S1", "P1")]


# ---------------------------------------------------------------------------
# perform_fee_matches — exact fee-equality counting (null==null must NOT count)
#
# NumberFeeMatches counts same-column fee equalities only when BOTH sides are
# non-null; two masked (null) fees are not evidence of a match. i_GenerousFeeMatch
# fires when any seller fee equals any purchaser fee (cross-column), even with zero
# exact same-column matches. A null==null leak here inflates match quality and
# admits spurious pairs.
# ---------------------------------------------------------------------------


def test_perform_fee_matches_counts_and_generous_flag():
    lf = pl.LazyFrame(
        {
            "total_loan_costs_s": [100.0, 300.0, 1.0],
            "total_loan_costs_p": [100.0, None, 2.0],
            "total_points_and_fees_s": [200.0, None, 3.0],
            "total_points_and_fees_p": [250.0, 300.0, 4.0],
            "origination_charges_s": [None, None, 5.0],
            "origination_charges_p": [None, None, 6.0],
            "discount_points_s": [None, None, 7.0],
            "discount_points_p": [50.0, None, 8.0],
            "lender_credits_s": [75.0, None, 9.0],
            "lender_credits_p": [75.0, None, 10.0],
        }
    )
    out = perform_fee_matches(lf).collect()

    # Row 0: total_loan_costs (100==100) and lender_credits (75==75) match exactly -> 2.
    #   origination_charges is null==null and does NOT count.
    # Row 1: no same-column non-null equality -> 0, but total_loan_costs_s=300 equals
    #   total_points_and_fees_p=300 cross-column -> i_GenerousFeeMatch=1.
    # Row 2: all distinct non-null values -> 0 exact, 0 generous.
    assert out["NumberFeeMatches"].to_list() == [2, 0, 0]
    assert out["NumberNonmissingFees_s"].to_list() == [3, 1, 5]
    assert out["NumberNonmissingFees_p"].to_list() == [4, 1, 5]
    assert out["i_GenerousFeeMatch"].to_list() == [1, 1, 0]
    # Persisted dtypes are load-bearing for downstream parquet consumers.
    assert out.schema["NumberFeeMatches"] == pl.Int32
    assert out.schema["i_GenerousFeeMatch"] == pl.Int64
