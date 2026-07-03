"""Generic matching engine for seller-purchaser matching.

This module provides a configuration-driven matching engine that executes
matching rounds based on RoundConfig specifications. The engine handles
the complexity of different round workflows through configuration rather
than duplicated code.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.reference.ct_planning_region import to_planning_region
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config import (
    MAX_YEAR_POST2018,
    MIN_YEAR_POST2018,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config.round_config import (
    RoundConfig,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.utils import (
    add_purchase_indicator,
    apply_drop_observation_cleanup,
    apply_quality_match_filters,
    drop_zeros,
    filter_generous_fee_match,
    filter_refi_types,
    filter_strict_fee_match,
    get_affiliates,
    get_lei_relationships,
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


def run_matching_round(
    config: RoundConfig,
    data_folder: Path,
    save_folder: Path,
    min_year: int = MIN_YEAR_POST2018,
    max_year: int = MAX_YEAR_POST2018,
) -> None:
    """Execute a single matching round based on configuration.

    This function dispatches to the appropriate workflow based on the round
    configuration. Different rounds have different orderings of operations,
    which are determined by examining the configuration parameters.

    Args:
        config: Complete configuration for this matching round.
        data_folder: Directory containing HMDA parquet files to load.
        save_folder: Directory to save match crosswalk outputs.
        min_year: Minimum activity year to process. Defaults to ``MIN_YEAR_POST2018``.
        max_year: Maximum activity year to process. Defaults to ``MAX_YEAR_POST2018``.
    """
    data_folder = Path(data_folder)
    save_folder = Path(save_folder)

    logger.info(f"Starting Round {config.round_number}: {config.description}")

    # Handle same-year vs cross-year differently
    if config.year_constraint == "same":
        # Process year-by-year for same-year matching
        for year in range(min_year, max_year + 1):
            logger.info(f"  Processing year {year}")
            _run_single_match(config, data_folder, save_folder, year, year)
    else:
        # Process all years together for cross-year matching
        _run_single_match(config, data_folder, save_folder, min_year, max_year)

    logger.info(f"Completed Round {config.round_number}")


def _run_single_match(
    config: RoundConfig,
    data_folder: Path,
    save_folder: Path,
    min_year: int,
    max_year: int,
) -> None:
    """Core matching logic driven by configuration.

    This function implements the matching workflow, with the order of operations
    determined by the round configuration flags (demographics_after_uniques,
    weak_tolerances_before_fees, use_numeric_matches_post_unique).
    """
    # =========================================================================
    # 1. Load and prepare special constraint data (for rounds 8-10)
    # =========================================================================
    affiliated_leis = None
    purchaser_types = None
    lei_relationships = None

    if config.special_constraints.use_affiliates:
        affiliated_leis = get_affiliates(
            match_folder=save_folder,
            data_folder=data_folder,
            match_round=config.special_constraints.affiliates_source_round,
            strict=config.special_constraints.affiliates_strict,
        )

    if config.special_constraints.use_purchaser_types:
        purchaser_types = get_purchaser_type_counts(
            match_folder=save_folder,
            data_folder=data_folder,
            match_round=config.special_constraints.purchaser_types_source_round,
        )

    if config.special_constraints.use_lei_relationships:
        lei_relationships = get_lei_relationships(
            match_folder=save_folder,
            data_folder=data_folder,
            match_round=config.special_constraints.lei_relationships_source_round,
        )

    # =========================================================================
    # 2. Load unmatched data
    # =========================================================================
    df = load_unmatched_data(
        data_folder=data_folder,
        crosswalk_folder=save_folder,
        filter_gse_sales=config.data_filter.filter_gse_sales,
        match_round=config.round_number,
        min_year=min_year,
        max_year=max_year,
    )

    # =========================================================================
    # 3. Apply data filters
    # =========================================================================
    df = _apply_data_filters(df, config)

    # =========================================================================
    # 4. Prepare data
    # =========================================================================
    df = replace_missing_values(df)

    # Normalize CT census tracts to the planning-region convention. HMDA switched
    # CT tract GEOIDs from county-based (09001-09015) to planning-region-based
    # (09110-09190) as a hard cut at 2023->2024; without this, cross-year CT
    # pairs that straddle the boundary (seller <=2023, purchaser >=2024) have
    # non-equal tract strings and are silently dropped by the exact join. The
    # mapping is a 1:1 bijection and idempotent (PR/non-CT tracts pass through),
    # so applying it to the whole mixed-year frame is safe for every round.
    df = to_planning_region(df)

    if config.data_filter.drop_zeros:
        df = drop_zeros(df)

    if config.use_i_purchase:
        df = add_purchase_indicator(df)

    # =========================================================================
    # 5. Get match columns and drop nulls
    # =========================================================================
    match_columns = config.match_columns.to_list()
    df = df.drop_nulls(subset=match_columns)

    # Filter out invalid census tract values if using census_tract
    if "census_tract" in match_columns:
        df = df.filter(~pl.col("census_tract").is_in(["", "NA"]))

    # =========================================================================
    # 6. Split and merge
    # =========================================================================
    df_seller, df_purchaser = split_originations_purchases(df)
    df = merge_sellers_and_purchasers(df_seller, df_purchaser, match_columns)

    # =========================================================================
    # 7. Apply special constraint joins (before other filters)
    # =========================================================================
    if config.special_constraints.use_purchaser_types and purchaser_types is not None:
        df = _apply_purchaser_type_constraint(
            df, purchaser_types, config.special_constraints.allow_purchaser_type_zero
        )

    if config.special_constraints.use_lei_relationships and lei_relationships is not None:
        df = _apply_lei_relationship_constraint(df, lei_relationships)

    # =========================================================================
    # 8. Year constraint
    # =========================================================================
    if config.year_constraint == "cross":
        df = df.filter(pl.col("activity_year_s") <= pl.col("activity_year_p"))

    # =========================================================================
    # 9. Refi types filter (conditional)
    # =========================================================================
    if config.filter_refi_types:
        df = filter_refi_types(df)

    # =========================================================================
    # 10. First-pass numeric tolerances
    # =========================================================================
    if config.tolerances:
        df = numeric_matches(df, config.tolerances.to_dict())

    # =========================================================================
    # 11. Demographics - BEFORE fees (if not deferred)
    # =========================================================================
    if config.use_demographics and not config.demographics_after_uniques:
        df = match_demographics(df)

    # =========================================================================
    # 12. Weak tolerances - BEFORE fees (if configured)
    # =========================================================================
    if config.weak_tolerances and config.weak_tolerances_before_fees:
        df = weak_numeric_matches(df, config.weak_tolerances.to_dict())

    # =========================================================================
    # 13. Fee matching
    # =========================================================================
    df = perform_fee_matches(df)

    # =========================================================================
    # 14. Fee filter
    # =========================================================================
    if config.fee_strategy == "generous":
        df = filter_generous_fee_match(df)
    elif config.fee_strategy == "strict":
        df = filter_strict_fee_match(df)

    # =========================================================================
    # 15. Weak tolerances - AFTER fees (if not done before)
    # =========================================================================
    if config.weak_tolerances and not config.weak_tolerances_before_fees:
        df = weak_numeric_matches(df, config.weak_tolerances.to_dict())

    # =========================================================================
    # 16. Quality filters (conditional)
    # =========================================================================
    if config.use_quality_filters:
        df = apply_quality_match_filters(df)

    # =========================================================================
    # 17. Affiliate join (conditional)
    # =========================================================================
    if affiliated_leis is not None:
        df = df.join(
            affiliated_leis.lazy(),
            on=["lei_s", "lei_p"],
            how="inner",
        )

    # =========================================================================
    # 18. Keep uniques
    # =========================================================================
    df = keep_uniques(df, one_to_one=config.one_to_one)

    # =========================================================================
    # 19. Demographics - AFTER uniques (if deferred)
    # =========================================================================
    if config.use_demographics and config.demographics_after_uniques:
        df = match_demographics(df)

    # =========================================================================
    # 20. Post-unique fee filter (conditional)
    # =========================================================================
    if config.number_fee_matches_min is not None:
        df = df.filter(pl.col("NumberFeeMatches") >= config.number_fee_matches_min)

    # =========================================================================
    # 21. Post-unique tolerances
    # =========================================================================
    if config.post_tolerances:
        if config.use_numeric_matches_post_unique:
            df = numeric_matches_post_unique(df, config.post_tolerances.to_dict())
        else:
            df = numeric_matches(df, config.post_tolerances.to_dict())

    # =========================================================================
    # 22. Drop cleanup (conditional)
    # =========================================================================
    if config.use_drop_cleanup:
        df = apply_drop_observation_cleanup(df)

    # =========================================================================
    # 23. Additional filters (conditional)
    # =========================================================================
    if config.require_loan_amount_gte:
        df = df.filter(pl.col("loan_amount_s") >= pl.col("loan_amount_p"))

    if config.purchaser_type_p_allowed:
        df = df.filter(pl.col("purchaser_type_p").is_in(config.purchaser_type_p_allowed))

    # =========================================================================
    # Final: Save crosswalk
    # =========================================================================
    save_crosswalk(df, save_folder, config.round_number)


def _apply_data_filters(df: pl.LazyFrame, config: RoundConfig) -> pl.LazyFrame:
    """Apply pre-merge data filters based on configuration."""
    data_filter = config.data_filter

    # Action taken filter (allows action_taken=6 OR specified filters)
    if data_filter.action_taken_filter:
        conditions = [pl.col("action_taken") == 6]
        if data_filter.purchaser_type_include:
            conditions.append(pl.col("purchaser_type").is_in(data_filter.purchaser_type_include))
        df = df.filter(pl.any_horizontal(conditions))
    elif data_filter.purchaser_type_include:
        # Include filter without action_taken
        df = df.filter(
            (pl.col("action_taken") == 6)
            | pl.col("purchaser_type").is_in(data_filter.purchaser_type_include)
        )
    elif data_filter.purchaser_type_exclude:
        # Exclude filter (allows action_taken=6 OR not in excluded types)
        df = df.filter(
            (pl.col("action_taken") == 6)
            | (~pl.col("purchaser_type").is_in(data_filter.purchaser_type_exclude))
        )

    return df


def _apply_purchaser_type_constraint(
    df: pl.LazyFrame,
    purchaser_types: pl.LazyFrame,
    allow_purchaser_type_zero: bool,
) -> pl.LazyFrame:
    """Apply purchaser type constraint from prior round matches."""
    df = df.join(
        purchaser_types,
        on=["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"],
        how="left",
    ).with_columns(
        pl.col("CountLEIMatches").is_not_null().cast(pl.Int8).alias("i_PurchaserTypeMatch")
    )

    if allow_purchaser_type_zero:
        df = df.filter((pl.col("i_PurchaserTypeMatch") == 1) | (pl.col("purchaser_type_s") == 0))
    else:
        df = df.filter(pl.col("i_PurchaserTypeMatch") == 1)

    return df


def _apply_lei_relationship_constraint(
    df: pl.LazyFrame,
    lei_relationships: pl.LazyFrame,
) -> pl.LazyFrame:
    """Apply LEI relationship constraint from prior round matches."""
    df = (
        df.join(
            lei_relationships,
            on=["lei_s", "lei_p", "activity_year_s"],
            how="left",
        )
        .with_columns(pl.col("CountLEIMatches").is_not_null().cast(pl.Int8).alias("i_LEIMatch"))
        .filter(pl.col("i_LEIMatch") == 1)
    )

    return df


def run_all_rounds(
    data_folder: Path,
    save_folder: Path,
    min_year: int = MIN_YEAR_POST2018,
    max_year: int = MAX_YEAR_POST2018,
    rounds: list[int] | None = None,
) -> None:
    """Run all (or selected) matching rounds.

    Args:
        data_folder: Directory containing HMDA parquet files.
        save_folder: Directory to save match crosswalk outputs.
        min_year: Minimum activity year to process. Defaults to ``MIN_YEAR_POST2018``.
        max_year: Maximum activity year to process. Defaults to ``MAX_YEAR_POST2018``.
        rounds: List of round numbers to run. If None, runs all configured rounds.
    """
    from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config.post2018_rounds import (
        POST2018_ROUNDS,
    )

    configs = POST2018_ROUNDS
    if rounds:
        configs = [c for c in configs if c.round_number in rounds]

    for config in configs:
        logger.info(f"Running Round {config.round_number}: {config.description}")
        run_matching_round(config, data_folder, save_folder, min_year, max_year)


__all__ = [
    "run_matching_round",
    "run_all_rounds",
]
