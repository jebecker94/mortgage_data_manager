"""Post-2018 seller-purchaser matching: the engine that runs the rounds in ``rounds.py``.

Every round pairs HMDA originations (sellers, ``_s``) with purchased-loan records
(purchasers, ``_p``) on exact match columns, screens the candidate pairs, resolves them
(`keep_uniques`) and writes them to the crosswalk with its ``match_round``, the round's
position in `ROUNDS_POST2018`. Records an earlier round matched never reach a later one.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import clear_partitions, should_process_file
from mortgage_data_manager.core.reference.ct_planning_region import to_planning_region
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.config import (
    HMDASellersPurchasersMatchingConfig as Config,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.rounds import (
    ROUNDS_POST2018,
    Round,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.utils import (
    add_purchase_indicator,
    apply_drop_observation_cleanup,
    apply_quality_match_filters,
    drop_zeros,
    filter_generous_fee_match,
    filter_refi_types,
    filter_strict_fee_match,
    get_purchaser_type_counts,
    keep_uniques,
    load_unmatched_data,
    match_demographics,
    merge_sellers_and_purchasers,
    numeric_matches,
    numeric_matches_post_unique,
    perform_fee_matches,
    replace_missing_values,
    save_crosswalk,
    split_originations_purchases,
    weak_numeric_matches,
)

logger = get_logger(__name__)


# =============================================================================
# Engine
# =============================================================================


def _run_pass(
    number: int, round_: Round, data_folder: Path, save_folder: Path, min_year: int, max_year: int
) -> None:
    """Join, screen, resolve and save one round over one year range."""
    # A constraint learned from earlier rounds' crosswalk files.
    purchaser_types = (
        get_purchaser_type_counts(
            match_folder=save_folder,
            data_folder=data_folder,
            match_round=round_.purchaser_types_from,
        )
        if round_.purchaser_types_from is not None
        else None
    )

    df = load_unmatched_data(
        data_folder=data_folder,
        crosswalk_folder=save_folder,
        match_round=number,
        min_year=min_year,
        max_year=max_year,
    )
    if round_.purchaser_type_exclude:
        df = df.filter(
            (pl.col("action_taken") == 6)
            | (~pl.col("purchaser_type").is_in(round_.purchaser_type_exclude))
        )
    df = replace_missing_values(df)
    # Normalize CT tracts to the planning-region convention. HMDA switched CT tract
    # GEOIDs from county-based (09001-09015) to planning-region-based (09110-09190) at
    # 2023->2024, so cross-year CT pairs straddling it would otherwise fail the exact
    # join. A 1:1 bijection and idempotent (PR/non-CT tracts pass through).
    df = to_planning_region(df)
    if round_.drop_zeros:
        df = drop_zeros(df)
    match_columns = list(round_.match_columns)
    if "i_Purchase" in match_columns:
        df = add_purchase_indicator(df)
    df = df.drop_nulls(subset=match_columns)
    if "census_tract" in match_columns:
        df = df.filter(~pl.col("census_tract").is_in(["", "NA"]))

    df = merge_sellers_and_purchasers(*split_originations_purchases(df), match_columns)

    if purchaser_types is not None:
        df = df.join(
            purchaser_types,
            on=["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"],
            how="left",
        ).with_columns(
            pl.col("CountLEIMatches").is_not_null().cast(pl.Int8).alias("i_PurchaserTypeMatch")
        )
        df = df.filter((pl.col("i_PurchaserTypeMatch") == 1) | (pl.col("purchaser_type_s") == 0))
    if round_.years == "cross":
        df = df.filter(pl.col("activity_year_s") <= pl.col("activity_year_p"))
    if round_.refi_types:
        df = filter_refi_types(df)
    df = numeric_matches(df, round_.tolerances.to_dict())
    if round_.demographics and not round_.early_order:
        df = match_demographics(df)
    if round_.weak is not None and round_.early_order:
        df = weak_numeric_matches(df, round_.weak.to_dict())

    df = perform_fee_matches(df)
    if round_.fees == "generous":
        df = filter_generous_fee_match(df)
    elif round_.fees == "strict":
        df = filter_strict_fee_match(df)

    if round_.weak is not None and not round_.early_order:
        df = weak_numeric_matches(df, round_.weak.to_dict())
    if round_.quality_filters:
        df = apply_quality_match_filters(df)

    df = keep_uniques(df, one_to_one=round_.one_to_one)

    if round_.demographics and round_.early_order:
        df = match_demographics(df)
    if round_.min_fee_matches is not None:
        df = df.filter(pl.col("NumberFeeMatches") >= round_.min_fee_matches)
    if round_.post is not None:
        post_filter = numeric_matches if round_.early_order else numeric_matches_post_unique
        df = post_filter(df, round_.post.to_dict())
    if round_.drop_cleanup:
        df = apply_drop_observation_cleanup(df)
    if round_.loan_amount_gte:
        df = df.filter(pl.col("loan_amount_s") >= pl.col("loan_amount_p"))
    if round_.purchaser_types_p:
        df = df.filter(pl.col("purchaser_type_p").is_in(round_.purchaser_types_p))

    save_crosswalk(df, save_folder, number)


def _run_round(
    number: int,
    config: Round,
    data_folder: Path,
    save_folder: Path,
    min_year: int = Config.MIN_YEAR_POST2018,
    max_year: int = Config.MAX_YEAR_POST2018,
    overwrite: bool = False,
) -> bool:
    """Run one round: year by year for a same-year round, pooled otherwise.

    Args:
        number: The round's position in `ROUNDS_POST2018`, its ``match_round``.
        config: The round to run.
        data_folder: HMDA post-2018 silver root.
        save_folder: Crosswalk directory; earlier rounds' pairs are read from it.
        min_year: First activity year.
        max_year: Last activity year.
        overwrite: Rebuild the round even if its partition already exists. Only the
            seller years in ``min_year``-``max_year`` are cleared first.

    Returns:
        Whether the round ran (False when it was already built).
    """
    data_folder, save_folder = Path(data_folder), Path(save_folder)
    if not should_process_file(save_folder / f"match_round={number}", overwrite):
        logger.info("Round %s already built; pass overwrite=True to rebuild", number)
        return False
    clear_partitions(
        save_folder,
        (f"match_round={number}/activity_year_s={y}" for y in range(min_year, max_year + 1)),
    )
    logger.info("Starting Round %s: %s", number, config.description)
    if config.years == "same":
        for year in range(min_year, max_year + 1):
            logger.info("  Processing year %s", year)
            _run_pass(number, config, data_folder, save_folder, year, year)
    else:
        _run_pass(number, config, data_folder, save_folder, min_year, max_year)
    logger.info("Completed Round %s", number)
    return True


def run_round(
    round_num: int,
    data_folder: str | Path,
    save_folder: str | Path,
    min_year: int = Config.MIN_YEAR_POST2018,
    max_year: int = Config.MAX_YEAR_POST2018,
    overwrite: bool = False,
) -> bool:
    """Run a single round by its position in `ROUNDS_POST2018`.

    Args:
        round_num: The round's 1-based position.
        data_folder: HMDA post-2018 silver root.
        save_folder: Crosswalk directory; earlier rounds' pairs are read from it.
        min_year: First activity year.
        max_year: Last activity year.
        overwrite: Rebuild the round even if it already exists.

    Returns:
        Whether the round ran (False when it was already built).

    Raises:
        ValueError: If ``round_num`` is not a position in `ROUNDS_POST2018`.
    """
    if not 1 <= round_num <= len(ROUNDS_POST2018):
        raise ValueError(f"Invalid round {round_num}. Valid rounds: 1-{len(ROUNDS_POST2018)}")
    return _run_round(
        round_num,
        ROUNDS_POST2018[round_num - 1],
        Path(data_folder),
        Path(save_folder),
        min_year,
        max_year,
        overwrite,
    )


def run_matching(
    data_folder: Path,
    save_folder: Path,
    min_year: int = Config.MIN_YEAR_POST2018,
    max_year: int = Config.MAX_YEAR_POST2018,
    rounds: list[int] | None = None,
    overwrite: bool = False,
) -> None:
    """Run every round (or the listed ones) in order.

    Args:
        data_folder: HMDA post-2018 silver root.
        save_folder: Crosswalk directory.
        min_year: First activity year.
        max_year: Last activity year.
        rounds: Round positions to run; None runs all.
        overwrite: Rebuild rounds that already exist.
    """
    for number, round_ in enumerate(ROUNDS_POST2018, start=1):
        if not rounds or number in rounds:
            logger.info("Running Round %s: %s", number, round_.description)
            _run_round(number, round_, data_folder, save_folder, min_year, max_year, overwrite)
