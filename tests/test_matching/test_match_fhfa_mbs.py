"""Unit tests for the pure key/bin helpers in the MBS<->FHFA matcher.

FHFA's sf_c file reports loan characteristics as coarsened bins (DTI in ~6 buckets,
loan amount at $10k midpoints). The matcher therefore hinges on a few deterministic
decisions where an off-by-one silently produces a plausible-but-wrong crosswalk that
no type checker catches:

* ``build_dti_match_expr`` — does a raw GSE DTI fall in the FHFA DTI bucket (10/20/30
  ranges, exact 36-49, 50/60 ranges, 99=NA) within a tolerance? ~15 nested inequalities;
  a shifted boundary silently accepts or rejects candidate pairs.
* ``round_to_fhfa_bin`` / ``is_bin_edge`` / ``expand_bin_edges`` — round each loan to a
  $10k bin CENTER and DUPLICATE a loan sitting exactly on a $10k boundary into both
  adjacent centers. A bug here either drops the ambiguous-boundary match or
  double-counts everything.
* ``quality_tier`` — the per-enterprise tier ladders, which the crosswalk consumers
  read as confidence.

All expected values below were observed by running the functions on these fixtures.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import polars as pl
import pytest

from mortgage_data_manager.matching.match_fhfa_mbs import inputs
from mortgage_data_manager.matching.match_fhfa_mbs.inputs import FHLMC, FNMA, prepare_fnma
from mortgage_data_manager.matching.match_fhfa_mbs.matching import (
    admitted,
    build_dti_match_expr,
    expand_bin_edges,
    match_pass,
    quality_tier,
)
from mortgage_data_manager.matching.match_fhfa_mbs.matching_arm import (
    RECORD_KEY,
    offer_origination_years,
)
from mortgage_data_manager.matching.match_fhfa_mbs.recovery import (
    iterate_to_fixpoint,
    recovery_gate,
    resolve_cross_year,
    settle,
)
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import ROUNDS_ARM
from mortgage_data_manager.matching.match_fhfa_mbs.spec import MatchingTolerances
from mortgage_data_manager.matching.terms import is_bin_edge, round_to_fhfa_bin


def _dti_match(gse: float | None, fhfa: float | None, tol: float) -> bool:
    """Evaluate build_dti_match_expr on a single (gse, fhfa, tol) triple."""
    return (
        pl.DataFrame(
            {
                "dti": pl.Series([gse], dtype=pl.Float64),
                "dti_fhfa": pl.Series([fhfa], dtype=pl.Float64),
            }
        )
        .select(build_dti_match_expr("dti", "dti_fhfa", tol))
        .item()
    )


@pytest.mark.parametrize(
    ("gse", "fhfa", "tol", "expected"),
    [
        # Binned ranges: the GSE value must land in the correct FHFA bucket.
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
    gse: float, fhfa: float, tol: float, expected: bool
) -> None:
    assert _dti_match(gse, fhfa, tol) is expected


@pytest.mark.parametrize(
    ("gse", "fhfa"),
    [
        (40, 99),  # FHFA 99 = "not available" is always allowed to pass
        (None, 30),  # null GSE DTI passes (matched on other keys)
        (30, None),  # null FHFA DTI passes
    ],
)
def test_build_dti_match_expr_null_and_na_pass(gse: float | None, fhfa: float | None) -> None:
    # These rows must never be filtered out by the DTI predicate.
    assert _dti_match(gse, fhfa, 2.0) is True


def _with_bins(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Attach the two amount sidecars the preparers emit."""
    return lf.with_columns(
        round_to_fhfa_bin(pl.col("loan_amount")).alias("loan_amount_rounded"),
        is_bin_edge(pl.col("loan_amount")).alias("_is_bin_edge"),
    )


def test_expand_bin_edges_duplicates_only_boundary_loans() -> None:
    # 141k/149k are interior to the [140k,150k) bin -> single row at center 145k.
    # 140k sits exactly on a $10k boundary -> duplicated into 135k AND 145k.
    lf = _with_bins(pl.LazyFrame({"loan_amount": [141000.0, 149000.0, 140000.0], "id": [1, 2, 3]}))
    out = expand_bin_edges(lf).collect().sort(["id", "loan_amount_rounded"])

    # One extra row appears purely from the boundary duplication (3 in -> 4 out).
    assert out.height == 4

    def bins_for(loan_id: int) -> list[float]:
        return sorted(out.filter(pl.col("id") == loan_id)["loan_amount_rounded"].to_list())

    assert bins_for(1) == [145000.0]
    assert bins_for(2) == [145000.0]
    assert bins_for(3) == [135000.0, 145000.0]

    # The duplicated boundary loan keeps its true original amount on both copies.
    assert out.filter(pl.col("id") == 3)["loan_amount"].to_list() == [140000.0, 140000.0]


def test_round_to_fhfa_bin_matches_floor_formula() -> None:
    # The bin-center helper must agree exactly with the floor-div formula
    # (floor(a/10k)*10k + 5k) FHFA's midpoints are defined by.
    amts = [141000.0, 149000.0, 140000.0, 170000.0, 171000.0, 168000.0, 5000.0, 9999.0]
    df = pl.DataFrame({"a": amts})
    binned = df.select(round_to_fhfa_bin(pl.col("a")).alias("r"))
    formula = df.select((pl.col("a") // 10000 * 10000 + 5000).alias("r"))

    assert binned["r"].to_list() == formula["r"].to_list()
    # Pin the concrete centers and the Float64 output dtype.
    assert binned["r"].to_list() == [
        145000.0,
        145000.0,
        145000.0,
        175000.0,
        175000.0,
        165000.0,
        5000.0,
        5000.0,
    ]
    assert binned["r"].dtype == pl.Float64


def test_is_bin_edge_flags_exact_10k_multiples() -> None:
    df = pl.DataFrame({"a": [170000.0, 171000.0, 140000.0, 145000.0, 0.0]})
    assert df.select(is_bin_edge(pl.col("a")).alias("e"))["e"].to_list() == [
        True,
        False,
        True,
        False,
        True,
    ]


def test_expand_bin_edges_duplicates_boundary_into_both_bins() -> None:
    # id=1 is a boundary loan (rounded to 175k midpoint): expands into the lower bin
    # (165k) and keeps the upper bin (175k). id=2 is interior and passes through once.
    lf = pl.LazyFrame(
        {
            "id": [1, 2],
            "loan_amount": [170000.0, 171000.0],
            "loan_amount_rounded": [175000.0, 175000.0],
            "_is_bin_edge": [True, False],
        }
    )
    out = expand_bin_edges(lf).collect().sort(["id", "loan_amount_rounded"])

    assert out.height == 3
    assert out.filter(pl.col("id") == 1)["loan_amount_rounded"].to_list() == [165000.0, 175000.0]
    assert out.filter(pl.col("id") == 2)["loan_amount_rounded"].to_list() == [175000.0]


def test_quality_tier_ladders() -> None:
    # Columns: (rate diff, amount diff, ltv diff, FHFA-truncation flag). Fannie demands
    # a literal exact rate and has no amount rung; Freddie counts <=1bp as exact and
    # adds the amount rung. Row 7 is a 4.125 reported by FHFA as 4.12: the 0.005
    # deviation is FHFA's convention, so both enterprises still count it as exact.
    df = pl.DataFrame(
        {
            "_rate_diff": [0.0, 0.0, 0.0, 0.005, 0.02, 0.0, 0.005],
            "_amount_diff": [0.0, 1000.0, 0.0, 0.0, 0.0, 1000.0, 0.0],
            "_ltv_diff": [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0],
            "_rate_conv_ok": [False, False, False, False, False, False, True],
        }
    )
    fnma = df.select(quality_tier(FNMA.options).alias("t"))["t"]
    fhlmc = df.select(quality_tier(FHLMC.options).alias("t"))["t"]

    assert fnma.to_list() == [1, 1, 2, 3, 3, 2, 1]
    assert fhlmc.to_list() == [1, 2, 3, 1, 4, 3, 1]
    assert fnma.dtype == pl.Int32
    assert fhlmc.dtype == pl.Int32


# ---------------------------------------------------------------------------
# Silver dtype-migration regressions (2026-07): the SF issuance silver now
# stores FTHB as Boolean and Origination Date as pl.Date. The old preparer
# mapped 'Y'/'N' strings, silently nulling fthb — a JOIN KEY — so the matcher
# returned ZERO matches with no error. These pin both encodings.
# ---------------------------------------------------------------------------


def _fnma_raw(fthb: object, orig_date: object, fpd: object) -> pl.LazyFrame:
    """Minimal single-loan raw issuance frame around the mutating dtypes."""
    return pl.LazyFrame(
        {
            "Loan Identifier": ["000000000001"],
            "Property State": ["CA"],
            "Metropolitan Statistical Area (MSA)": ["31080"],
            "Channel": ["R"],
            "Original Interest Rate": [6.5],
            "Original UPB": [250000.0],
            "Original Loan to Value Ratio (LTV)": [80.0],
            "Original Combined Loan to Value Ratio (CLTV)": [80.0],
            "Debt-To-Income (DTI)": [38.0],
            "Original Loan Term": [360],
            "Number of Units": [1],
            "Number of Borrowers": [2],
            "First Time Home Buyer Indicator": [fthb],
            "Loan Purpose": ["P"],
            "Occupancy Status": ["P"],
            "Property Type": ["SF"],
            "Origination Date": [orig_date],
            "First Payment Date": [fpd],
            "Zip Code Short": ["900"],
        }
    )


@pytest.mark.parametrize(
    ("fthb", "orig_date", "fpd", "expected_fthb", "expected_year"),
    [
        # current silver: Boolean + pl.Date
        (True, date(2021, 12, 1), date(2022, 2, 1), 1, 2021),
        (False, date(2021, 12, 1), date(2022, 2, 1), 2, 2021),
        # legacy silver: Y/N + MMYYYY strings
        ("Y", "122021", "022022", 1, 2021),
        ("N", "122021", "022022", 2, 2021),
    ],
)
def test_prepare_fnma_handles_both_silver_encodings(
    fthb: object, orig_date: object, fpd: object, expected_fthb: int, expected_year: int
) -> None:
    row = prepare_fnma(_fnma_raw(fthb, orig_date, fpd)).collect().row(0, named=True)
    assert row["fthb"] == expected_fthb  # null here == the silent-zero-matches bug
    assert row["origination_year"] == expected_year
    # December origination, February first payment: the two years must differ.
    assert row["fpd_year"] == expected_year + 1
    assert row["zip3"] == "900"
    # $250k sits on a bin boundary: rounded up, flagged for duplication.
    assert row["loan_amount_rounded"] == 255000.0
    assert row["_is_bin_edge"] is True


def _match_fixture(zip3s: list[str | None]) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """One FHFA record vs len(zip3s) FNMA twins identical up to ZIP3."""
    n = len(zip3s)
    fnma = _with_bins(
        pl.LazyFrame(
            {
                "loan_id": [f"L{i}" for i in range(n)],
                "state_fips": [6] * n,
                "msa": [31080] * n,
                "channel": [1] * n,
                "interest_rate": [6.5] * n,
                "loan_amount": [255000.0] * n,  # bin center: no edge duplication
                "ltv": [80.0] * n,
                "dti": [38.0] * n,
                "term": [360] * n,
                "num_units": [1] * n,
                "num_borrowers": [2] * n,
                "fthb": [2] * n,
                "loan_purpose": [1] * n,
                "occupancy_code": [1] * n,
                "property_type": [1] * n,
                "origination_year": [2021] * n,
                "zip3": zip3s,
            }
        )
    )
    fhfa = pl.LazyFrame(
        {
            "fhfa_record_id": [1],
            "fhfa_enterprise": [1],
            "state_fips": [6],
            "msa": [31080],
            "channel": [1],
            "interest_rate": [6.5],
            "loan_amount": [255000.0],
            "ltv": [80.0],
            "dti": [38.0],  # exact-range DTI (36-49): must equal the loan side
            "term": [360],
            "num_units": [1],
            "num_borrowers": [2],
            "fthb": [2],
            "loan_purpose": [1],
            "occupancy_code": [1],
            "property_type": [1],
            "year": [2021],
            "orig_year_flag": [1],
            "zip3": ["900"],
        }
    )
    return fnma, fhfa


def test_zip3_penalty_splits_score_tied_twins() -> None:
    """Two identical candidates differing only in ZIP3.

    Without the penalty the tie drops both (the pre-ZIP3 behavior); with it, the
    geography-consistent twin wins and carries ``zip3_consistent=True``.
    """
    fnma, fhfa = _match_fixture(["900", "941"])
    no_penalty = replace(FNMA, tolerances=MatchingTolerances(zip3_penalty=0.0))
    assert match_pass(fnma, fhfa, no_penalty).collect().height == 0
    with_pen = match_pass(fnma, fhfa, FNMA).collect()
    assert with_pen.height == 1
    assert with_pen["loan_id"][0] == "L0"
    assert with_pen["zip3_consistent"][0] is True


def test_zip3_penalty_neutral_on_null_zip3() -> None:
    """A lone candidate with null ZIP3 still matches.

    Null ZIP3 is neutral, never a filter; the QA flag is null.
    """
    fnma, fhfa = _match_fixture([None])
    out = match_pass(fnma, fhfa, FNMA).collect()
    assert out.height == 1
    assert out["zip3_consistent"][0] is None


# ---------------------------------------------------------------------------
# The FHLMC amount term (2026-09-14). Both sides are pre-binned onto FHFA's $10k
# midpoints and joined exactly, so the term measures where inside the bin a loan's
# UPB sits -- constant across one loan's candidates, and flat against truth across
# a record's (held-out tract agreement 0.9806-0.9823 at all six levels it takes).
# At weight 1/0 -> 1000 on whole-$1,000 balances it outranks the whole rest of the
# score, so it used to settle every contested FHFA record on its own. It stays in
# the score; it may no longer decide a pair.
# ---------------------------------------------------------------------------

#: The FHLMC spec as it stood before the rule: the amount term decides. Used for the
#: A/A comparison, and the shape a staged rebuild of the old crosswalk takes.
LEGACY_FHLMC = replace(FHLMC, options=replace(FHLMC.options, non_evidence_score_fields=()))


def test_fnma_score_has_no_amount_term() -> None:
    # Fannie's leg never scored the amount, so the rule is a no-op there and the
    # FNMA crosswalk cannot move: no flag column, nothing for `admitted` to drop.
    assert "loan_amount" not in FNMA.options.score_fields
    assert FNMA.options.non_evidence_score_fields == ()
    assert FNMA.options.evidence_score_fields() == FNMA.options.score_fields
    fnma, fhfa = _match_fixture(["900"])
    assert "_evidence_decided" not in match_pass(fnma, fhfa, FNMA).collect().columns


def test_fhlmc_amount_term_is_scored_but_not_evidence() -> None:
    assert "loan_amount" in FHLMC.options.score_fields
    assert FHLMC.options.non_evidence_score_fields == ("loan_amount",)
    # What remains is exactly Fannie's score.
    assert FHLMC.options.evidence_score_fields() == FNMA.options.score_fields


def _amount_fixture(
    amounts: list[float], zip3s: list[str | None]
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """FHLMC twins in one $10k bin, differing only in their UPB and their ZIP3."""
    n = len(amounts)
    gse = _with_bins(
        pl.LazyFrame(
            {
                "loan_id": [f"L{i}" for i in range(n)],
                "state_fips": [6] * n,
                "msa": [31080] * n,
                "channel": [1] * n,
                "interest_rate": [6.5] * n,
                "loan_amount": amounts,
                "ltv": [80.0] * n,
                "dti": [38.0] * n,
                "term": [360] * n,
                "num_units": [1] * n,
                "num_borrowers": [2] * n,
                "fthb": [2] * n,
                "loan_purpose": [1] * n,
                "occupancy_code": [1] * n,
                "property_type": [1] * n,
                "origination_year": [2021] * n,
                "zip3": zip3s,
            }
        )
    )
    _, fhfa = _match_fixture([None])
    return gse, fhfa.with_columns(pl.lit(2).alias("fhfa_enterprise"), pl.lit("900").alias("zip3"))


@pytest.mark.parametrize(
    ("amounts", "zip3s", "legacy_winner", "written"),
    [
        # The term breaks a tie the evidence cannot: rate, LTV, DTI and ZIP3 all agree,
        # and it picks the loan nearer the $255k midpoint.
        ([251000.0, 254000.0], ["900", "900"], "L1", []),
        # The term overrules the evidence: the ZIP3 penalty prefers L0, the term L1.
        ([251000.0, 254000.0], ["900", "941"], "L1", []),
        # The term agrees with the evidence: both single out L0, so the pair stands.
        ([254000.0, 251000.0], ["900", "941"], "L0", ["L0"]),
        # One candidate: no contest, so there is nothing for the term to settle.
        ([251000.0], ["900"], "L0", ["L0"]),
    ],
    ids=["tie broken by the term", "term overrode the evidence", "term agreed", "sole candidate"],
)
def test_amount_term_no_longer_settles_a_record(
    amounts: list[float], zip3s: list[str | None], legacy_winner: str, written: list[str]
) -> None:
    gse, fhfa = _amount_fixture(amounts, zip3s)

    legacy = match_pass(gse, fhfa, LEGACY_FHLMC).collect()
    assert legacy["loan_id"].to_list() == [legacy_winner]
    assert "_evidence_decided" not in legacy.columns

    # The pass itself is unchanged -- the same pair, claiming the same loan and the
    # same record, so the rounds after it see identical leftovers. Only the written
    # crosswalk differs, which is what makes the change a pure deletion.
    pairs = match_pass(gse, fhfa, FHLMC).collect()
    assert pairs["loan_id"].to_list() == [legacy_winner]
    assert pairs["_evidence_decided"].to_list() == [bool(written)]
    assert admitted(pairs)["loan_id"].to_list() == written
    assert "_evidence_decided" not in admitted(pairs).columns


def test_admitted_is_a_no_op_without_the_flag() -> None:
    # An FNMA frame carries no flag; `admitted` must not drop its rows, only the
    # cross-year sidecar.
    pairs = pl.DataFrame({"fnma_loan_id": ["A", "B"], "fpd_year": [2021, 2022]})
    assert admitted(pairs).to_dict(as_series=False) == {"fnma_loan_id": ["A", "B"]}


# ---------------------------------------------------------------------------
# Recovery rounds (2026-09): the pure decisions in recovery.py and the FHLMC
# acquisition-window pre-filter. Production once carried 48k Freddie loans
# assigned to FHFA records in two adjacent acquisition years; the tie-break
# order, the retire-not-re-offer rule and the window edges are the places a
# flipped sign would silently ship wrong pairs.
# ---------------------------------------------------------------------------


def _claims(rows: list[tuple[str, int, int, bool | None, int | None]], year: int) -> pl.DataFrame:
    """One year's crosswalk rows: (loan, record, round, zip3_consistent, fpd_year)."""
    loans, records, rounds, zip3s, fpds = zip(*rows, strict=True)
    return pl.DataFrame(
        {
            "fhlmc_loan_id": list(loans),
            "fhfa_record_id": list(records),
            "fhfa_year": [year] * len(rows),
            "match_round": pl.Series(list(rounds), dtype=pl.UInt8),
            "zip3_consistent": pl.Series(list(zip3s), dtype=pl.Boolean),
            "fpd_year": pl.Series(list(fpds), dtype=pl.Int32),
        }
    )


def test_resolve_cross_year_drops_every_contested_loan() -> None:
    # A-E are each claimed by both 2020 and 2021. The claims exclude each other, so
    # neither survives -- whatever the rounds, ZIP3 agreement or first-payment years
    # say (each letter differs on one of them). F is claimed once and is untouched.
    y2020 = _claims(
        [
            ("A", 1, 1, True, 2020),
            ("B", 2, 1, False, 2020),
            ("C", 3, 1, None, 2020),
            ("D", 4, 1, True, 2021),
            ("E", 5, 1, True, None),
            ("F", 6, 1, True, 2020),
        ],
        2020,
    )
    y2021 = _claims(
        [
            ("A", 11, 3, True, 2020),
            ("B", 12, 1, True, 2020),
            ("C", 13, 1, False, 2020),
            ("D", 14, 1, True, 2021),
            ("E", 15, 1, True, None),
        ],
        2021,
    )

    resolved, removed = resolve_cross_year({2020: y2020, 2021: y2021}, "fhlmc_loan_id")

    assert removed == 10
    assert resolved[2020]["fhlmc_loan_id"].to_list() == ["F"]
    assert resolved[2021].is_empty()
    # Surviving rows come back unchanged.
    assert resolved[2020].columns == y2020.columns
    assert resolved[2020]["fhfa_record_id"].to_list() == [6]


def _pools(n: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    return (
        pl.DataFrame({"fhlmc_loan_id": [f"L{i}" for i in range(1, n + 1)]}),
        pl.DataFrame({"fhfa_record_id": list(range(1, n + 1))}),
    )


def test_settle_keeps_gated_pairs_but_retires_every_record() -> None:
    mbs, fhfa = _pools(5)  # L5 / record 5 never paired
    pairs = pl.DataFrame(
        {
            "fhlmc_loan_id": ["L1", "L2", "L3", "L4"],
            "fhfa_record_id": [1, 2, 3, 4],
            "zip3_consistent": pl.Series([True, False, None, None], dtype=pl.Boolean),
            "match_quality_tier": [1, 1, 3, 2],
        }
    )
    accepted, mbs, fhfa = settle(mbs, fhfa, pairs, "fhlmc_loan_id", recovery_gate(True, 2))

    # L2 fails on zip3, L3 on tier; a null zip3 passes (L4).
    assert accepted["fhlmc_loan_id"].to_list() == ["L1", "L4"]
    # Rejected pairs are retired, not re-offered: only the unpaired records remain.
    assert mbs["fhlmc_loan_id"].to_list() == ["L5"]
    assert fhfa["fhfa_record_id"].to_list() == [5]


def test_recovery_gate_without_constraints_accepts_everything() -> None:
    pairs = pl.DataFrame(
        {
            "zip3_consistent": pl.Series([True, False, None], dtype=pl.Boolean),
            "match_quality_tier": [1, 3, 3],
        }
    )
    assert pairs.filter(recovery_gate(require_zip3=False)).height == 3
    assert pairs.filter(recovery_gate(require_zip3=True)).height == 2


def _one_pair_per_pass(mbs: pl.DataFrame, fhfa: pl.DataFrame) -> pl.DataFrame:
    """Fake matcher: pair the first remaining loan with the first remaining record."""
    if mbs.is_empty() or fhfa.is_empty():
        return pl.DataFrame(schema={"fhlmc_loan_id": pl.String, "fhfa_record_id": pl.Int64})
    return pl.DataFrame(
        {"fhlmc_loan_id": [mbs["fhlmc_loan_id"][0]], "fhfa_record_id": [fhfa["fhfa_record_id"][0]]}
    )


def test_iterate_to_fixpoint_concatenates_every_pass_until_empty() -> None:
    mbs, fhfa = _pools(3)
    out = iterate_to_fixpoint(_one_pair_per_pass, mbs, fhfa, "fhlmc_loan_id")
    assert out["fhlmc_loan_id"].to_list() == ["L1", "L2", "L3"]
    assert out["fhfa_record_id"].to_list() == [1, 2, 3]

    # The pass budget caps the recovery rather than raising.
    capped = iterate_to_fixpoint(_one_pair_per_pass, mbs, fhfa, "fhlmc_loan_id", max_iter=2)
    assert capped.height == 2


def test_iterate_to_fixpoint_empty_matcher_returns_schema() -> None:
    mbs, fhfa = _pools(2)

    def nothing(mbs: pl.DataFrame, fhfa: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame(schema={"fhlmc_loan_id": pl.String, "fhfa_record_id": pl.Int64})

    out = iterate_to_fixpoint(nothing, mbs, fhfa, "fhlmc_loan_id")
    assert out.height == 0
    assert out.columns == ["fhlmc_loan_id", "fhfa_record_id"]


def test_filter_acquisition_consistent_window_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    # Acquisition year 2020: the window is [Nov 1 2019, Jan 1 2021], both ends inclusive.
    periods = pl.LazyFrame(
        {
            "loan_id": ["oct", "nov", "mid", "jan", "feb"],
            "first_reporting_period": [
                date(2019, 10, 1),
                date(2019, 11, 1),
                date(2020, 6, 1),
                date(2021, 1, 1),
                date(2021, 2, 1),
            ],
        }
    )
    monkeypatch.setattr(
        inputs, "load_first_reporting_period", lambda cohorts, overwrite=False: periods
    )
    pool = pl.DataFrame({"loan_id": ["oct", "nov", "mid", "jan", "feb", "no_perf"]})

    kept = inputs.filter_acquisition_consistent(pool, 2020)

    assert kept["loan_id"].to_list() == ["nov", "mid", "jan", "no_perf"]
    assert "first_reporting_period" not in kept.columns


# ---------------------------------------------------------------------------
# FHFA->UMBS ARM crosswalk (2026-09): one pass pooled over acquisition years.
# The origination years a record is offered under decide which loans can meet
# it, and pooling holds only if an FHFA record is identified with its year.
# ---------------------------------------------------------------------------


def test_offer_origination_years_by_flag() -> None:
    fhfa = pl.LazyFrame(
        {"fhfa_record_id": [1, 2, 3], "year": [2022, 2022, 2022], "orig_year_flag": [1, 2, None]},
        schema_overrides={"orig_year_flag": pl.Int8},
    )

    offered = offer_origination_years(fhfa, seasoned_years=2).collect()
    years = dict(
        offered.group_by("fhfa_record_id").agg(pl.col("origination_year").sort()).iter_rows()
    )

    assert years[1] == [2022]
    assert years[2] == [2020, 2021, 2022]  # seasoned, plus the acquisition year itself
    assert years[3] == [None]  # an unknown flag admits no year, so joins nothing


def test_pooled_pass_keeps_a_record_number_apart_across_years() -> None:
    """Record 1 of 2021 and record 1 of 2022 are different loans' records.

    Keyed by number alone they read as one record claimed by two loans, and the
    resolution drops both.
    """
    loans, record = _match_fixture([None, None])
    loans = loans.with_columns(
        pl.when(pl.col("loan_id") == "L0").then(2021).otherwise(2022).alias("origination_year")
    )
    fhfa = offer_origination_years(
        pl.concat([record, record.with_columns(pl.lit(2022).alias("year"))], how="diagonal_relaxed")
    )

    pooled = match_pass(loans, fhfa, FNMA, keys=ROUNDS_ARM[0].keys, record_key=RECORD_KEY).collect()

    assert sorted(pooled.select("loan_id", "year").rows()) == [("L0", 2021), ("L1", 2022)]
    assert match_pass(loans, fhfa, FNMA, keys=ROUNDS_ARM[0].keys).collect().is_empty()
