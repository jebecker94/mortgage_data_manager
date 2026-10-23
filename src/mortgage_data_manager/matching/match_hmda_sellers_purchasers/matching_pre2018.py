"""Seller-purchaser matching for the 2007-2017 HMDA era.

The legacy HMDA files carry a different column structure and simpler
demographic fields than the post-2018 release, so this era has its own engine
rather than a round configuration on the post-2018 one: that engine always runs
fee matching, LEI-keyed joins and the post-2018 loader and demographics, none
of which exist for these years.

One engine, `_run_round`, runs every round from an `EraRound` spec in ``rounds.py``. Each round
joins originations (sellers, ``action_taken == 1``) to purchases
(``action_taken == 6``) on its match columns, then applies the same sequence:

1. pre-uniqueness: an income tolerance (``9999``-thousand income is missing and
   passes) and a different-lender requirement;
2. `keep_uniques`: purchases and originations matched exactly once;
3. post-uniqueness: the tight income tolerance, sex / race / ethnicity
   compatibility, no secondary sales (``purchaser_type_p <= 4``), and the round's
   own extra filters.

The rounds (`ROUNDS_PRE2018`) differ only in data:

- Round 1 matches each activity year alone on a $10k loan-amount bin, offering
  sellers in the lower half of a bin to the bin below as well (rounding).
- Round 2 pools the years left unmatched by round 1, keys on the exact loan
  amount and property type, and admits a purchase in the origination year or
  the year after: a same-year pair must report a sale, a next-year pair must not.

Amounts are expressed in dollars throughout, matching HMDA silver. The
tolerances below were originally written against the raw files, which report
``loan_amount`` and ``income`` in thousands, so each is scaled by
``DOLLARS_PER_THOUSAND`` and the missing-income code is ``9999`` thousand.
"""

from __future__ import annotations

import logging
import shutil
from functools import reduce
from operator import or_
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import null_sentinels
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.config import (
    HMDASellersPurchasersMatchingConfig as Config,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.rounds import (
    DOLLARS_PER_THOUSAND,
    ROUNDS_PRE2018,
    EraRound,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.utils import (
    save_crosswalk,
)
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

if TYPE_CHECKING:
    from mortgage_data_manager.sources.hmda.config import HMDAPeriod

logger = get_logger(__name__)

#: Silver era this matcher reads.
HMDA_PERIOD: HMDAPeriod = "period_2007_2017"

#: Raw-file sentinel for "income not available", carried into silver as dollars.
INCOME_MISSING_CODE = 9_999 * DOLLARS_PER_THOUSAND

#: Width of the loan-amount bin round 1 joins on.
LOAN_AMOUNT_BIN_WIDTH = 10 * DOLLARS_PER_THOUSAND

#: Columns no round reads, dropped before the seller-purchaser self-join.
_UNUSED_COLUMNS = [
    "denial_reason_1", "denial_reason_2", "denial_reason_3", "edit_status",
    "application_date_indicator", "tract_one_to_four_family_units",
    "derived_loan_product_type", "derived_dwelling_category", "derived_ethnicity",
    "derived_race", "derived_sex", "lien_status", "rate_spread", "hoepa_status",
]  # fmt: skip

#: Loan identity per side, for `keep_uniques`; the subset present in the frame is used.
_LOAN_ID = [
    "activity_year",
    "respondent_id",
    "agency_code",
    "sequence_number",
    "HMDAIndex",
]

#: Race codes 7 (not applicable) and 8 (no co-applicant) are compatible with any race.
_RACE_NA = [7, 8]


def _era_crosswalk_dir(save_folder: str | Path | None) -> Path:
    """The era's crosswalk root, read at call time so a caller can repoint it."""
    if save_folder is not None:
        return Path(save_folder)
    # get_output_dir_for_round names the era subdirectory; the match_round
    # partition below it is appended by save_crosswalk.
    return Config.get_output_dir_for_round(1, post2018=False).parent


def _read_rounds(save_folder: Path, max_round: int, min_round: int = 1) -> pl.LazyFrame:
    """Pairs already written for a span of rounds, empty when none are.

    Args:
        save_folder: Era crosswalk root holding the ``match_round=`` partitions.
        max_round: Highest round to read, inclusive.
        min_round: Lowest round to read, inclusive.

    Returns:
        A lazy frame over the matching partitions; empty if none exist.
    """
    files = [
        file
        for match_round in range(min_round, max_round + 1)
        for file in sorted((save_folder / f"match_round={match_round}").glob("**/*.parquet"))
    ]
    if not files:
        return pl.LazyFrame()
    return pl.scan_parquet(files, hive_partitioning=False)


def _skip_or_clear(save_folder: Path, match_round: int, overwrite: bool) -> int | None:
    """The existing pair count when a round should be skipped, else None.

    On overwrite only this round's partition subtree is removed, leaving the
    other rounds of the era crosswalk in place.

    Args:
        save_folder: Era crosswalk root holding the ``match_round=`` partitions.
        match_round: Round about to be built.
        overwrite: Rebuild the round even if its partition already exists.

    Returns:
        The pairs already present if the round is skipped, otherwise None.
    """
    round_dir = save_folder / f"match_round={match_round}"
    if not should_process_file(round_dir, overwrite):
        pairs = int(
            _read_rounds(save_folder, max_round=match_round, min_round=match_round)
            .select(pl.len())
            .collect()
            .item()
        )
        logger.info(
            "Round %s already built (%s pairs); pass overwrite=True to rebuild",
            match_round,
            f"{pairs:,}",
        )
        return pairs
    if round_dir.exists():
        shutil.rmtree(round_dir)
    return None


def load_data(
    data_folder: str | Path | None = None,
    min_year: int = Config.MIN_YEAR_PRE2018,
    max_year: int = Config.MAX_YEAR_PRE2018,
    matched_seller_indices: pl.LazyFrame | None = None,
    matched_purchaser_indices: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """Load 2007-2017 HMDA originations and purchases into a single frame.

    Release (``file_type``) selection is delegated to `hmda.load`, which takes
    the most mature release present for each year.

    Args:
        data_folder: Era directory to read; None uses the configured silver layout.
        min_year: Earliest activity year to include (inclusive).
        max_year: Latest activity year to include (inclusive).
        matched_seller_indices: HMDAIndex values for sellers already matched. If provided, these will
            be excluded from the loaded data.
        matched_purchaser_indices: HMDAIndex values for purchasers already matched. If provided, these
            will be excluded from the loaded data.

    Returns:
        Combined seller and purchaser records for the requested year range.
    """
    df = scan_hmda_years(
        HMDA_PERIOD,
        min_year,
        max_year,
        root=Path(data_folder) if data_folder is not None else None,
    )
    # Originations and purchases only, and only originations not sold to the agencies.
    df = df.filter(pl.col("action_taken").is_in([1, 6]))
    df = df.filter((pl.col("action_taken") == 6) | ~pl.col("purchaser_type").is_in([1, 2, 3, 4]))
    for matched, side in (
        (matched_seller_indices, "s"),
        (matched_purchaser_indices, "p"),
    ):
        if matched is not None:
            index = f"HMDAIndex_{side}"
            df = df.join(matched.select(index), left_on="HMDAIndex", right_on=index, how="anti")
    return df


def keep_uniques(lf: pl.LazyFrame, one_to_one: bool = True) -> pl.LazyFrame:
    """Keep unique matches for pre-2018 data.

    Args:
        lf: Data before unique matches are enforced.
        one_to_one: Whether to only keep unique seller matches. The default is True.

    Returns:
        Data after unique matches are enforced.
    """
    schema_names = lf.collect_schema().names()
    loan_id_s = [f"{c}_s" for c in _LOAN_ID if f"{c}_s" in schema_names]
    loan_id_p = [f"{c}_p" for c in _LOAN_ID if f"{c}_p" in schema_names]

    lf = lf.with_columns(
        pl.col("activity_year_s").count().over(loan_id_s).alias("count_index_s"),
        pl.col("activity_year_p").count().over(loan_id_p).alias("count_index_p"),
    )

    if logger.isEnabledFor(logging.DEBUG):
        cardinality = lf.group_by(["count_index_s", "count_index_p"]).agg(pl.len()).collect()
        logger.debug("Match cardinality counts:\n%s", cardinality)

    lf = lf.filter(pl.col("count_index_p") == 1)
    if one_to_one:
        lf = lf.filter(pl.col("count_index_s") == 1)
    else:
        # Allow one-to-many for secondary sales
        lf = lf.with_columns(
            (pl.col("purchaser_type_p") > 4).cast(pl.Int8).max().over(loan_id_s).alias("_secondary")
        )
        lf = lf.filter(
            (pl.col("count_index_s") == 1)
            | ((pl.col("count_index_s") == 2) & (pl.col("_secondary") == 1))
        ).drop("_secondary")
    return lf.drop(["count_index_s", "count_index_p"])


def _both_present(schema_names: list[str], column: str) -> bool:
    """Whether the seller and purchaser copies of ``column`` are both in the frame."""
    return f"{column}_s" in schema_names and f"{column}_p" in schema_names


def match_sex_pre2018(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Drop seller/purchaser pairs with incompatible sex designations (pre-2018).

    Pre-2018 sex codes:
    - 1: Male
    - 2: Female
    - 3: Information not provided
    - 4: Not applicable
    - 5: No co-applicant

    Args:
        lf: Candidate matches with applicant_sex_* and co_applicant_sex_* columns.

    Returns:
        Subset where sex indicators are mutually consistent.
    """
    schema_names = lf.collect_schema().names()
    for person in ("applicant", "co_applicant"):
        if not _both_present(schema_names, f"{person}_sex"):
            continue
        s, p = pl.col(f"{person}_sex_s"), pl.col(f"{person}_sex_p")
        lf = lf.filter((s != 1) | (p != 2)).filter((s != 2) | (p != 1))
        if person == "co_applicant":
            # No co-applicant (5) should not match with male/female
            lf = lf.filter((s != 5) | ~p.is_in([1, 2])).filter(~s.is_in([1, 2]) | (p != 5))
    return lf


def match_race_pre2018(lf: pl.LazyFrame, strict: bool = False) -> pl.LazyFrame:
    """Perform race matches for pre-2018 data.

    Pre-2018 race codes:
    - 1-6: Specific race categories
    - 7: Not applicable
    - 8: No co-applicant

    A race reported in one side's first field must appear in any of the other
    side's five fields, unless that side's first field is 7 or 8.

    Args:
        lf: Data with unmatched races.
        strict: Whether to enforce strict race matching. The default is False.

    Returns:
        Data with matched races.
    """
    schema_names = lf.collect_schema().names()
    for person in ("applicant", "co_applicant"):
        if not _both_present(schema_names, f"{person}_race_1"):
            continue
        first = {side: pl.col(f"{person}_race_1_{side}") for side in ("s", "p")}
        if person == "co_applicant":
            # No co-applicant on one side requires none (or n/a) on the other
            lf = lf.filter((first["s"] != 8) | first["p"].is_in(_RACE_NA))
            lf = lf.filter((first["p"] != 8) | first["s"].is_in(_RACE_NA))
        for race in range(1, 7):
            for side, other in (("s", "p"), ("p", "s")):
                others = [
                    pl.col(c)
                    for c in (f"{person}_race_{i}_{other}" for i in range(2, 6))
                    if c in schema_names
                ]
                compatible = reduce(
                    or_,
                    (c == race for c in others),
                    first[other].is_in([race, *_RACE_NA]),
                )
                lf = lf.filter((first[side] != race) | compatible)
        if strict:
            lf = lf.filter(
                (first["s"] == first["p"]) | first["s"].is_in(_RACE_NA) | first["p"].is_in(_RACE_NA)
            )
    return lf


def match_ethnicity_pre2018(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Perform ethnicity matches for pre-2018 data.

    Pre-2018 ethnicity codes:
    - 1-3: Specific ethnicity categories
    - 4: Not applicable
    - 5: No co-applicant

    Args:
        lf: Data with unmatched ethnicities.

    Returns:
        Data with matched ethnicities.
    """
    schema_names = lf.collect_schema().names()
    for person in ("applicant", "co_applicant"):
        if not _both_present(schema_names, f"{person}_ethnicity"):
            continue
        s, p = pl.col(f"{person}_ethnicity_s"), pl.col(f"{person}_ethnicity_p")
        for code in range(1, 4):
            lf = lf.filter((s != code) | p.is_in([code, 4])).filter(
                (p != code) | s.is_in([code, 4])
            )
        if person == "co_applicant":
            # Co-applicant present
            lf = lf.filter((s != 5) | p.is_in([4, 5])).filter((p != 5) | s.is_in([4, 5]))
    return lf


def _prepare(lf: pl.LazyFrame, spec: EraRound) -> pl.LazyFrame:
    """Drop unused columns and, for a binned round, add the loan-amount bin."""
    lf = lf.drop([c for c in _UNUSED_COLUMNS if c in lf.collect_schema().names()])
    if spec.bin_loan_amount:
        width = LOAN_AMOUNT_BIN_WIDTH
        lf = lf.with_columns(
            (width * pl.col("loan_amount").floordiv(width) + width // 2).alias("LoanAmountBin")
        )
    return lf


def _income_within(tolerance: int) -> pl.Expr:
    """Incomes within ``tolerance`` dollars, or at least one of them missing."""
    return (pl.col("IncomeDifference").abs() <= tolerance) | pl.col("IncomeDifference").is_null()


def _match(lf: pl.LazyFrame, spec: EraRound) -> pl.LazyFrame:
    """Join sellers to purchasers and apply the round's filters around `keep_uniques`.

    Args:
        lf: Prepared originations and purchases (see `_prepare`).
        spec: The round to run.

    Returns:
        The round's accepted pairs, seller columns suffixed ``_s`` and purchaser ``_p``.
    """
    keys = list(spec.match_columns)
    lf = lf.drop_nulls(subset=keys).filter(pl.col("census_tract") != "")
    seller = lf.filter(pl.col("action_taken") == 1)
    if spec.bin_loan_amount:
        # Expand LoanAmountBins for sold loans (to catch rounding differences)
        width = LOAN_AMOUNT_BIN_WIDTH
        low_half = seller.filter(pl.col("loan_amount") % width < width // 2)
        seller = pl.concat(
            [seller, low_half.with_columns(pl.col("LoanAmountBin") - width)],
            how="diagonal_relaxed",
        )
    df = seller.join(lf.filter(pl.col("action_taken") == 6), on=keys, suffix="_p")
    df = df.rename(
        {c: f"{c}_s" for c in df.collect_schema().names() if not c.endswith("_p") and c not in keys}
    )
    if spec.pooled:
        df = df.filter(
            pl.col("activity_year_s").is_between(
                pl.col("activity_year_p") - 1, pl.col("activity_year_p")
            )
        )

    df = df.with_columns(
        [
            null_sentinels(pl.col(f"income_{side}"), [INCOME_MISSING_CODE]).alias(f"income_{side}")
            for side in ("s", "p")
        ]
    ).with_columns((pl.col("income_s") - pl.col("income_p")).alias("IncomeDifference"))
    df = df.filter(
        _income_within(spec.income_tolerance),
        # Make sure lenders are different
        (pl.col("respondent_id_s") != pl.col("respondent_id_p"))
        | (pl.col("agency_code_s") != pl.col("agency_code_p")),
    )

    df = keep_uniques(df, one_to_one=True)

    df = match_ethnicity_pre2018(match_race_pre2018(match_sex_pre2018(df)))
    return df.filter(
        _income_within(spec.income_tolerance_tight),
        pl.col("purchaser_type_p") <= 4,  # drop secondary sales
        *spec.filters,
    )


@log_execution_time
def _run_round(
    number: int,
    spec: EraRound,
    data_folder: str | Path | None,
    save_folder: str | Path | None,
    min_year: int,
    max_year: int,
    overwrite: bool,
) -> int:
    """Run one round end to end and write its ``match_round`` partition.

    A pooled round first excludes every record an earlier round matched.

    Args:
        number: The round's position in `ROUNDS_PRE2018`, its ``match_round``.
        spec: The round to run.
        data_folder: Era directory to read; None uses the configured silver layout.
        save_folder: Crosswalk root to write under; None uses the configured era directory.
        min_year: Inclusive first activity year to process.
        max_year: Inclusive last activity year to process.
        overwrite: Rebuild the round even if its partition already exists.

    Returns:
        The number of pairs written, or the pairs already present when skipped.

    Raises:
        FileNotFoundError: If a later round runs before the rounds it builds on.
    """
    save_folder = _era_crosswalk_dir(save_folder)
    existing = _skip_or_clear(save_folder, match_round=number, overwrite=overwrite)
    if existing is not None:
        return existing

    matched = None
    if number > 1:
        matched = _read_rounds(save_folder, max_round=number - 1).collect()
        if matched.is_empty():
            prior = number - 1
            raise FileNotFoundError(
                f"No round-{prior} pairs under {save_folder}; run round {prior} before round {number}."
            )

    frames = []
    for year in range(min_year, max_year + 1):
        logger.info("Matching HMDA sellers and purchasers for year: %s", year)
        lf = load_data(
            data_folder,
            min_year=year,
            max_year=year,
            matched_seller_indices=None if matched is None else matched.lazy(),
            matched_purchaser_indices=None if matched is None else matched.lazy(),
        )
        if spec.pooled:
            frames.append(_prepare(lf, spec).collect())
        else:
            pairs_year = _match(_prepare(lf, spec), spec).collect()
            logger.info("Adding %d matches from year %s", len(pairs_year), year)
            frames.append(pairs_year)

    df = pl.concat(frames, how="diagonal_relaxed")
    if spec.pooled:
        df = _match(df.lazy(), spec).collect()

    save_crosswalk(df.lazy(), save_folder, match_round=number)
    logger.info("Round %s: wrote %s pairs under %s", number, f"{len(df):,}", save_folder)
    return len(df)


def run_round_pre2018(
    round_num: int,
    data_folder: str | Path | None = None,
    save_folder: str | Path | None = None,
    min_year: int = Config.MIN_YEAR_PRE2018,
    max_year: int = Config.MAX_YEAR_PRE2018,
    overwrite: bool = False,
) -> int:
    """Run a single 2007-2017 matching round by its position in `ROUNDS_PRE2018`.

    Args:
        round_num: The round's 1-based position.
        data_folder: Era directory to read; None uses the configured silver layout.
        save_folder: Crosswalk root to write under; None uses the configured
            ``crosswalk/hmda_sellers_purchasers/hmda_sellers_purchasers_crosswalk_pre2018`` directory.
        min_year: Inclusive first activity year to process.
        max_year: Inclusive last activity year to process.
        overwrite: Rebuild the round even if its partition already exists.

    Returns:
        The number of pairs the round wrote, or already held.

    Raises:
        ValueError: If round_num is not a position in `ROUNDS_PRE2018`.
    """
    if not 1 <= round_num <= len(ROUNDS_PRE2018):
        raise ValueError(f"Invalid round {round_num}. Valid rounds: 1-{len(ROUNDS_PRE2018)}")
    return _run_round(
        round_num,
        ROUNDS_PRE2018[round_num - 1],
        data_folder,
        save_folder,
        min_year,
        max_year,
        overwrite,
    )


def run_matching_pre2018(
    data_folder: str | Path | None = None,
    save_folder: str | Path | None = None,
    min_year: int = Config.MIN_YEAR_PRE2018,
    max_year: int = Config.MAX_YEAR_PRE2018,
    rounds: list[int] | None = None,
    overwrite: bool = False,
) -> dict[int, int]:
    """Run the 2007-2017 rounds in order.

    Round 2 consumes round 1's partition, so a subset that omits round 1
    requires it to have been built by an earlier call.

    Args:
        data_folder: Era directory to read; None uses the configured silver layout.
        save_folder: Crosswalk root to write under; None uses the configured
            ``crosswalk/hmda_sellers_purchasers/hmda_sellers_purchasers_crosswalk_pre2018`` directory.
        min_year: Inclusive first activity year to process.
        max_year: Inclusive last activity year to process.
        rounds: Round positions to run; None runs every round in `ROUNDS_PRE2018`.
        overwrite: Rebuild each round even if its partition already exists.

    Returns:
        Pairs written per round, keyed by round number.
    """
    Config.ensure_directories()
    selected = list(range(1, len(ROUNDS_PRE2018) + 1)) if rounds is None else rounds
    return {
        round_num: run_round_pre2018(
            round_num,
            data_folder=data_folder,
            save_folder=save_folder,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
        )
        for round_num in selected
    }


__all__ = [
    "load_data",
    "keep_uniques",
    "match_sex_pre2018",
    "match_race_pre2018",
    "match_ethnicity_pre2018",
    "run_matching_pre2018",
    "run_round_pre2018",
]
