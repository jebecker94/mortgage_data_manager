#!/usr/bin/env python3
"""FNMA-FHLB Matching Logic using Polars.

Matches FNMA UMBS ILLD (Investor Loan-Level Disclosure) data with
FHLB AMA (Acquired Member Assets) data to test the mutual exclusivity hypothesis:
loans sold to FNMA through UMBS (with FHLB as seller) should NOT appear in
FHLB AMA direct acquisitions.

Expected result: near-zero matches, confirming that these represent distinct
loan populations with different distribution channels.

The matching uses a multi-field approach with intentionally loose tolerances
to confirm the hypothesis:
1. Filter UMBS data to loans where Seller Name contains "FEDERAL HOME LOAN BANK"
2. Filter FHLB AMA to conventional loans only (MortgageType == 0)
3. Join on state FIPS
4. Apply tolerance-based matching on rate, balance, LTV, DTI, term
5. Exact match on property units, borrowers, origination year
6. Loan purpose compatibility filter
7. Resolve duplicates via mutual best-match scoring
"""

# Standard library imports

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Third-party imports
import polars as pl

# Local application imports
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_fhlb.config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
    ensure_directories,
    get_fhlb_ama_silver_dir,
    get_umbs_fnma_illd_bronze_dir,
)

logger = get_logger(__name__)


@dataclass
class FNMAMatchingTolerances:
    """Configuration for FNMA-FHLB matching tolerances.

    Intentionally loose tolerances since we expect near-zero matches.
    This ensures that any failure to match is not due to overly strict criteria.

    Attributes:
        rate_tolerance: Interest rate tolerance in percentage points (default: 0.5%)
        balance_tolerance: Principal balance tolerance in dollars (default: $5,000)
        ltv_tolerance: LTV tolerance in percentage points (default: 2.0%)
        dti_tolerance: DTI tolerance in percentage points (default: 2.0%)
        term_tolerance: Loan term tolerance in months (default: 12)
    """

    rate_tolerance: float = 0.5
    balance_tolerance: float = 5000.0
    ltv_tolerance: float = 2.0
    dti_tolerance: float = 2.0
    term_tolerance: int = 12


# Default tolerances (intentionally loose)
DEFAULT_FNMA_TOLERANCES = FNMAMatchingTolerances()


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


def load_fnma_illd_data(
    fnma_dir: Path,
    min_year: int = 2019,
    max_year: int = 2024,
    seller_filter: str = "FEDERAL HOME LOAN BANK",
) -> pl.DataFrame:
    """Load FNMA UMBS ILLD (Investor Loan-Level Disclosure) data.

    Filters to loans where the Seller Name contains the specified string
    (case-insensitive), typically "FEDERAL HOME LOAN BANK" to identify
    loans sold by FHLBanks to FNMA through the UMBS program.

    Args:
        fnma_dir: Directory containing FNMA ILLD parquet files (FNM_ILLD_YYYYMM.parquet)
        min_year: Minimum year to include (based on First Payment Date)
        max_year: Maximum year to include
        seller_filter: Filter seller names containing this string (case-insensitive)

    Returns:
        FNMA ILLD data filtered to FHLB sellers
    """
    parquet_files = sorted(fnma_dir.glob("FNM_ILLD_*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No FNM_ILLD parquet files found in {fnma_dir}")

    # Filter to year range based on filename (YYYYMM)
    year_files = []
    for f in parquet_files:
        # Skip revision files for now (use original)
        if "_REV" in f.stem:
            continue
        # Extract YYYYMM from filename
        yyyymm = f.stem.split("_")[-1]
        if len(yyyymm) == 6:
            file_year = int(yyyymm[:4])
            if min_year <= file_year <= max_year:
                year_files.append(f)

    if not year_files:
        raise FileNotFoundError(
            f"No FNM_ILLD files found for years {min_year}-{max_year} in {fnma_dir}"
        )

    logger.info("Loading %d FNMA ILLD files (%d-%d)...", len(year_files), min_year, max_year)

    # Read all files and concat with diagonal_relaxed to handle schema differences
    dfs = [pl.read_parquet(f) for f in year_files]
    df = pl.concat(dfs, how="diagonal_relaxed")

    logger.info("  Total rows loaded: %d", len(df))

    # Filter by seller name containing the filter string (case-insensitive)
    df = df.filter(pl.col("Seller Name").str.to_lowercase().str.contains(seller_filter.lower()))

    logger.info("  After seller filter ('%s'): %d", seller_filter, len(df))

    # Filter by First Payment Date year
    # First Payment Date is stored as integer in M(M)YYYY format (e.g., 22024 = Feb 2024, 122024 = Dec 2024)
    # Year can be extracted as value % 10000
    df = df.filter(
        (pl.col("First Payment Date") % 10000 >= min_year)
        & (pl.col("First Payment Date") % 10000 <= max_year + 1)
    )

    logger.info("  After year filter (%d-%d): %d", min_year, max_year, len(df))

    return df


def load_fhlb_ama_conventional(
    fhlb_dir: Path,
    min_year: int = 2019,
    max_year: int = 2024,
    banks: list[str] | None = None,
) -> pl.DataFrame:
    """Load FHLB AMA silver data filtered to conventional loans only.

    Args:
        fhlb_dir: Directory containing FHLB AMA silver parquet files
        min_year: Minimum year to include
        max_year: Maximum year to include
        banks: Filter to specific FHLB banks (e.g., ["Chicago"]). If None, all banks included.

    Returns:
        FHLB AMA data filtered to conventional mortgages (MortgageType == 0)
    """
    parquet_files = sorted(fhlb_dir.glob("fhlb_ama_*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No fhlb_ama_*.parquet files found in {fhlb_dir}")

    # Filter to relevant years
    year_files = [f for f in parquet_files if min_year <= int(f.stem.split("_")[-1]) <= max_year]

    if not year_files:
        raise FileNotFoundError(
            f"No FHLB AMA files found for years {min_year}-{max_year} in {fhlb_dir}"
        )

    logger.info("Loading %d FHLB AMA files (%d-%d)...", len(year_files), min_year, max_year)

    # Use diagonal_relaxed to handle schema evolution across years
    df = pl.concat([pl.read_parquet(f) for f in year_files], how="diagonal_relaxed")

    logger.info("  Total rows loaded: %d", len(df))

    # Filter to conventional loans only (MortgageType == 0)
    # Exclude FHA (1), VA (2), RD (3) - these are handled by GNMA matching
    df = df.filter(pl.col("MortgageType") == 0)
    logger.info("  After conventional filter (MortgageType=0): %d", len(df))

    # Filter to specific banks if requested
    if banks:
        df = df.filter(pl.col("Bank").is_in(banks))
        logger.info("  After bank filter (%s): %d", banks, len(df))

    return df


def prepare_fnma_for_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare FNMA ILLD data for matching by selecting and transforming columns.

    Args:
        df: Raw FNMA ILLD data

    Returns:
        Prepared FNMA data with standardized columns
    """
    # Select and rename columns for matching
    df = df.select(
        [
            pl.col("Loan Identifier").alias("fnma_loan_id"),
            pl.col("Origination Mortgage Loan Amount").alias("original_balance"),
            pl.col("Origination Interest Rate").alias("interest_rate"),
            pl.col("Origination Loan-To-Value (LTV)").alias("ltv"),
            pl.col("Origination Debt-To-Income Ratio").alias("dti"),
            pl.col("Origination Loan Term").alias("original_term"),
            pl.col("Property State").alias("state"),
            pl.col("First Payment Date").alias("first_payment_date"),
            pl.col("Number of Units").alias("property_units"),
            pl.col("Number of Borrowers").alias("num_borrowers"),
            pl.col("Origination Loan Purpose").alias("loan_purpose_str"),
            pl.col("Seller Name").alias("seller_name"),
        ]
    )

    # Convert to numeric types
    df = df.with_columns(
        [
            pl.col("original_balance").cast(pl.Float64, strict=False),
            pl.col("interest_rate").cast(pl.Float64, strict=False),
            pl.col("ltv").cast(pl.Float64, strict=False),
            pl.col("dti").cast(pl.Float64, strict=False),
            pl.col("original_term").cast(pl.Int64, strict=False),
            pl.col("property_units").cast(pl.Int64, strict=False),
            pl.col("num_borrowers").cast(pl.Int64, strict=False),
        ]
    )

    # Add State FIPS code
    df = df.with_columns(
        pl.col("state").replace_strict(STATE_FIPS, default=None).alias("state_fips")
    )

    # Derive origination year from First Payment Date
    # First Payment Date is stored as integer in M(M)YYYY format, year = value % 10000
    # First payment is typically 1-2 months after origination, so this is close enough
    df = df.with_columns((pl.col("first_payment_date") % 10000).alias("origination_year"))

    # Map loan purpose: P=1 (Purchase), C=7 (Cash-out), R=2 (Refi)
    # FHLB uses: 1=Purchase, 2=Refi (rate/term), 6=Refi (cash-out)
    df = df.with_columns(
        pl.when(pl.col("loan_purpose_str") == "P")
        .then(1)
        .when(pl.col("loan_purpose_str") == "R")
        .then(2)
        .when(pl.col("loan_purpose_str") == "C")
        .then(6)
        .otherwise(None)
        .alias("loan_purpose")
    )

    return df


def prepare_fhlb_for_fnma_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare FHLB AMA data for matching with FNMA data.

    Args:
        df: Raw FHLB AMA silver data (already filtered to conventional)

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
            pl.col("LoanAmortizationMaxTermMonths").alias("original_term"),
            pl.col("BorrowerCount").alias("num_borrowers"),
            pl.col("PropertyUnitCount").alias("property_units"),
            pl.col("NoteRatePercent").alias("interest_rate"),  # as percent (e.g., 4.5)
            pl.col("NoteAmount").alias("note_amount"),  # in dollars
            pl.col("TotalDebtExpenseRatioPercent").alias("dti"),
            pl.col("Bank").alias("fhlb_bank"),
        ]
    )

    return df


def match_fnma_fhlb(
    fnma_df: pl.DataFrame,
    fhlb_df: pl.DataFrame,
    tolerances: FNMAMatchingTolerances | None = None,
) -> pl.DataFrame:
    """Match FNMA ILLD loans to FHLB AMA acquisitions.

    Uses tolerance-based matching with intentionally loose tolerances
    to test the mutual exclusivity hypothesis.

    Expected result: near-zero matches.

    Args:
        fnma_df: Prepared FNMA data
        fhlb_df: Prepared FHLB data
        tolerances: Tolerance configuration. If None, uses DEFAULT_FNMA_TOLERANCES.

    Returns:
        Matched loan pairs (expected to be empty or near-empty)
    """
    if tolerances is None:
        tolerances = DEFAULT_FNMA_TOLERANCES

    logger.info("Starting match: %d FNMA loans, %d FHLB loans", len(fnma_df), len(fhlb_df))

    # Initial join on state
    merged = fnma_df.join(
        fhlb_df,
        on="state_fips",
        how="inner",
        suffix="_fhlb",
    )

    logger.info("  After state join: %d candidate pairs", len(merged))

    if len(merged) == 0:
        return merged

    # Apply matching filters with tolerances

    # Interest rate: both in percent (e.g., 4.5)
    merged = merged.filter(
        (pl.col("interest_rate") - pl.col("interest_rate_fhlb")).abs() <= tolerances.rate_tolerance
    )

    logger.info("  After rate tolerance: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Principal balance: both in dollars
    merged = merged.filter(
        (pl.col("original_balance") - pl.col("note_amount")).abs() <= tolerances.balance_tolerance
    )

    logger.info("  After balance tolerance: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Loan term (allow tolerance for rounding differences)
    merged = merged.filter(
        (pl.col("original_term") - pl.col("original_term_fhlb")).abs() <= tolerances.term_tolerance
    )

    logger.info("  After term tolerance: %d", len(merged))

    if len(merged) == 0:
        return merged

    # LTV tolerance (allow null on either side to pass)
    merged = merged.filter(
        pl.col("ltv").is_null()
        | pl.col("ltv_fhlb").is_null()
        | ((pl.col("ltv") - pl.col("ltv_fhlb")).abs() <= tolerances.ltv_tolerance)
    )

    logger.info("  After LTV tolerance: %d", len(merged))

    if len(merged) == 0:
        return merged

    # DTI tolerance (allow null on either side to pass)
    merged = merged.filter(
        pl.col("dti").is_null()
        | pl.col("dti_fhlb").is_null()
        | ((pl.col("dti") - pl.col("dti_fhlb")).abs() <= tolerances.dti_tolerance)
    )

    logger.info("  After DTI tolerance: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Property units match
    merged = merged.filter(pl.col("property_units") == pl.col("property_units_fhlb"))

    logger.info("  After property units match: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Number of borrowers match
    merged = merged.filter(pl.col("num_borrowers") == pl.col("num_borrowers_fhlb"))

    logger.info("  After borrowers match: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Loan purpose compatibility
    # FNMA: 1=Purchase, 2=Refi, 6=Cash-out
    # FHLB: 1=Purchase, 2=Refi (rate/term), 6=Refi (cash-out)
    merged = merged.filter(
        (pl.col("loan_purpose") == pl.col("loan_purpose_fhlb"))
        |
        # Allow refi types to match each other
        (pl.col("loan_purpose").is_in([2, 6]) & pl.col("loan_purpose_fhlb").is_in([2, 6]))
    )

    logger.info("  After loan purpose match: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Origination year match (FNMA origination_year vs FHLB note_year)
    merged = merged.filter(pl.col("origination_year") == pl.col("note_year"))

    logger.info("  After year match: %d", len(merged))

    if len(merged) == 0:
        return merged

    # Mutual best-match scoring to resolve duplicates
    # Compute match score (lower = better match)
    merged = merged.with_columns(
        [
            (
                (
                    (pl.col("interest_rate") - pl.col("interest_rate_fhlb")).abs()
                    / tolerances.rate_tolerance
                )
                + (
                    (pl.col("original_balance") - pl.col("note_amount")).abs()
                    / tolerances.balance_tolerance
                )
                + ((pl.col("dti") - pl.col("dti_fhlb")).abs() / tolerances.dti_tolerance).fill_null(
                    0
                )
                + ((pl.col("ltv") - pl.col("ltv_fhlb")).abs() / tolerances.ltv_tolerance).fill_null(
                    0
                )
            ).alias("match_score")
        ]
    )

    # Rank each pair within FNMA and FHLB groups
    merged = merged.with_columns(
        [
            pl.col("match_score").rank(method="dense").over("fnma_loan_id").alias("rank_in_fnma"),
            pl.col("match_score").rank(method="dense").over("fhlb_loan_id").alias("rank_in_fhlb"),
        ]
    )

    # Keep only mutual best matches (best for both sides)
    unique_matches = merged.filter((pl.col("rank_in_fnma") == 1) & (pl.col("rank_in_fhlb") == 1))

    logger.info("  Mutual best matches: %d", len(unique_matches))

    # Ensure 1:1 by checking for remaining duplicates
    unique_matches = unique_matches.with_columns(
        [
            pl.len().over("fnma_loan_id").alias("fnma_count"),
            pl.len().over("fhlb_loan_id").alias("fhlb_count"),
        ]
    )

    unique_matches = unique_matches.filter(
        (pl.col("fnma_count") == 1) & (pl.col("fhlb_count") == 1)
    )

    logger.info("  Final 1:1 matches: %d", len(unique_matches))

    # Drop helper columns
    unique_matches = unique_matches.drop(
        ["match_score", "rank_in_fnma", "rank_in_fhlb", "fnma_count", "fhlb_count"]
    )

    return unique_matches


def run_fnma_fhlb_matching(
    min_year: int = 2019,
    max_year: int = 2024,
    seller_filter: str = "FEDERAL HOME LOAN BANK",
    tolerances: FNMAMatchingTolerances | None = None,
) -> pl.DataFrame:
    """Run the complete FNMA-FHLB matching pipeline.

    Tests the mutual exclusivity hypothesis: loans sold to FNMA through UMBS
    (with FHLB as seller) should NOT appear in FHLB AMA direct acquisitions.

    Args:
        min_year: Minimum year for data (default: 2019, when UMBS ILLD starts)
        max_year: Maximum year for data
        seller_filter: Filter FNMA data to sellers containing this string
        tolerances: Tolerance configuration. If None, uses DEFAULT_FNMA_TOLERANCES.

    Returns:
        Matched loan pairs (expected to be empty or near-empty)
    """
    if tolerances is None:
        tolerances = DEFAULT_FNMA_TOLERANCES

    ensure_directories()

    # Get data directories
    fnma_dir = get_umbs_fnma_illd_bronze_dir()
    fhlb_dir = get_fhlb_ama_silver_dir()

    logger.info("FNMA-FHLB Matching (Mutual Exclusivity Test)")
    logger.info("FNMA ILLD directory: %s", fnma_dir)
    logger.info("FHLB AMA directory: %s", fhlb_dir)
    logger.info("Seller filter: '%s'", seller_filter)
    logger.info(
        "Tolerances: rate +/-%s%%, balance +/-$%s, LTV +/-%s%%, DTI +/-%s%%",
        tolerances.rate_tolerance,
        f"{tolerances.balance_tolerance:,.0f}",
        tolerances.ltv_tolerance,
        tolerances.dti_tolerance,
    )

    # Load data
    fnma_raw = load_fnma_illd_data(fnma_dir, min_year, max_year, seller_filter)
    fhlb_raw = load_fhlb_ama_conventional(fhlb_dir, min_year, max_year)

    # Prepare for matching
    fnma_prepared = prepare_fnma_for_matching(fnma_raw)
    fhlb_prepared = prepare_fhlb_for_fnma_matching(fhlb_raw)

    logger.info("Prepared FNMA records: %d", len(fnma_prepared))
    logger.info("Prepared FHLB records: %d", len(fhlb_prepared))

    # Run matching
    matches = match_fnma_fhlb(fnma_prepared, fhlb_prepared, tolerances)

    # Calculate match rate
    match_rate = len(matches) / len(fnma_prepared) * 100 if len(fnma_prepared) > 0 else 0

    logger.info("Total matches: %d", len(matches))
    logger.info("Match rate: %.2f%%", match_rate)
    if match_rate < 1.0:
        logger.info(
            "Hypothesis confirmed: FNMA UMBS (FHLB seller) and FHLB AMA are mutually exclusive"
        )
    else:
        logger.warning("Unexpected overlap detected - investigate matches")

    # Save crosswalk (just the ID pairs)
    crosswalk = (
        matches.select(["fnma_loan_id", "fhlb_loan_id"])
        if len(matches) > 0
        else pl.DataFrame(
            {
                "fnma_loan_id": pl.Series([], dtype=pl.Int64),
                "fhlb_loan_id": pl.Series([], dtype=pl.Int64),
            }
        )
    )
    crosswalk_file = CROSSWALK_OUTPUT_DIR / "fnma_fhlb_crosswalk.parquet"
    crosswalk.write_parquet(crosswalk_file)

    logger.info("Crosswalk saved to: %s", crosswalk_file)

    # Save full match details to intermediate (if any matches)
    if len(matches) > 0:
        details_file = INTERMEDIATE_DIR / "fnma_fhlb_match_details.parquet"
        matches.write_parquet(details_file)
        logger.info("Match details saved to: %s", details_file)

    return matches


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")
    df = run_fnma_fhlb_matching()
    logger.info("Total matches: %d", len(df))
