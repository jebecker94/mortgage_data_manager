#!/usr/bin/env python3
"""GNMA-FHLB Matching Logic using Polars.

Matches GNMA loan-level disclosure data (dailyllmni silver) with
FHLB AMA (Acquired Member Assets) data to identify loans that were
acquired by Federal Home Loan Banks from GNMA issuers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_fhlb.config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
    ensure_directories,
    get_fhlb_ama_silver_dir,
    get_gnma_silver_dir,
)

logger = get_logger(__name__)


@dataclass
class MatchingTolerances:
    """Configuration for GNMA-FHLB matching tolerances.

    Two-stage tolerance system:
    1. Candidate tolerances: Used during initial matching to find potential pairs
    2. Post-match tolerances: Stricter filters applied after mutual best-match selection

    Attributes:
        # Candidate-finding tolerances (looser, to find potential matches)
        rate_tolerance: Interest rate tolerance in percentage points (default: 0.25%)
        balance_tolerance: Principal balance tolerance in dollars (default: $2,500)
        ltv_tolerance: LTV tolerance in percentage points (default: 1.1%)
        dti_tolerance: DTI tolerance in percentage points (default: 1.1%)
        term_tolerance: Loan term tolerance in months (default: 12)

        # Post-match tolerances (stricter, for final quality filter)
        post_rate_tolerance: Post-match interest rate tolerance (default: 0.125%, strict less-than)
        post_balance_tolerance: Post-match balance tolerance (default: $1,000, weak less-than-or-equal)
        post_ltv_tolerance: Post-match LTV tolerance (default: 1.0%, weak less-than-or-equal)
        post_dti_tolerance: Post-match DTI tolerance (default: 1.0%, weak less-than-or-equal)

        # Behavior flags
        apply_post_match_filter: Whether to apply post-match filtering (default: True)
    """

    # Candidate-finding tolerances
    rate_tolerance: float = 0.25
    balance_tolerance: float = 2500.0
    ltv_tolerance: float = 1.1
    dti_tolerance: float = 1.1
    term_tolerance: int = 12

    # Post-match tolerances (stricter)
    post_rate_tolerance: float = 0.125
    post_balance_tolerance: float = 1000.0
    post_ltv_tolerance: float = 1.0
    post_dti_tolerance: float = 1.0

    # Behavior
    apply_post_match_filter: bool = True

    def __post_init__(self):
        """Validate tolerances."""
        if self.post_rate_tolerance > self.rate_tolerance:
            raise ValueError("post_rate_tolerance must be <= rate_tolerance")
        if self.post_balance_tolerance > self.balance_tolerance:
            raise ValueError("post_balance_tolerance must be <= balance_tolerance")
        if self.post_ltv_tolerance > self.ltv_tolerance:
            raise ValueError("post_ltv_tolerance must be <= ltv_tolerance")
        if self.post_dti_tolerance > self.dti_tolerance:
            raise ValueError("post_dti_tolerance must be <= dti_tolerance")


# Default tolerances
DEFAULT_TOLERANCES = MatchingTolerances()


# State FIPS code mapping
STATE_FIPS = {
    "AL": 1,
    "AK": 2,
    "AZ": 4,
    "AR": 5,
    "CA": 6,
    "CO": 8,
    "CT": 9,
    "DE": 10,
    "DC": 11,
    "FL": 12,
    "GA": 13,
    "HI": 15,
    "ID": 16,
    "IL": 17,
    "IN": 18,
    "IA": 19,
    "KS": 20,
    "KY": 21,
    "LA": 22,
    "ME": 23,
    "MD": 24,
    "MA": 25,
    "MI": 26,
    "MN": 27,
    "MS": 28,
    "MO": 29,
    "MT": 30,
    "NE": 31,
    "NV": 32,
    "NH": 33,
    "NJ": 34,
    "NM": 35,
    "NY": 36,
    "NC": 37,
    "ND": 38,
    "OH": 39,
    "OK": 40,
    "OR": 41,
    "PA": 42,
    "RI": 44,
    "SC": 45,
    "SD": 46,
    "TN": 47,
    "TX": 48,
    "UT": 49,
    "VT": 50,
    "VA": 51,
    "WA": 53,
    "WV": 54,
    "WI": 55,
    "WY": 56,
    "PR": 72,
    "VI": 78,
    "GU": 66,
    "AS": 60,
    "MP": 69,
}


def find_column(df: pl.DataFrame, pattern: str) -> str | None:
    """Find a column name that starts with the given pattern.

    Handles slight variations in GNMA column names across files (e.g., trailing spaces).
    """
    for col in df.columns:
        if col.startswith(pattern):
            return col
    return None


def normalize_gnma_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize GNMA column names by stripping trailing spaces.

    GNMA data has inconsistent column names across file versions (trailing spaces).
    When using diagonal_relaxed concat, this creates duplicate columns.
    This function coalesces the duplicate columns into single normalized names.
    """
    # Map of normalized name -> list of variant names
    column_variants = {}
    for col in df.columns:
        # Normalize by stripping trailing whitespace before closing paren
        normalized = col.rstrip(" ").rstrip(")").rstrip(" ") + ")" if ")" in col else col.rstrip()
        # Also normalize the actual name
        normalized = normalized.replace(" )", ")")
        if normalized not in column_variants:
            column_variants[normalized] = []
        column_variants[normalized].append(col)

    # For each normalized name with multiple variants, coalesce them
    coalesce_exprs = []
    rename_map = {}

    for normalized, variants in column_variants.items():
        if len(variants) > 1:
            # Coalesce multiple columns into one
            coalesce_exprs.append(pl.coalesce([pl.col(v) for v in variants]).alias(normalized))
        elif variants[0] != normalized:
            # Single variant but name differs - rename
            rename_map[variants[0]] = normalized

    # Apply coalesces
    if coalesce_exprs:
        # First add coalesced columns
        df = df.with_columns(coalesce_exprs)
        # Then drop the original variant columns (keep only coalesced)
        for normalized, variants in column_variants.items():
            if len(variants) > 1:
                for v in variants:
                    if v in df.columns and v != normalized:
                        df = df.drop(v)

    # Apply renames
    if rename_map:
        df = df.rename(rename_map)

    return df


def load_gnma_silver_data(
    gnma_dir: Path,
    min_year: int = 2009,
    max_year: int = 2024,
    issuer_id: int | None = 3937,
) -> pl.DataFrame:
    """Load GNMA silver dailyllmni loan-level data.

    Uses diagonal_relaxed concat to handle schema evolution across files
    (GNMA adds columns over time).

    Args:
        gnma_dir: Directory containing GNMA silver L parquet files
        min_year: Minimum year to include (based on First Payment Date)
        max_year: Maximum year to include
        issuer_id: Filter to specific issuer ID (3937 is common for FHLB matching)

    Returns:
        Combined GNMA loan-level data
    """
    parquet_files = sorted(gnma_dir.glob("*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {gnma_dir}")

    logger.info("Loading %d GNMA silver files...", len(parquet_files))

    # Read all files and concat with diagonal_relaxed to handle schema differences
    dfs = [pl.read_parquet(f) for f in parquet_files]
    df = pl.concat(dfs, how="diagonal_relaxed")

    # Normalize column names (handles trailing space variations across file versions)
    df = normalize_gnma_columns(df)

    logger.info("  Total rows loaded: %d", len(df))

    # Find the issuer ID column (name may vary slightly)
    issuer_col = find_column(df, "Issuer ID")
    if issuer_col is None:
        raise ValueError("Could not find Issuer ID column in GNMA data")

    # Filter by issuer if specified
    if issuer_id is not None:
        df = df.filter(pl.col(issuer_col).cast(pl.Int64, strict=False) == issuer_id)
        logger.info("  After issuer filter (%s): %d", issuer_id, len(df))

    # Find First Payment Date column for year filtering
    # This column exists in all files, unlike Loan Origination Date
    first_payment_col = find_column(df, "First Payment Date")
    if first_payment_col is None:
        raise ValueError("Could not find First Payment Date column in GNMA data")

    # Filter by first payment date year (YYYYMMDD format)
    # First payment is typically 1 month after origination, so this is a good proxy
    df = df.filter(
        (pl.col(first_payment_col).str.slice(0, 4).cast(pl.Int64, strict=False) >= min_year)
        & (
            pl.col(first_payment_col).str.slice(0, 4).cast(pl.Int64, strict=False) <= max_year + 1
        )  # +1 for 1-month offset
    )
    logger.info("  After year filter (%d-%d): %d", min_year, max_year, len(df))

    return df


def load_fhlb_ama_data(
    fhlb_dir: Path,
    min_year: int = 2009,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load FHLB AMA silver data.

    Args:
        fhlb_dir: Directory containing FHLB AMA silver parquet files
        min_year: Minimum year to include
        max_year: Maximum year to include

    Returns:
        Combined FHLB AMA data
    """
    parquet_files = sorted(fhlb_dir.glob("fhlb_ama_*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {fhlb_dir}")

    # Filter to relevant years
    year_files = [f for f in parquet_files if min_year <= int(f.stem.split("_")[-1]) <= max_year]

    logger.info("Loading %d FHLB AMA files (%d-%d)...", len(year_files), min_year, max_year)

    # Use diagonal_relaxed to handle schema evolution across years
    df = pl.concat([pl.read_parquet(f) for f in year_files], how="diagonal_relaxed")

    logger.info("  Total rows loaded: %d", len(df))

    # Filter to government mortgage types (FHA=1, VA=2, RD=3)
    df = df.filter(pl.col("MortgageType").is_in([1, 2, 3]))
    logger.info("  After mortgage type filter (FHA/VA/RD): %d", len(df))

    return df


def prepare_gnma_for_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare GNMA data for matching by selecting and transforming columns.

    Args:
        df: Raw GNMA silver data

    Returns:
        Prepared GNMA data with standardized columns
    """
    # Find column names dynamically to handle schema variations
    disclosure_col = find_column(df, "Disclosure Sequence Number")
    agency_col = find_column(df, "Agency (Agency Loan Type")
    first_payment_col = find_column(df, "First Payment Date")
    origination_date_col = find_column(df, "Loan Origination Date")  # May not exist in older files
    interest_rate_col = find_column(df, "Loan Interest Rate")
    opb_col = find_column(df, "Original Principal Balance")
    term_col = find_column(df, "Original Loan Term")
    ltv_col = find_column(df, "Loan To Value")
    cltv_col = find_column(df, "Combined LTV")
    dti_col = find_column(df, "Total Debt Expense Ratio")
    credit_col = find_column(df, "Credit Score")
    upfront_mip_col = find_column(df, "Upfront MIP")
    annual_mip_col = find_column(df, "Annual MIP")
    borrowers_col = find_column(df, "Number of Borrowers")
    fthb_col = find_column(df, "First Time Home Buyer")
    property_col = find_column(df, "Property Type")
    state_col = find_column(df, "State")

    # Build column selection list
    select_cols = [
        pl.col(disclosure_col).alias("gnma_loan_id"),
        pl.col(agency_col).alias("agency"),
        pl.col("Loan Purpose").alias("loan_purpose"),
        pl.col("Refinance Type").alias("refinance_type"),
        pl.col(first_payment_col).alias("first_payment_date"),
        pl.col(interest_rate_col).alias("interest_rate"),
        pl.col(opb_col).alias("original_balance"),
        pl.col(term_col).alias("original_term"),
        pl.col(ltv_col).alias("ltv"),
        pl.col(cltv_col).alias("cltv"),
        pl.col(dti_col).alias("dti"),
        pl.col(credit_col).alias("credit_score"),
        pl.col(borrowers_col).alias("num_borrowers"),
        pl.col(fthb_col).alias("first_time_buyer"),
        pl.col(property_col).alias("property_type"),
        pl.col(state_col).alias("state"),
    ]

    # Add optional columns if they exist
    if upfront_mip_col:
        select_cols.append(pl.col(upfront_mip_col).alias("upfront_mip"))
    if annual_mip_col:
        select_cols.append(pl.col(annual_mip_col).alias("annual_mip"))
    if origination_date_col:
        select_cols.append(pl.col(origination_date_col).alias("origination_date"))

    df = df.select(select_cols)

    # Convert to numeric types
    df = df.with_columns(
        [
            # Interest rate: stored as basis points (e.g., 4500 = 4.5%)
            pl.col("interest_rate").cast(pl.Float64, strict=False),
            pl.col("original_balance").cast(pl.Float64, strict=False),
            pl.col("original_term").cast(pl.Int64, strict=False),
            pl.col("ltv").cast(pl.Float64, strict=False),
            pl.col("cltv").cast(pl.Float64, strict=False),
            pl.col("dti").cast(pl.Float64, strict=False),
            pl.col("credit_score").cast(pl.Int64, strict=False),
            pl.col("num_borrowers").cast(pl.Int64, strict=False),
            pl.col("loan_purpose").cast(pl.Int64, strict=False),
        ]
    )

    # Add State FIPS code
    state_fips_expr = pl.col("state").replace_strict(STATE_FIPS, default=None).alias("state_fips")
    df = df.with_columns(state_fips_expr)

    # Map agency codes: F=FHA=1, V=VA=2, R=RD=3
    df = df.with_columns(
        pl.when(pl.col("agency") == "F")
        .then(1)
        .when(pl.col("agency") == "V")
        .then(2)
        .when(pl.col("agency") == "R")
        .then(3)
        .otherwise(None)
        .alias("agency_code")
    )

    # Derive origination year
    # Use Loan Origination Date when available (exists in files from 2015+)
    # Fall back to first_payment_date year when origination_date is null
    if "origination_date" in df.columns:
        df = df.with_columns(
            pl.coalesce(
                pl.col("origination_date").str.slice(0, 4).cast(pl.Int64, strict=False),
                pl.col("first_payment_date").str.slice(0, 4).cast(pl.Int64, strict=False),
            ).alias("origination_year")
        )
    else:
        # No origination date column available, use first payment date
        df = df.with_columns(
            pl.col("first_payment_date")
            .str.slice(0, 4)
            .cast(pl.Int64, strict=False)
            .alias("origination_year")
        )

    # Map property type to unit count (1-4 for single family, etc.)
    df = df.with_columns(
        pl.col("property_type").cast(pl.Int64, strict=False).alias("property_units")
    )

    return df


def prepare_fhlb_for_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare FHLB AMA data for matching by selecting and transforming columns.

    Args:
        df: Raw FHLB AMA silver data

    Returns:
        Prepared FHLB data with standardized columns
    """
    df = df.select(
        [
            pl.col("LoanCharacteristicsID").alias("fhlb_loan_id"),
            pl.col("FIPSStateNumericCode").alias("state_fips"),
            pl.col("LTVRatioPercent").alias("ltv"),
            pl.col("NoteDate").alias("note_year"),
            pl.col("LoanAcquisitionDate").alias("acquisition_year"),
            pl.col("LoanPurposeType").alias("loan_purpose"),
            pl.col("MortgageType").alias("mortgage_type"),  # 1=FHA, 2=VA, 3=RD
            pl.col("LoanAmortizationMaxTermMonths").alias("original_term"),
            pl.col("BorrowerCount").alias("num_borrowers"),
            pl.col("BorrowerFirstTimeHomebuyer").alias("first_time_buyer"),
            pl.col("PropertyUnitCount").alias("property_units"),
            pl.col("NoteRatePercent").alias("interest_rate"),  # as percent (e.g., 4.5)
            pl.col("NoteAmount").alias("note_amount"),  # in dollars
            pl.col("TotalDebtExpenseRatioPercent").alias("dti"),
            pl.col("Borrower1CreditScoreValue").alias("credit_score_1"),
            pl.col("Borrower2CreditScoreValue").alias("credit_score_2"),
            pl.col("PMICoveragePercent").alias("pmi_coverage"),
            pl.col("Bank").alias("fhlb_bank"),
        ]
    )

    # Filter out certain banks that don't participate in this program
    df = df.filter(~pl.col("fhlb_bank").is_in(["Indianapolis", "Cincinnati"]))

    return df


def match_gnma_fhlb(
    gnma_df: pl.DataFrame,
    fhlb_df: pl.DataFrame,
    use_best_match: bool = True,
    tolerances: MatchingTolerances | None = None,
) -> pl.DataFrame:
    """Match GNMA loans to FHLB AMA acquisitions.

    Uses a two-stage tolerance matching approach:
    1. Join on state and agency/mortgage type
    2. Filter using candidate-finding tolerances (looser)
    3. Resolve duplicates via mutual best-match scoring
    4. Apply post-match tolerances for final quality filtering (stricter)

    The tight tolerances ensure high-quality matches—analysis shows >95% of
    matched loans have near-exact values for rate, LTV, and DTI.

    Data scale reference:
    - GNMA interest_rate: basis points * 100 (3750 = 3.75%)
    - FHLB interest_rate: percent (3.75 = 3.75%)
    - GNMA original_balance: cents (11000000 = $110,000)
    - FHLB note_amount: dollars (110000 = $110,000)
    - GNMA ltv/dti: basis points (9900 = 99%)
    - FHLB ltv/dti: percent (99.0 = 99%)

    Args:
        gnma_df: Prepared GNMA data
        fhlb_df: Prepared FHLB data
        use_best_match: If True (default), use mutual best-match scoring to resolve duplicates.
            If False, use strict 1:1 unique matching only.
        tolerances: Tolerance configuration. If None, uses DEFAULT_TOLERANCES.

    Returns:
        Matched loan pairs with all relevant columns
    """
    if tolerances is None:
        tolerances = DEFAULT_TOLERANCES
    logger.info("Starting match: %d GNMA loans, %d FHLB loans", len(gnma_df), len(fhlb_df))

    # Initial join on state and agency type
    merged = gnma_df.join(
        fhlb_df,
        left_on=["state_fips", "agency_code"],
        right_on=["state_fips", "mortgage_type"],
        how="inner",
        suffix="_fhlb",
    )

    logger.info("  After state/agency join: %d candidate pairs", len(merged))

    # Apply matching filters with tolerances (candidate-finding stage)
    # Interest rate: GNMA in bp*100 (3750 = 3.75%), FHLB in percent (3.75)
    # Convert GNMA to percent: GNMA / 1000
    merged = merged.filter(
        (pl.col("interest_rate") / 1000 - pl.col("interest_rate_fhlb")).abs()
        <= tolerances.rate_tolerance
    )

    # Principal balance: GNMA in cents, FHLB note_amount in dollars
    # Convert GNMA to dollars: GNMA / 100
    merged = merged.filter(
        (pl.col("original_balance") / 100 - pl.col("note_amount")).abs()
        <= tolerances.balance_tolerance
    )

    # Loan term
    merged = merged.filter(
        (pl.col("original_term") - pl.col("original_term_fhlb")).abs() <= tolerances.term_tolerance
    )

    # DTI: GNMA in bp (2618 = 26.18%), FHLB in percent (26.18)
    # Convert GNMA to percent: GNMA / 100
    # Allow null on either side to pass (missing data = don't filter)
    merged = merged.filter(
        pl.col("dti").is_null()
        | pl.col("dti_fhlb").is_null()
        | ((pl.col("dti") / 100 - pl.col("dti_fhlb")).abs() <= tolerances.dti_tolerance)
    )

    # LTV: GNMA in bp (9900 = 99%), FHLB in percent (99.0)
    # Convert GNMA to percent: GNMA / 100
    # Allow null on either side to pass (missing data = don't filter)
    merged = merged.filter(
        pl.col("ltv").is_null()
        | pl.col("ltv_fhlb").is_null()
        | ((pl.col("ltv") / 100 - pl.col("ltv_fhlb")).abs() <= tolerances.ltv_tolerance)
    )

    logger.info("  After numeric tolerances: %d pairs", len(merged))

    # Property type / unit count match
    merged = merged.filter(pl.col("property_units") == pl.col("property_units_fhlb"))

    # Number of borrowers match
    merged = merged.filter(pl.col("num_borrowers") == pl.col("num_borrowers_fhlb"))

    logger.info("  After borrower/property match: %d pairs", len(merged))

    # Loan purpose compatibility
    # GNMA: 1=Purchase, 2=Refi (rate/term), 3=Refi (cash-out), 4=Construction
    # FHLB: 1=Purchase, 2=Refi (rate/term), 6=Refi (cash-out)
    merged = merged.filter(
        ~(pl.col("loan_purpose").is_in([1, 4]) & pl.col("loan_purpose_fhlb").is_in([2, 6]))
        & ~(pl.col("loan_purpose").is_in([2, 3]) & (pl.col("loan_purpose_fhlb") == 1))
    )

    # Origination year match (GNMA origination_year vs FHLB note_year)
    merged = merged.filter(pl.col("origination_year") == pl.col("note_year"))

    logger.info("  After loan purpose/date match: %d pairs", len(merged))

    if use_best_match:
        # Mutual best-match approach: use match quality scoring
        # Compute match score (lower = better match)
        # Score is sum of normalized deviations from each tolerance
        # Use fill_null(0) for LTV/DTI so missing values don't penalize the score
        merged = merged.with_columns(
            [
                (
                    (
                        (pl.col("interest_rate") / 1000 - pl.col("interest_rate_fhlb")).abs()
                        / tolerances.rate_tolerance
                    )
                    + (
                        (pl.col("original_balance") / 100 - pl.col("note_amount")).abs()
                        / tolerances.balance_tolerance
                    )
                    + (
                        (pl.col("dti") / 100 - pl.col("dti_fhlb")).abs() / tolerances.dti_tolerance
                    ).fill_null(0)
                    + (
                        (pl.col("ltv") / 100 - pl.col("ltv_fhlb")).abs() / tolerances.ltv_tolerance
                    ).fill_null(0)
                ).alias("match_score")
            ]
        )

        # Rank each pair within GNMA and FHLB groups
        merged = merged.with_columns(
            [
                pl.col("match_score")
                .rank(method="dense")
                .over("gnma_loan_id")
                .alias("rank_in_gnma"),
                pl.col("match_score")
                .rank(method="dense")
                .over("fhlb_loan_id")
                .alias("rank_in_fhlb"),
            ]
        )

        # Keep only mutual best matches (best for both sides)
        unique_matches = merged.filter(
            (pl.col("rank_in_gnma") == 1) & (pl.col("rank_in_fhlb") == 1)
        )

        logger.info("  Mutual best matches: %d", len(unique_matches))
    else:
        # Strict 1:1 unique matching (original approach)
        merged = merged.with_columns(
            [
                pl.len().over("gnma_loan_id").alias("gnma_match_count"),
                pl.len().over("fhlb_loan_id").alias("fhlb_match_count"),
            ]
        )

        unique_matches = merged.filter(
            (pl.col("gnma_match_count") == 1) & (pl.col("fhlb_match_count") == 1)
        )

        logger.info("  Unique 1:1 matches: %d", len(unique_matches))

    # Apply post-match filtering if enabled
    if tolerances.apply_post_match_filter:
        pre_filter_count = len(unique_matches)

        # Compute differences for filtering
        unique_matches = unique_matches.with_columns(
            [
                ((pl.col("interest_rate") / 1000) - pl.col("interest_rate_fhlb"))
                .abs()
                .alias("_rate_diff"),
                ((pl.col("original_balance") / 100) - pl.col("note_amount"))
                .abs()
                .alias("_balance_diff"),
                ((pl.col("ltv") / 100) - pl.col("ltv_fhlb")).abs().alias("_ltv_diff"),
                ((pl.col("dti") / 100) - pl.col("dti_fhlb")).abs().alias("_dti_diff"),
            ]
        )

        # Apply stricter post-match tolerances
        # Interest rate: strictly less than
        unique_matches = unique_matches.filter(
            pl.col("_rate_diff") < tolerances.post_rate_tolerance
        )
        # Balance: weakly less than or equal
        unique_matches = unique_matches.filter(
            pl.col("_balance_diff") <= tolerances.post_balance_tolerance
        )
        # LTV: weakly less than or equal (allow null = missing data passes)
        unique_matches = unique_matches.filter(
            pl.col("_ltv_diff").is_null() | (pl.col("_ltv_diff") <= tolerances.post_ltv_tolerance)
        )
        # DTI: weakly less than or equal (allow null = missing data passes)
        unique_matches = unique_matches.filter(
            pl.col("_dti_diff").is_null() | (pl.col("_dti_diff") <= tolerances.post_dti_tolerance)
        )

        # Drop temporary columns
        unique_matches = unique_matches.drop(
            ["_rate_diff", "_balance_diff", "_ltv_diff", "_dti_diff"]
        )

        dropped = pre_filter_count - len(unique_matches)
        logger.info("  After post-match filter: %d (dropped %d)", len(unique_matches), dropped)

    return unique_matches


def run_gnma_fhlb_matching(
    min_year: int = 2009,
    max_year: int = 2024,
    issuer_id: int | None = 3937,
    tolerances: MatchingTolerances | None = None,
) -> pl.DataFrame:
    """Run the complete GNMA-FHLB matching pipeline.

    Args:
        min_year: Minimum year for data
        max_year: Maximum year for data
        issuer_id: GNMA issuer ID to filter (3937 is common)
        tolerances: Tolerance configuration. If None, uses DEFAULT_TOLERANCES.

    Returns:
        Matched loan pairs
    """
    if tolerances is None:
        tolerances = DEFAULT_TOLERANCES

    ensure_directories()

    # Get data directories
    gnma_dir = get_gnma_silver_dir()
    fhlb_dir = get_fhlb_ama_silver_dir()

    logger.info("GNMA data directory: %s", gnma_dir)
    logger.info("FHLB data directory: %s", fhlb_dir)
    logger.info(
        "Tolerances: rate +/-%s%%, balance +/-$%s, LTV +/-%s%%, DTI +/-%s%%",
        tolerances.rate_tolerance,
        f"{tolerances.balance_tolerance:,.0f}",
        tolerances.ltv_tolerance,
        tolerances.dti_tolerance,
    )
    if tolerances.apply_post_match_filter:
        logger.info(
            "Post-filter: rate <%s%%, balance <=$%s, LTV <=%s%%, DTI <=%s%%",
            tolerances.post_rate_tolerance,
            f"{tolerances.post_balance_tolerance:,.0f}",
            tolerances.post_ltv_tolerance,
            tolerances.post_dti_tolerance,
        )

    # Load data
    gnma_raw = load_gnma_silver_data(gnma_dir, min_year, max_year, issuer_id)
    fhlb_raw = load_fhlb_ama_data(fhlb_dir, min_year, max_year)

    # Prepare for matching
    gnma_prepared = prepare_gnma_for_matching(gnma_raw)
    fhlb_prepared = prepare_fhlb_for_matching(fhlb_raw)

    # Run matching
    matches = match_gnma_fhlb(gnma_prepared, fhlb_prepared, tolerances=tolerances)

    # Save crosswalk (just the ID pairs)
    crosswalk = matches.select(["gnma_loan_id", "fhlb_loan_id"])
    crosswalk_file = CROSSWALK_OUTPUT_DIR / "gnma_fhlb_crosswalk.parquet"
    crosswalk.write_parquet(crosswalk_file)

    logger.info("Crosswalk saved to: %s", crosswalk_file)

    # Save full match details to intermediate
    details_file = INTERMEDIATE_DIR / "gnma_fhlb_match_details.parquet"
    matches.write_parquet(details_file)

    logger.info("Match details saved to: %s", details_file)

    return matches


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")
    df = run_gnma_fhlb_matching()
    logger.info("Total matches: %d", len(df))
