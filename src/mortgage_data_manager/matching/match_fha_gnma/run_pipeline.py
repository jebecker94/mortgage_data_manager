#!/usr/bin/env python3
"""FHA-GNMA Matching Pipeline.

Main entry point for the FHA-GNMA matching workflow.

Matches FHA endorsement records with GNMA loan-level disclosure data (dailyllmni)
using probabilistic matching based on loan characteristics (since GNMA lacks
FHA Case Numbers).

Usage:
    mortgage-data match fha-gnma run
    mortgage-data match fha-gnma run --all-states
    mortgage-data match fha-gnma run --state CA
    mortgage-data match fha-gnma run --min-year 2020 --max-year 2024
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

from .config import (
    FHA_SILVER_DIR,
    GNMA_SILVER_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    OUTPUT_DIR,
    PILOT_STATE,
    ensure_directories,
)
from .matching import (
    create_crosswalk,
    run_multi_round_matching,
    save_match_details,
)
from .prepare_fha import load_fha_silver_data, prepare_fha_for_matching
from .prepare_gnma import load_gnma_silver_data, prepare_gnma_for_matching

logger = get_logger(__name__)


def run_fha_gnma_matching(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    state_filter: str | None = PILOT_STATE,
    skip_data_prep: bool = False,
) -> Path:
    """Run the complete FHA-GNMA matching pipeline.

    Args:
        min_year: Minimum year to match (default: 2015)
        max_year: Maximum year to match (default: 2024)
        state_filter: State to filter (e.g., "DC"). None = all states.
        skip_data_prep: Skip data preparation if intermediate files exist

    Returns:
        Path to final crosswalk file
    """
    ensure_directories()

    logger.info("FHA-GNMA Matching Pipeline")
    logger.info(f"Years: {min_year}-{max_year}")
    logger.info(f"State filter: {state_filter or 'All states'}")
    logger.info(f"FHA source: {FHA_SILVER_DIR}")
    logger.info(f"GNMA source: {GNMA_SILVER_DIR}")
    logger.info(f"Output: {OUTPUT_DIR}")

    # Check for existing prepared data files
    state_suffix = f"_{state_filter}" if state_filter else ""
    fha_prepared_file = (
        INTERMEDIATE_DIR / f"fha_prepared_{min_year}_{max_year}{state_suffix}.parquet"
    )
    gnma_prepared_file = (
        INTERMEDIATE_DIR / f"gnma_prepared_{min_year}_{max_year}{state_suffix}.parquet"
    )

    # Step 1: Prepare FHA data
    if should_process_file(fha_prepared_file, overwrite=not skip_data_prep):
        logger.info("Preparing FHA data...")
        fha_raw = load_fha_silver_data(
            fha_dir=FHA_SILVER_DIR,
            min_year=min_year,
            max_year=max_year,
            state_filter=state_filter,
        )
        fha_df = prepare_fha_for_matching(fha_raw)
        fha_df.write_parquet(fha_prepared_file)
        logger.info(f"  Cached to: {fha_prepared_file}")
        del fha_raw
    else:
        logger.info(f"Loading cached FHA data: {fha_prepared_file}")
        fha_df = pl.read_parquet(fha_prepared_file)

    # Step 2: Prepare GNMA data
    if should_process_file(gnma_prepared_file, overwrite=not skip_data_prep):
        logger.info("Preparing GNMA data...")
        gnma_raw = load_gnma_silver_data(
            gnma_dir=GNMA_SILVER_DIR,
            min_year=min_year,
            max_year=max_year,
            state_filter=state_filter,
        )
        gnma_df = prepare_gnma_for_matching(gnma_raw)
        gnma_df.write_parquet(gnma_prepared_file)
        logger.info(f"  Cached to: {gnma_prepared_file}")
        del gnma_raw
    else:
        logger.info(f"Loading cached GNMA data: {gnma_prepared_file}")
        gnma_df = pl.read_parquet(gnma_prepared_file)

    # Step 3: Run multi-round matching
    matches = run_multi_round_matching(fha_df, gnma_df)

    if len(matches) == 0:
        logger.info("No matches found!")
        return OUTPUT_DIR / f"fha_gnma_crosswalk_{min_year}_{max_year}{state_suffix}.parquet"

    # Step 4: Save results
    crosswalk_file = create_crosswalk(
        matches,
        output_dir=OUTPUT_DIR,
        min_year=min_year,
        max_year=max_year,
        state_filter=state_filter,
    )

    save_match_details(
        matches,
        output_dir=INTERMEDIATE_DIR,
        min_year=min_year,
        max_year=max_year,
        state_filter=state_filter,
    )

    # Step 5: Validation summary
    logger.info("VALIDATION")
    validate_matches(matches, fha_df, gnma_df)

    return crosswalk_file


def validate_matches(
    matches: pl.DataFrame,
    fha_df: pl.DataFrame,
    gnma_df: pl.DataFrame,
) -> None:
    """Log validation statistics for matches.

    Args:
        matches: All matched loan pairs
        fha_df: Original FHA data
        gnma_df: Original GNMA data
    """
    total_fha = len(fha_df)
    total_gnma = len(gnma_df)
    total_matches = len(matches)

    logger.info(
        f"FHA match rate: {total_matches:,} / {total_fha:,} = {total_matches / total_fha * 100:.1f}%"
    )
    logger.info(
        f"GNMA match rate: {total_matches:,} / {total_gnma:,} = {total_matches / total_gnma * 100:.1f}%"
    )

    # Check for duplicates
    fha_dups = (
        matches.group_by("FHA_Index").agg(pl.len().alias("count")).filter(pl.col("count") > 1)
    )
    gnma_dups = (
        matches.group_by("gnma_loan_id").agg(pl.len().alias("count")).filter(pl.col("count") > 1)
    )
    logger.info(f"Duplicate FHA_Index: {len(fha_dups)}")
    logger.info(f"Duplicate gnma_loan_id: {len(gnma_dups)}")

    # Round distribution
    round1 = matches.filter(pl.col("match_round") == 1).height
    round2 = matches.filter(pl.col("match_round") == 2).height
    logger.info(
        f"Round distribution: {round1:,} R1 ({round1 / total_matches * 100:.1f}%), {round2:,} R2 ({round2 / total_matches * 100:.1f}%)"
    )

    # Match quality stats
    if "match_score" in matches.columns:
        score_stats = matches.select(
            [
                pl.col("match_score").mean().alias("mean"),
                pl.col("match_score").median().alias("median"),
                pl.col("match_score").quantile(0.9).alias("p90"),
                pl.col("match_score").max().alias("max"),
            ]
        ).row(0)
        logger.info(
            f"Match score: mean={score_stats[0]:.3f}, median={score_stats[1]:.3f}, p90={score_stats[2]:.3f}, max={score_stats[3]:.3f}"
        )

    # Year distribution
    if "origination_year" in matches.columns:
        logger.info("Matches by year:")
        year_counts = (
            matches.group_by("origination_year")
            .agg(pl.len().alias("count"))
            .sort("origination_year")
        )
        for row in year_counts.iter_rows():
            logger.info(f"  {row[0]}: {row[1]:,}")
