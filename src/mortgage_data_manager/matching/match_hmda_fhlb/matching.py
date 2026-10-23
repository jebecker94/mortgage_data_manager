"""HMDA <-> FHLB AMA matching: one round engine for both eras.

The rounds themselves are in ``rounds.py``. Every round does the same three things: it
joins an HMDA pool to the FHLB acquisitions on the round's exact keys, screens the
candidate pairs on the fields the join does not key on, and keeps the pairs that are
one-to-one on both sides (`drop_ambiguous`). Each round runs on the loans earlier rounds
left unclaimed, and its ``match_round`` is its position in the era's tuple.

What differs between eras is the screen, because the pre-2018 AMA file has its own schema
(column names, code spaces, a Bank district field). Each era therefore has one screen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.core.reference import to_planning_region
from mortgage_data_manager.core.scalars import scalar_int
from mortgage_data_manager.core.types import null_sentinels
from mortgage_data_manager.matching.match_hmda_fhlb import config
from mortgage_data_manager.matching.match_hmda_fhlb.rounds import (
    ROUNDS_POST2018,
    ROUNDS_PRE2018,
    MatchRound,
    Tolerances,
)
from mortgage_data_manager.matching.resolve import drop_ambiguous
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)

HMDA_ID = "HMDAIndex"
FHLB_ID = "LoanCharacteristicsID"

#: AMA discloses the fee-inclusive LTV for government loans while HMDA reports the base
#: CLTV: proven on FHLB<->GNMA matched pairs. Financed-fee factors by HMDA loan_type: FHA
#: upfront MIP; VA funding fee (pre/post-2020, first/subsequent use); USDA/RHS guarantee fee.
LTV_FEE_FACTORS: dict[int, tuple[float, ...]] = {
    2: (1.0175,),
    3: (1.0215, 1.023, 1.033, 1.036),
    4: (1.01,),
}
#: Slack for the fee-adjusted LTV comparison (integer-rounding drift), whatever the round.
LTV_FEE_TOLERANCE = 1.05


Screen = Callable[[pl.LazyFrame, Tolerances], pl.LazyFrame]
"""``(candidate pairs, tolerances) -> one-to-one pairs``: an era's filter and resolution."""


# =============================================================================
# Screens
# =============================================================================


def _not_beyond(left: str, right: str, tolerance: float | None) -> pl.Expr:
    """``|left - right|`` not above ``tolerance``; a null gap fails (NaN passes).

    A ``None`` tolerance means the field is not compared, so every pair passes.
    """
    if tolerance is None:
        return pl.lit(True)
    return ~((pl.col(left) - pl.col(right)).abs() > tolerance)


def _sex_consistent(borrower1: str, borrower2: str) -> list[pl.Expr]:
    """HMDA and FHLB borrower sexes do not contradict (1 male / 2 female; others neutral)."""
    return [
        ~((pl.col(hmda) == a) & (pl.col(fhlb) == b))
        for hmda, fhlb in (("applicant_sex", borrower1), ("co_applicant_sex", borrower2))
        for a, b in ((1, 2), (2, 1))
    ]


def _one_to_one(lf: pl.LazyFrame, fhlb_key: str | list[str]) -> pl.LazyFrame:
    """Keep the pairs whose HMDA loan and FHLB loan appear on no other row.

    The drop_nulls is the explicit form of what the earlier ``Expr.count().over(key)``
    spelling did implicitly: it counts non-null values, so a null-keyed row scored 0 and
    never passed the == 1 test. Both keys are null-free in the current silver, so this is
    a no-op today and a guard tomorrow.
    """
    return drop_ambiguous(lf.drop_nulls(subset=[HMDA_ID, FHLB_ID]), HMDA_ID, fhlb_key)


def _screen_post2018(lf: pl.LazyFrame, tolerances: Tolerances) -> pl.LazyFrame:
    """Filter post-2018 candidate pairs on the unkeyed fields, then resolve them 1:1.

    The LTV comparison is program-aware: AMA discloses the fee-inclusive LTV for
    government loans (financed FHA UFMIP, VA funding fee, RHS guarantee fee) while HMDA
    reports the base CLTV, so a pair passes on either the base comparison at the round
    tolerance or a fee-adjusted comparison at the tight fee tolerance. A null base
    comparison drops the row.

    Args:
        lf: Joined HMDA-FHLB candidate pairs.
        tolerances: The round's tolerances.

    Returns:
        The surviving pairs, one-to-one on both sides.
    """
    lf = lf.with_columns(
        [
            null_sentinels(pl.col(c), [-99999]).cast(pl.Float64).alias(c)
            for c in (
                "income",
                "loan_term",
                "interest_rate",
                "combined_loan_to_value_ratio",
                "debt_to_income_ratio",
            )
        ]
    )
    hmda_ltv, fhlb_ltv = pl.col("combined_loan_to_value_ratio"), pl.col("LTVRatioPercent")
    fee_pass = pl.lit(False)
    for loan_type, factors in LTV_FEE_FACTORS.items():
        fee_gap = pl.min_horizontal(*[(fhlb_ltv - hmda_ltv * f).abs() for f in factors])
        fee_pass = fee_pass | ((pl.col("loan_type") == loan_type) & (fee_gap <= LTV_FEE_TOLERANCE))
    dti = pl.col("debt_to_income_ratio")
    keep = [
        # Purpose: an HMDA purchase (1) never pairs with FHLB purpose 2 or 6, nor an HMDA
        # refinance (31, 32) with an FHLB purchase (1).
        ~((pl.col("loan_purpose") == 1) & pl.col("LoanPurposeType").is_in([6, 2])),
        ~(pl.col("loan_purpose").is_in([31, 32]) & (pl.col("LoanPurposeType") == 1)),
        _not_beyond("TotalMonthlyIncomeAmount", "income", tolerances.income),
        _not_beyond("LoanAmortizationMaxTermMonths", "loan_term", tolerances.term),
        _not_beyond("NoteRatePercent", "interest_rate", tolerances.rate),
        _not_beyond("loan_amount", "NoteAmount", tolerances.amount),
        ~(((fhlb_ltv - hmda_ltv).abs() > tolerances.ltv) & ~fee_pass.fill_null(False)),
        ~(
            ((pl.col("TotalDebtExpenseRatioPercent") - dti).abs() > tolerances.dti)
            & (dti >= 36)
            & (dti < 50)
        ),
        *_sex_consistent("Borrower1SexType", "Borrower2SexType"),
    ]
    return _one_to_one(lf.filter(*keep), FHLB_ID)


def _screen_pre2018(lf: pl.LazyFrame, tolerances: Tolerances) -> pl.LazyFrame:
    """Filter pre-2018 candidate pairs and resolve them 1:1.

    Purpose code spaces differ: FHLB 1 = purchase, 2 = refinance; pre-2018 HMDA 1 =
    purchase, 2 = home improvement, 3 = refinance. Equality therefore admits purchase
    with purchase and an FHLB refinance with an HMDA home improvement (a
    home-improvement loan the Bank acquired as a refinance); the second arm is the plain
    refinance mapping. The Bank district must agree where the FHLB side reports one.
    ``LoanCharacteristicsID`` repeats across years, so resolution keys on it with the year.

    Loan type must agree; both sides share one code space (1 conventional, 2 FHA, 3 VA,
    4 FSA/RHS). Occupancy is not compared: AMA and pre-2018 HMDA code it differently.

    Loans HMDA reports sold to Fannie Mae, Ginnie Mae or Freddie Mac contradict a Bank's AMA
    acquisition. They are excluded with the other filters, before the 1:1 step, so an
    excluded candidate is never counted as a rival.

    Args:
        lf: Joined HMDA-FHLB candidate pairs.
        tolerances: The round's tolerances.

    Returns:
        The surviving pairs, one-to-one on both sides.
    """
    purpose = pl.col("Loan Purpose Type")
    district = pl.col("FHFBID_right")  # the FHLB side's copy, suffixed by the join
    lf = lf.filter(
        (purpose == pl.col("loan_purpose")) | ((purpose == 2) & (pl.col("loan_purpose") == 3)),
        (pl.col("Total Yearly Income Amount") - pl.col("income").cast(pl.Float64)).abs()
        <= tolerances.income,
        *_sex_consistent("Borrower 1 Gender Type", "Borrower 2 Gender Type"),
        (pl.col("FHFBID") == district) | district.is_null() | (district == 0),
        ~pl.col("purchaser_type").is_in([1, 2, 3]),
        pl.col("loan_type") == pl.col("Loan Type"),
    )
    return _one_to_one(lf, [FHLB_ID, "activity_year"])


# =============================================================================
# Rounds
# =============================================================================


# =============================================================================
# Rounds
# =============================================================================


def _match_round(
    round_: MatchRound, hmda: pl.LazyFrame, fhlb: pl.LazyFrame, screen: Screen
) -> pl.DataFrame:
    """Join, screen and resolve one round."""
    hmda_keys, fhlb_keys = zip(*round_.keys, strict=True)
    pairs = hmda.join(fhlb, left_on=list(hmda_keys), right_on=list(fhlb_keys), how="inner")
    return screen(pairs, round_.tolerances).collect()


def run_rounds(
    rounds: Sequence[MatchRound],
    hmda: pl.LazyFrame,
    fhlb: pl.LazyFrame,
    screen: Screen,
    *,
    fhlb_claim_key: Mapping[str, str],
    open_pool: Callable[[], pl.LazyFrame] | None = None,
) -> list[pl.DataFrame]:
    """Run an era's rounds in order, each on the loans earlier rounds left unclaimed.

    Args:
        rounds: The era's rounds; a round's ``match_round`` is its 1-based position.
        hmda: The HMDA pool (FHLB members).
        fhlb: The FHLB acquisitions.
        screen: The era's filter and 1:1 resolution.
        fhlb_claim_key: Identifies a claimed FHLB loan: each FHLB column mapped to the
            column of the matched pairs that holds its value.
        open_pool: Loads every HMDA originator, for a round with ``open_pool`` set.

    Returns:
        Each round's one-to-one pairs, with every column of both sides and ``match_round``.

    Raises:
        ValueError: If a round sets ``open_pool`` and the era has no open pool.
    """
    fhlb_cols, pair_cols = list(fhlb_claim_key), list(fhlb_claim_key.values())
    fhlb_schema = fhlb.collect_schema()
    matched_rounds: list[pl.DataFrame] = []
    for number, round_ in enumerate(rounds, start=1):
        if round_.open_pool and open_pool is None:
            raise ValueError(f"round {number} sets open_pool, which this era does not support")
        pool = open_pool() if round_.open_pool and open_pool is not None else hmda
        targets = fhlb
        if matched_rounds:
            claimed = pl.concat(
                [m.select(HMDA_ID, *pair_cols) for m in matched_rounds], how="vertical_relaxed"
            ).lazy()
            pool = pool.join(claimed.select(HMDA_ID), on=HMDA_ID, how="anti")
            targets = targets.join(
                claimed.select(pl.col(p).cast(fhlb_schema[f]) for f, p in fhlb_claim_key.items()),
                left_on=fhlb_cols,
                right_on=pair_cols,
                how="anti",
            )
        logger.info("Round %d (%s)", number, round_.description)
        matched = _match_round(round_, pool, targets, screen).with_columns(
            pl.lit(number, dtype=pl.UInt8).alias("match_round")
        )
        logger.info("Round %d: %s matches", number, f"{len(matched):,}")
        matched_rounds.append(matched)
    return matched_rounds


def apply_post2018_filters(lf: pl.LazyFrame, round_number: int = 1) -> pl.LazyFrame:
    """Screen joined post-2018 candidate pairs with one round's tolerances.

    Args:
        lf: Joined HMDA-FHLB candidate pairs.
        round_number: The post-2018 round whose tolerances apply (1, 2 or 3).

    Returns:
        The surviving pairs, one-to-one on both sides.
    """
    return _screen_post2018(lf, ROUNDS_POST2018[round_number - 1].tolerances)


def _load_all_hmda_originations(max_year: int) -> pl.LazyFrame:
    """Load all HMDA originations (not restricted to FHLB members).

    Used by the open round to find correspondent originators who sell through FHLB
    member intermediaries.

    Args:
        max_year: Maximum year to load.

    Returns:
        LazyFrame of all HMDA originations with action_taken=1 and
        purchaser_type != 3 (drops loans reported sold to Freddie Mac).
    """
    lf = (
        scan_hmda_years("post2018", 2018, max_year)
        .filter((pl.col("action_taken") == 1) & (pl.col("purchaser_type") != 3))
        .select(config.HMDA_POST2018_COLS)
    )
    # Normalize CT tracts to planning-region convention (HMDA switched in 2024)
    # so they line up with the FHLB side after its own normalization.
    return to_planning_region(lf, tract_col="census_tract")


# =============================================================================
# Entry points
# =============================================================================


def _log_by_year(number: int, matched: pl.DataFrame, fhlb_counts: pl.DataFrame) -> None:
    """Log a post-2018 round's match rate by FHLB acquisition year."""
    by_year = fhlb_counts.join(
        matched.group_by("data_year").len().rename({"len": "matches"}), on="data_year", how="left"
    ).fill_null(0)
    for row in by_year.iter_rows(named=True):
        logger.info(
            "Round %s - Year %s: %s matches (%s of %s FHLB loans)",
            number,
            row["data_year"],
            f"{row['matches']:,}",
            f"{row['matches'] / row['len']:.1%}",
            f"{row['len']:,}",
        )


def _log_membership(matched: pl.DataFrame, crosswalk_folder: Path) -> None:
    """Split an open round's matches into FHLB members and non-members (per LEI-year)."""
    member_lei_years = (
        pl.read_parquet(crosswalk_folder / "hmda_fhlb_lender_crosswalk_post2018.parquet")
        .select(pl.col("LEI").alias("lei"), pl.col("Year").alias("activity_year"))
        .unique()
    )
    n_member = matched.join(
        member_lei_years.cast({"activity_year": matched.schema["activity_year"]}),
        on=["lei", "activity_year"],
        how="semi",
    ).height
    logger.info(
        "Open round: %s from FHLB members, %s from non-members (correspondent originators)",
        f"{n_member:,}",
        f"{len(matched) - n_member:,}",
    )


def run_rounds_pre2018(match_folder: Path, crosswalk_folder: Path | None = None) -> pl.DataFrame:
    """Execute FHLB matching for pre-2018 data (the rounds in ``ROUNDS_PRE2018``).

    Args:
        match_folder: Directory containing cleaned data files.
        crosswalk_folder: Directory for crosswalk outputs. Defaults to config.CROSSWALK_DIR.

    Returns:
        Matched records.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    logger.info("Matching HMDA to FHLB (pre-2018)")

    hmda = pl.scan_parquet(match_folder / "hmda_fhlb_members_pre2018.parquet")
    fhlb = pl.scan_parquet(match_folder / "fhlb_acquisitions_pre2018.parquet").with_columns(
        (pl.col("Round Loan Amount") * 1000).cast(pl.Int64),
        pl.col("data_year").cast(pl.Int64),
    )
    # LoanCharacteristicsID is reused across pre-2018 years, so an FHLB loan is the id
    # with its year (the FHLB data_year, joined into activity_year).
    matched = pl.concat(
        run_rounds(
            ROUNDS_PRE2018,
            hmda,
            fhlb,
            _screen_pre2018,
            fhlb_claim_key={FHLB_ID: FHLB_ID, "data_year": "activity_year"},
        ),
        how="diagonal_relaxed",
    )
    matched.write_parquet(match_folder / "hmda_fhlb_matches_pre2018.parquet")

    with atomic_output(crosswalk_folder / "hmda_fhlb_crosswalk_pre2018.parquet") as tmp:
        matched.select(HMDA_ID, FHLB_ID, "activity_year", "match_round").write_parquet(tmp)

    logger.info("Pre-2018 matching complete: %s matches", f"{len(matched):,}")
    return matched


def run_rounds_post2018(match_folder: Path, crosswalk_folder: Path | None = None) -> pl.DataFrame:
    """Execute FHLB matching for post-2018 data (the rounds in ``ROUNDS_POST2018``).

    Args:
        match_folder: Directory containing cleaned data files.
        crosswalk_folder: Directory for crosswalk outputs. Defaults to config.CROSSWALK_DIR.

    Returns:
        Matched records from every round, on the columns all rounds share.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)

    # Normalize CT tracts to the planning-region convention on both sides: HMDA switched
    # to planning regions in 2024, while FHLB AMA reports a mix of both conventions for
    # CT, so without this the exact census_tract join silently drops CT records whose two
    # sides disagree. Idempotent (PR/non-CT tracts pass through).
    members = to_planning_region(
        pl.scan_parquet(match_folder / "hmda_fhlb_members_post2018.parquet"),
        tract_col="census_tract",
    )
    fhlb = to_planning_region(
        pl.scan_parquet(match_folder / "fhlb_acquisitions_post2018.parquet"),
        tract_col="Census Tract String",
    )
    fhlb_counts = fhlb.group_by("data_year").len().sort("data_year").collect()

    open_pool_year = scalar_int(fhlb_counts["data_year"].max())
    rounds = run_rounds(
        ROUNDS_POST2018,
        members,
        fhlb,
        _screen_post2018,
        fhlb_claim_key={FHLB_ID: FHLB_ID},
        open_pool=lambda: _load_all_hmda_originations(open_pool_year),
    )
    for number, (round_, matched) in enumerate(zip(ROUNDS_POST2018, rounds, strict=True), 1):
        _log_by_year(number, matched, fhlb_counts)
        if round_.open_pool:
            _log_membership(matched, crosswalk_folder)

    with atomic_output(crosswalk_folder / "hmda_fhlb_crosswalk_post2018.parquet") as tmp:
        pl.concat(
            [df.select(HMDA_ID, FHLB_ID, "match_round") for df in rounds], how="diagonal_relaxed"
        ).write_parquet(tmp)

    shared = sorted(set.intersection(*(set(df.columns) for df in rounds)))
    result = pl.concat([df.select(shared) for df in rounds], how="diagonal_relaxed")
    result.write_parquet(match_folder / "hmda_fhlb_matches_post2018.parquet")
    logger.info("Total post-2018 matches: %s", f"{len(result):,}")
    return result
