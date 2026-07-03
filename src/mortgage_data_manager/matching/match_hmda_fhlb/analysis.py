"""Analysis functions for FHLB matching results.

Uses lazy evaluation with Polars LazyFrames for memory efficiency.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def calculate_match_statistics(match_folder: Path) -> None:
    """Calculate and log match statistics for identifying Likely-PFIs (Post-2018).

    Tabulates:
    - Number of loans originated by each lender (LEI) per year.
    - Number of loans matched to a FHLB loan per lender per year.
    - Fraction of originations matched.

    Args:
        match_folder: Directory containing match results
    """
    logger.info("Calculating match statistics (Post-2018)")

    hmda_path = match_folder / "hmda_fhlb_members_post2018.parquet"
    matches_path = match_folder / "fhlb_hmda_matches_post2018.parquet"

    if not hmda_path.exists() or not matches_path.exists():
        logger.warning("Data files for statistics not found. Skipping analysis")
        return

    # Load data lazily
    df_hmda = pl.scan_parquet(hmda_path).select(["lei", "activity_year"])
    df_matches = pl.scan_parquet(matches_path).select(["lei", "activity_year"])

    # Calculate originations (denominator)
    originations = (
        df_hmda
        .group_by(["lei", "activity_year"])
        .len()
        .rename({"len": "total_originations"})
    )

    # Calculate matches (numerator)
    matches = (
        df_matches
        .group_by(["lei", "activity_year"])
        .len()
        .rename({"len": "matched_loans"})
    )

    # Join and compute statistics
    stats = (
        originations
        .join(matches, on=["lei", "activity_year"], how="left")
        .fill_null(0)
        .with_columns(
            (pl.col("matched_loans") / pl.col("total_originations")).alias("match_fraction")
        )
        .collect()
    )

    # Filter for lenders with at least one match
    active_sellers = (
        stats
        .filter(pl.col("matched_loans") > 0)
        .sort(["activity_year", "matched_loans"], descending=[False, True])
    )

    # Log yearly summary
    yearly_summary = (
        active_sellers
        .group_by("activity_year")
        .agg([
            pl.col("lei").n_unique().alias("num_active_lenders"),
            pl.col("matched_loans").sum().alias("total_matches"),
            pl.col("total_originations").sum().alias("total_originations_by_active"),
        ])
        .sort("activity_year")
    )

    logger.info("Match statistics by year:")
    for row in yearly_summary.iter_rows(named=True):
        logger.info(
            f"  {row['activity_year']}: {row['num_active_lenders']:,} lenders, "
            f"{row['total_matches']:,} matches / {row['total_originations_by_active']:,} originations"
        )

    # Log top lenders for latest year
    years = sorted(active_sellers["activity_year"].unique().to_list())
    if years:
        latest_year = years[-1]
        top_lenders = active_sellers.filter(pl.col("activity_year") == latest_year).head(10)
        logger.info(f"Top 10 lenders by match count ({latest_year}):")
        for row in top_lenders.iter_rows(named=True):
            logger.info(
                f"  {row['lei']}: {row['matched_loans']:,} matches "
                f"({row['match_fraction']:.1%} of {row['total_originations']:,})"
            )

    # Save statistics
    output_file = match_folder / "lender_match_statistics_post2018.csv"
    active_sellers.write_csv(output_file)
    logger.info(f"Saved detailed match statistics to {output_file}")


def calculate_match_statistics_pre2018(match_folder: Path) -> None:
    """Calculate and log match statistics for identifying Likely-PFIs (Pre-2018).

    Uses respondent_id and agency_code as identifier.

    Args:
        match_folder: Directory containing match results
    """
    logger.info("Calculating match statistics (Pre-2018)")

    hmda_path = match_folder / "hmda_fhlb_members_pre2018.parquet"
    matches_path = match_folder / "fhlb_hmda_matches_pre2018.parquet"

    if not hmda_path.exists() or not matches_path.exists():
        logger.warning("Data files for Pre-2018 statistics not found. Skipping analysis")
        return

    id_cols = ["respondent_id", "agency_code"]

    # Load data lazily
    df_hmda = pl.scan_parquet(hmda_path).select(id_cols + ["activity_year"])
    df_matches = pl.scan_parquet(matches_path).select(id_cols + ["activity_year"])

    # Calculate originations
    originations = (
        df_hmda
        .group_by(id_cols + ["activity_year"])
        .len()
        .rename({"len": "total_originations"})
    )

    # Calculate matches
    matches = (
        df_matches
        .group_by(id_cols + ["activity_year"])
        .len()
        .rename({"len": "matched_loans"})
    )

    # Join and compute statistics
    stats = (
        originations
        .join(matches, on=id_cols + ["activity_year"], how="left")
        .fill_null(0)
        .with_columns(
            (pl.col("matched_loans") / pl.col("total_originations")).alias("match_fraction")
        )
        .collect()
    )

    # Filter for lenders with at least one match
    active_sellers = (
        stats
        .filter(pl.col("matched_loans") > 0)
        .sort(["activity_year", "matched_loans"], descending=[False, True])
        .with_columns(
            pl.concat_str([pl.col("agency_code"), pl.col("respondent_id")], separator="-").alias("unique_id")
        )
    )

    # Log yearly summary
    yearly_summary = (
        active_sellers
        .group_by("activity_year")
        .agg([
            pl.col("unique_id").n_unique().alias("num_active_lenders"),
            pl.col("matched_loans").sum().alias("total_matches"),
            pl.col("total_originations").sum().alias("total_originations_by_active"),
        ])
        .sort("activity_year")
    )

    logger.info("Match statistics by year (Pre-2018):")
    for row in yearly_summary.iter_rows(named=True):
        logger.info(
            f"  {row['activity_year']}: {row['num_active_lenders']:,} lenders, "
            f"{row['total_matches']:,} matches / {row['total_originations_by_active']:,} originations"
        )

    # Save statistics
    output_file = match_folder / "lender_match_statistics_pre2018.csv"
    active_sellers.write_csv(output_file)
    logger.info(f"Saved detailed match statistics to {output_file}")
