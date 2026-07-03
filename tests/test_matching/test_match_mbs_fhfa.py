"""Unit tests for the pure key/bin helpers in the MBS<->FHFA matcher.

FHFA's sf_c file reports loan characteristics as coarsened bins (DTI in ~6 buckets,
loan amount at $10k midpoints). The FNMA/FHLMC matchers therefore hinge on three
deterministic decisions where an off-by-one silently produces a plausible-but-wrong
crosswalk that no type checker catches:

* ``build_dti_match_expr`` — does a raw GSE DTI fall in the FHFA DTI bucket (10/20/30
  ranges, exact 36-49, 50/60 ranges, 99=NA) within a tolerance? ~15 nested inequalities;
  a shifted boundary silently accepts or rejects candidate pairs.
* ``round_to_fhfa_amount_bins`` (FNMA) — rounds each loan to a $10k bin CENTER and
  DUPLICATES a loan sitting exactly on a $10k boundary into both adjacent centers. A
  bug here either drops the ambiguous-boundary match or double-counts everything.
* ``round_to_fhfa_bin`` / ``is_bin_edge`` / ``expand_edge_cases`` (FHLMC) — the Freddie
  side of the same bin-center logic; the two GSE bin-center formulas MUST agree or the
  two crosswalks bin loans onto different midpoints.

All expected values below were observed by running the functions on these fixtures.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_mbs_fhfa.matching_fhlmc import (
    expand_edge_cases,
    is_bin_edge,
    round_to_fhfa_bin,
)
from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
    build_dti_match_expr,
    round_to_fhfa_amount_bins,
)


def _dti_match(fnma: float | None, fhfa: float | None, tol: float) -> bool:
    """Evaluate build_dti_match_expr on a single (fnma, fhfa, tol) triple."""
    return (
        pl.DataFrame(
            {
                "dti": pl.Series([fnma], dtype=pl.Float64),
                "dti_fhfa": pl.Series([fhfa], dtype=pl.Float64),
            }
        )
        .select(build_dti_match_expr("dti", "dti_fhfa", tol))
        .item()
    )


@pytest.mark.parametrize(
    ("fnma", "fhfa", "tol", "expected"),
    [
        # Binned ranges: FNMA value must land in the correct FHFA bucket.
        (15, 10, 2.0, True),  # 0-19 -> bin 10
        (25, 20, 2.0, True),  # 20-29 -> bin 20
        (25, 10, 2.0, False),  # 25 is not within tol of the 20 lower edge -> wrong bucket
        (33, 30, 2.0, True),  # 30-35 -> bin 30
        (55, 50, 2.0, True),  # 50-60 -> bin 50
        (65, 60, 2.0, True),  # >60 -> bin 60
        (65, 50, 2.0, False),  # 65 nowhere near the 60 lower edge -> reject
        # Bin-edge tolerance: 19 sits one point below the 20 boundary.
        (19, 20, 1.0, True),  # accepted at tol 1
        (19, 20, 0.5, False),  # rejected once tol drops below the 1-point gap
        # Exact range (36-49): FHFA has 1-point precision, tolerance check applies.
        (42, 42, 2.0, True),  # exact
        (42, 43, 1.0, True),  # within tol
        (42, 45, 2.0, False),  # diff 3 > tol 2 -> reject
    ],
)
def test_build_dti_match_expr_boundary_table(
    fnma: float, fhfa: float, tol: float, expected: bool
) -> None:
    assert _dti_match(fnma, fhfa, tol) is expected


@pytest.mark.parametrize(
    ("fnma", "fhfa"),
    [
        (40, 99),  # FHFA 99 = "not available" is always allowed to pass
        (None, 30),  # null FNMA DTI passes (matched on other keys)
        (30, None),  # null FHFA DTI passes
    ],
)
def test_build_dti_match_expr_null_and_na_pass(fnma: float | None, fhfa: float | None) -> None:
    # These rows must never be filtered out by the DTI predicate.
    assert _dti_match(fnma, fhfa, 2.0) is True


def test_round_to_fhfa_amount_bins_duplicates_only_boundary_loans() -> None:
    # 141k/149k are interior to the [140k,150k) bin -> single row at center 145k.
    # 140k sits exactly on a $10k boundary -> duplicated into 135k AND 145k.
    lf = pl.LazyFrame({"loan_amount": [141000.0, 149000.0, 140000.0], "id": [1, 2, 3]})
    out = round_to_fhfa_amount_bins(lf).collect().sort(["id", "loan_amount"])

    # One extra row appears purely from the boundary duplication (3 in -> 4 out).
    assert out.height == 4
    # Original amount is preserved for downstream restoration.
    assert "loan_amount_orig" in out.columns

    def amounts_for(loan_id: int) -> list[float]:
        return sorted(out.filter(pl.col("id") == loan_id)["loan_amount"].to_list())

    assert amounts_for(1) == [145000.0]
    assert amounts_for(2) == [145000.0]
    assert amounts_for(3) == [135000.0, 145000.0]

    # The duplicated boundary loan keeps its true original amount on both copies.
    orig_for_3 = out.filter(pl.col("id") == 3)["loan_amount_orig"].to_list()
    assert orig_for_3 == [140000.0, 140000.0]


def test_round_to_fhfa_bin_matches_fnma_floor_formula() -> None:
    # The Freddie bin-center helper must agree exactly with the Fannie floor-div
    # formula (floor(a/10k)*10k + 5k); a drift would bin the two GSEs differently.
    amts = [141000.0, 149000.0, 140000.0, 170000.0, 171000.0, 168000.0, 5000.0, 9999.0]
    df = pl.DataFrame({"a": amts})
    fhlmc = df.select(round_to_fhfa_bin(pl.col("a")).alias("r"))
    fnma = df.select((pl.col("a") // 10000 * 10000 + 5000).alias("r"))

    assert fhlmc["r"].to_list() == fnma["r"].to_list()
    # Pin the concrete centers and the Float64 output dtype.
    assert fhlmc["r"].to_list() == [
        145000.0,
        145000.0,
        145000.0,
        175000.0,
        175000.0,
        165000.0,
        5000.0,
        5000.0,
    ]
    assert fhlmc["r"].dtype == pl.Float64


def test_is_bin_edge_flags_exact_10k_multiples() -> None:
    df = pl.DataFrame({"a": [170000.0, 171000.0, 140000.0, 145000.0, 0.0]})
    assert df.select(is_bin_edge(pl.col("a")).alias("e"))["e"].to_list() == [
        True,
        False,
        True,
        False,
        True,
    ]


def test_expand_edge_cases_duplicates_boundary_into_both_bins() -> None:
    # id=1 is a boundary loan (rounded to 175k midpoint): expands into the lower bin
    # (165k) and keeps the upper bin (175k). id=2 is interior and passes through once.
    lf = pl.LazyFrame(
        {
            "id": [1, 2],
            "loan_amount": [170000.0, 171000.0],
            "loan_amount_rounded": [175000.0, 175000.0],
            "is_edge_case": [True, False],
        }
    )
    out = expand_edge_cases(lf).collect().sort(["id", "loan_amount_rounded"])

    assert out.height == 3
    edge_bins = out.filter(pl.col("id") == 1)["loan_amount_rounded"].to_list()
    assert edge_bins == [165000.0, 175000.0]
    interior_bins = out.filter(pl.col("id") == 2)["loan_amount_rounded"].to_list()
    assert interior_bins == [175000.0]
