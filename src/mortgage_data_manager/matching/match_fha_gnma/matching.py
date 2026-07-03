"""FHA-GNMA Matching Logic.

Probabilistic matching of FHA endorsement records with GNMA loan-level
disclosure data using loan characteristics since GNMA lacks FHA Case Numbers.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import configure_logging, get_logger

from .config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
    ROUND1_TOLERANCES,
    ROUND2_TOLERANCES,
    MatchingTolerances,
    ensure_directories,
)

logger = get_logger(__name__)


def match_fha_gnma_round(
    fha_df: pl.DataFrame | pl.LazyFrame,
    gnma_df: pl.DataFrame | pl.LazyFrame,
    tolerances: MatchingTolerances,
) -> pl.DataFrame:
    """Match FHA and GNMA loans using blocking-based matching.

    Uses exact join keys for blocking, then tolerance filters:
    1. Join on exact keys: state, year, purpose, loan_amount_thousands, rate_bucket, month
    2. Apply post-match tolerances for quality filtering
    3. Resolve duplicates via mutual best-match scoring

    Args:
        fha_df: Prepared FHA data (must have loan_amount_thousands, rate_bucket columns)
        gnma_df: Prepared GNMA data (must have loan_amount_thousands, rate_bucket columns)
        tolerances: Tolerance configuration for this round

    Returns:
        Matched loan pairs with match quality metrics
    """
    # Convert to LazyFrame if needed
    fha_lf = fha_df.lazy() if isinstance(fha_df, pl.DataFrame) else fha_df
    gnma_lf = gnma_df.lazy() if isinstance(gnma_df, pl.DataFrame) else gnma_df

    if logger.isEnabledFor(logging.DEBUG):
        fha_count = fha_lf.select(pl.len()).collect().item()
        gnma_count = gnma_lf.select(pl.len()).collect().item()
        logger.debug(f"Starting match: {fha_count:,} FHA loans, {gnma_count:,} GNMA loans")
    logger.debug("  Join keys: state, year, purpose, amount (thousands), rate bucket (0.125%)")
    logger.debug(
        f"  Month tolerance: [{tolerances.month_tolerance_min}, {tolerances.month_tolerance_max}]"
    )

    # Use month-based blocking: for each valid month offset, join with exact keys
    all_candidates = []

    for month_offset in range(tolerances.month_tolerance_min, tolerances.month_tolerance_max + 1):
        # Add join key: GNMA month that would match this FHA month with given offset
        fha_with_key = fha_lf.with_columns(
            (pl.col("origination_month") - month_offset).alias("_join_month")
        )
        gnma_with_key = gnma_lf.with_columns(pl.col("origination_month").alias("_join_month"))

        # Join on ALL exact keys: state, year, purpose, amount, rate, month
        merged = fha_with_key.join(
            gnma_with_key,
            left_on=[
                "state",
                "origination_year",
                "is_purchase",
                "loan_amount_thousands",
                "rate_bucket",
                "_join_month",
            ],
            right_on=[
                "state",
                "origination_year",
                "is_purchase",
                "loan_amount_thousands",
                "rate_bucket",
                "_join_month",
            ],
            how="inner",
            suffix="_gnma",
        )

        # Drop the join key
        merged = merged.drop("_join_month")

        all_candidates.append(merged)

    # Combine all month-offset candidates and collect
    if len(all_candidates) == 1:
        merged = all_candidates[0].collect()
    else:
        merged = pl.concat(all_candidates, how="diagonal_relaxed").collect()

    # Deduplicate: same pair might appear from different month offsets
    merged = merged.unique(subset=["FHA_Index", "gnma_loan_id"])

    logger.debug(f"  After exact-key join: {len(merged):,} candidate pairs")

    if len(merged) == 0:
        # Return empty with expected schema
        return merged

    # Mutual best-match approach using match quality scoring
    # Compute match quality score (lower = better)
    # Amount diff: FHA - GNMA should be in [0, 999] due to truncation
    # Rate diff: absolute difference in percentage points
    merged = merged.with_columns(
        [
            (pl.col("loan_amount") - pl.col("loan_amount_gnma")).alias("_amount_diff"),
            (pl.col("interest_rate") - pl.col("interest_rate_gnma")).abs().alias("_rate_diff"),
        ]
    )

    # Score = normalized deviations
    merged = merged.with_columns(
        [
            (
                (pl.col("_amount_diff") / 999.0)  # Normalize amount diff by max possible (999)
                + (pl.col("_rate_diff") / 0.125)  # Normalize rate diff by bucket size
            ).alias("match_score")
        ]
    )

    # Drop temp columns
    merged = merged.drop(["_amount_diff", "_rate_diff"])

    # Rank each pair within FHA and GNMA groups
    merged = merged.with_columns(
        [
            pl.col("match_score").rank(method="dense").over("FHA_Index").alias("rank_in_fha"),
            pl.col("match_score").rank(method="dense").over("gnma_loan_id").alias("rank_in_gnma"),
        ]
    )

    # Keep only mutual best matches (best for both sides)
    unique_matches = merged.filter((pl.col("rank_in_fha") == 1) & (pl.col("rank_in_gnma") == 1))

    logger.debug(f"  Mutual best matches: {len(unique_matches):,}")

    # Handle ties: when multiple pairs have the same best score, keep only
    # truly unique 1:1 matches (where both IDs appear exactly once)
    if len(unique_matches) > 0:
        unique_matches = unique_matches.with_columns(
            [
                pl.len().over("FHA_Index").alias("_fha_count"),
                pl.len().over("gnma_loan_id").alias("_gnma_count"),
            ]
        )
        pre_dedup = len(unique_matches)
        unique_matches = unique_matches.filter(
            (pl.col("_fha_count") == 1) & (pl.col("_gnma_count") == 1)
        )
        unique_matches = unique_matches.drop(["_fha_count", "_gnma_count"])

        if pre_dedup != len(unique_matches):
            logger.debug(
                f"  After tie-breaking: {len(unique_matches):,} (dropped {pre_dedup - len(unique_matches):,} ties)"
            )

    # Apply post-match filtering if enabled
    if tolerances.apply_post_match_filter and len(unique_matches) > 0:
        pre_filter_count = len(unique_matches)

        # Compute differences for filtering
        unique_matches = unique_matches.with_columns(
            [
                (pl.col("loan_amount") - pl.col("loan_amount_gnma")).abs().alias("_amount_diff"),
                (pl.col("interest_rate") - pl.col("interest_rate_gnma")).abs().alias("_rate_diff"),
            ]
        )

        # Apply stricter post-match tolerances
        unique_matches = unique_matches.filter(
            pl.col("_amount_diff") <= tolerances.post_amount_tolerance
        )
        unique_matches = unique_matches.filter(
            pl.col("_rate_diff") < tolerances.post_rate_tolerance
        )

        # Drop temporary columns
        unique_matches = unique_matches.drop(["_amount_diff", "_rate_diff"])

        dropped = pre_filter_count - len(unique_matches)
        logger.debug(f"  After post-match filter: {len(unique_matches):,} (dropped {dropped:,})")

    return unique_matches


def run_multi_round_matching_by_year(
    fha_df: pl.DataFrame | pl.LazyFrame,
    gnma_df: pl.DataFrame | pl.LazyFrame,
    tolerances: MatchingTolerances,
    match_round: int,
) -> pl.DataFrame:
    """Run matching year-by-year to reduce memory usage.

    Processing year-by-year keeps join sizes manageable for larger datasets.
    Passes LazyFrames to match_fha_gnma_round for lazy join/filter.

    Args:
        fha_df: Prepared FHA data
        gnma_df: Prepared GNMA data
        tolerances: Tolerance configuration
        match_round: Round number (1 or 2)

    Returns:
        Combined matches from all years
    """
    # Convert to LazyFrame if needed
    fha_lf = fha_df.lazy() if isinstance(fha_df, pl.DataFrame) else fha_df
    gnma_lf = gnma_df.lazy() if isinstance(gnma_df, pl.DataFrame) else gnma_df

    # Get unique years (need to collect just this column)
    years = sorted(fha_lf.select("origination_year").unique().collect().to_series().to_list())
    all_matches = []

    for year in years:
        # Filter lazily - don't collect until inside match_fha_gnma_round
        fha_year = fha_lf.filter(pl.col("origination_year") == year)
        gnma_year = gnma_lf.filter(pl.col("origination_year") == year)

        # Quick count check
        fha_count = fha_year.select(pl.len()).collect().item()
        gnma_count = gnma_year.select(pl.len()).collect().item()
        if fha_count == 0 or gnma_count == 0:
            continue
        # Log year progress (match count will be appended on same line in next logger call)
        year_msg = f"  Year {year}: {fha_count:,} FHA, {gnma_count:,} GNMA"

        # Pass LazyFrames - join and filtering happen lazily inside
        matches = match_fha_gnma_round(fha_year, gnma_year, tolerances)

        if len(matches) > 0:
            all_matches.append(matches)
            logger.info(f"{year_msg} -> {len(matches):,} matches")
        else:
            logger.info(f"{year_msg} -> 0 matches")

    if not all_matches:
        # Return empty DataFrame with expected schema
        return (
            fha_lf.head(0)
            .join(
                gnma_lf.head(0),
                left_on=["state", "origination_year", "is_purchase"],
                right_on=["state", "origination_year", "is_purchase"],
                suffix="_gnma",
            )
            .with_columns(
                [
                    pl.lit(0.0).alias("match_score"),
                    pl.lit(0).alias("rank_in_fha"),
                    pl.lit(0).alias("rank_in_gnma"),
                ]
            )
            .collect()
        )

    return pl.concat(all_matches, how="diagonal_relaxed")


def run_multi_round_matching(
    fha_df: pl.DataFrame | pl.LazyFrame,
    gnma_df: pl.DataFrame | pl.LazyFrame,
) -> pl.DataFrame:
    """Run multi-round FHA-GNMA matching.

    Round 1: Strict tolerances
    Round 2: Relaxed tolerances on remaining unmatched records

    Processes year-by-year to keep memory usage bounded for large datasets.
    Join and filtering operations are lazy until ranking/dedup.

    Args:
        fha_df: Prepared FHA data
        gnma_df: Prepared GNMA data

    Returns:
        Combined matches from all rounds with match_round indicator
    """
    ensure_directories()

    # Convert to LazyFrame if needed
    fha_lf = fha_df.lazy() if isinstance(fha_df, pl.DataFrame) else fha_df
    gnma_lf = gnma_df.lazy() if isinstance(gnma_df, pl.DataFrame) else gnma_df

    logger.info("Multi-Round FHA-GNMA Matching")

    # Round 1: Strict tolerances (year-by-year)
    logger.info("ROUND 1 (Strict) - processing year-by-year")

    round1_matches = run_multi_round_matching_by_year(
        fha_lf, gnma_lf, ROUND1_TOLERANCES, match_round=1
    )
    round1_matches = round1_matches.with_columns(pl.lit(1).alias("match_round"))

    logger.info(f"  Round 1 total: {len(round1_matches):,} matches")

    # Get unmatched FHA and GNMA records for Round 2 (lazy anti-join)
    if len(round1_matches) > 0:
        matched_fha = round1_matches.select("FHA_Index").unique().lazy()
        matched_gnma = round1_matches.select("gnma_loan_id").unique().lazy()
        fha_unmatched = fha_lf.join(matched_fha, on="FHA_Index", how="anti")
        gnma_unmatched = gnma_lf.join(matched_gnma, on="gnma_loan_id", how="anti")
    else:
        fha_unmatched = fha_lf
        gnma_unmatched = gnma_lf

    fha_unmatched_count = fha_unmatched.select(pl.len()).collect().item()
    gnma_unmatched_count = gnma_unmatched.select(pl.len()).collect().item()
    logger.info(
        f"Unmatched after Round 1: {fha_unmatched_count:,} FHA, {gnma_unmatched_count:,} GNMA"
    )

    # Round 2: Relaxed tolerances (year-by-year)
    logger.info("ROUND 2 (Relaxed) - processing year-by-year")

    round2_matches = run_multi_round_matching_by_year(
        fha_unmatched, gnma_unmatched, ROUND2_TOLERANCES, match_round=2
    )
    round2_matches = round2_matches.with_columns(pl.lit(2).alias("match_round"))

    logger.info(f"  Round 2 total: {len(round2_matches):,} matches")

    # Combine rounds
    if len(round2_matches) > 0 and len(round1_matches) > 0:
        all_matches = pl.concat([round1_matches, round2_matches], how="diagonal_relaxed")
    elif len(round1_matches) > 0:
        all_matches = round1_matches
    else:
        all_matches = round2_matches

    logger.info("MATCHING SUMMARY")
    logger.info(f"Total matches: {len(all_matches):,}")
    logger.info(f"  Round 1: {len(round1_matches):,}")
    logger.info(f"  Round 2: {len(round2_matches):,}")
    fha_total = fha_lf.select(pl.len()).collect().item()
    gnma_total = gnma_lf.select(pl.len()).collect().item()
    if fha_total > 0:
        logger.info(f"FHA match rate: {len(all_matches) / fha_total * 100:.1f}%")
    if gnma_total > 0:
        logger.info(f"GNMA match rate: {len(all_matches) / gnma_total * 100:.1f}%")

    return all_matches


def create_crosswalk(
    matches: pl.DataFrame,
    output_dir: Path = CROSSWALK_OUTPUT_DIR,
    min_year: int = 2015,
    max_year: int = 2024,
    state_filter: str | None = None,
) -> Path:
    """Create and save the FHA-GNMA crosswalk.

    Args:
        matches: All matched loan pairs
        output_dir: Output directory
        min_year: Minimum year in data
        max_year: Maximum year in data
        state_filter: State filter applied (for filename)

    Returns:
        Path to saved crosswalk file
    """
    # Select crosswalk columns
    crosswalk = matches.select(
        [
            "FHA_Index",
            "gnma_loan_id",
            "match_round",
            "match_score",
        ]
    )

    # Validate 1:1 matching
    fha_duplicates = (
        crosswalk.group_by("FHA_Index").agg(pl.len().alias("count")).filter(pl.col("count") > 1)
    )
    gnma_duplicates = (
        crosswalk.group_by("gnma_loan_id").agg(pl.len().alias("count")).filter(pl.col("count") > 1)
    )

    if len(fha_duplicates) > 0:
        logger.warning(f"{len(fha_duplicates)} FHA_Index values have multiple matches")
    if len(gnma_duplicates) > 0:
        logger.warning(f"{len(gnma_duplicates)} gnma_loan_id values have multiple matches")

    # Save crosswalk
    state_suffix = f"_{state_filter}" if state_filter else ""
    output_file = output_dir / f"fha_gnma_crosswalk_{min_year}_{max_year}{state_suffix}.parquet"
    crosswalk.write_parquet(output_file)

    logger.info(f"Crosswalk saved to: {output_file}")
    logger.info(f"  Total matches: {len(crosswalk):,}")
    logger.info(f"  Round 1: {crosswalk.filter(pl.col('match_round') == 1).height:,}")
    logger.info(f"  Round 2: {crosswalk.filter(pl.col('match_round') == 2).height:,}")

    return output_file


def save_match_details(
    matches: pl.DataFrame,
    output_dir: Path = INTERMEDIATE_DIR,
    min_year: int = 2015,
    max_year: int = 2024,
    state_filter: str | None = None,
) -> Path:
    """Save full match details for analysis.

    Args:
        matches: All matched loan pairs with full columns
        output_dir: Output directory
        min_year: Minimum year in data
        max_year: Maximum year in data
        state_filter: State filter applied (for filename)

    Returns:
        Path to saved details file
    """
    state_suffix = f"_{state_filter}" if state_filter else ""
    output_file = output_dir / f"fha_gnma_match_details_{min_year}_{max_year}{state_suffix}.parquet"
    matches.write_parquet(output_file)

    logger.info(f"Match details saved to: {output_file}")

    return output_file


if __name__ == "__main__":
    configure_logging(level="INFO")

    # Test matching with sample data
    from .prepare_fha import run_fha_preparation
    from .prepare_gnma import run_gnma_preparation

    # Prepare data
    fha_file = run_fha_preparation()
    gnma_file = run_gnma_preparation()

    # Load prepared data
    fha_df = pl.read_parquet(fha_file)
    gnma_df = pl.read_parquet(gnma_file)

    # Run matching
    matches = run_multi_round_matching(fha_df, gnma_df)

    # Save results
    create_crosswalk(matches)
    save_match_details(matches)
