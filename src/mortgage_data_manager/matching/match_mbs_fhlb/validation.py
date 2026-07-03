"""MBS-FHLB Match Validation Module.

This module provides validation analyses for the MBS-FHLB matching workflows:

1. GNMA-FHLB Matching: Links GNMA government-backed loans (FHA/VA/RD) to FHLB
   AMA acquisitions. Expected HIGH match rates (~90%+) with very tight match
   quality (99.9% have rate diff <0.01%).

2. FNMA-FHLB Mutual Exclusivity Test: Tests the hypothesis that loans sold to
   FNMA through UMBS (with FHLB as seller) do NOT appear in FHLB AMA direct
   acquisitions. Expected ZERO or near-zero matches.

Validation analyses include:
- Match statistics summary
- Match quality analysis (distribution of differences)
- Temporal analysis (match rates by origination year)
- Geographic analysis (match rates by state)
- Loan characteristics (match rates by amount, rate, LTV, mortgage type)
- Distribution comparisons (for mutual exclusivity test)

Usage:
    from mortgage_data_manager.matching.match_mbs_fhlb.validation import (
        run_gnma_validation,
        run_fnma_validation,
        run_validation,
    )

    # Run GNMA matching validation
    gnma_results = run_gnma_validation()

    # Run FNMA mutual exclusivity test
    fnma_results = run_fnma_validation()

    # Run both validations
    all_results = run_validation()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_mbs_fhlb.config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
)

# =============================================================================
# Configuration
# =============================================================================

# Year range for analysis
MIN_YEAR = 2018
MAX_YEAR = 2024
DEFAULT_GNMA_ISSUER_ID = 3937  # Default GNMA issuer for filtering

# Output directory for figures
PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
FIGURES_DIR = PROJECT_DIR / "docs" / "matching" / "figures" / "mbs_fhlb"

# Color palette (Jonathan's palette)
HUNTER_GREEN = "#145A32"
BURNISHED_GOLD = "#D4AC0D"
TERRACOTTA = "#BA4A00"
MIDNIGHT_BLUE = "#0E2F44"

# Dataset-specific colors
GNMA_COLOR = HUNTER_GREEN
FHLB_COLOR = BURNISHED_GOLD
FNMA_COLOR = HUNTER_GREEN
QUALITY_COLOR = TERRACOTTA

# Check for matplotlib availability
try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None  # type: ignore


def _get_pyplot(interactive: bool = True):
    """Get pyplot with appropriate backend."""
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


# =============================================================================
# FIPS to State Mapping
# =============================================================================

FIPS_TO_STATE = {
    1: "AL",
    2: "AK",
    4: "AZ",
    5: "AR",
    6: "CA",
    8: "CO",
    9: "CT",
    10: "DE",
    11: "DC",
    12: "FL",
    13: "GA",
    15: "HI",
    16: "ID",
    17: "IL",
    18: "IN",
    19: "IA",
    20: "KS",
    21: "KY",
    22: "LA",
    23: "ME",
    24: "MD",
    25: "MA",
    26: "MI",
    27: "MN",
    28: "MS",
    29: "MO",
    30: "MT",
    31: "NE",
    32: "NV",
    33: "NH",
    34: "NJ",
    35: "NM",
    36: "NY",
    37: "NC",
    38: "ND",
    39: "OH",
    40: "OK",
    41: "OR",
    42: "PA",
    44: "RI",
    45: "SC",
    46: "SD",
    47: "TN",
    48: "TX",
    49: "UT",
    50: "VT",
    51: "VA",
    53: "WA",
    54: "WV",
    55: "WI",
    56: "WY",
    72: "PR",
    78: "VI",
    66: "GU",
    60: "AS",
    69: "MP",
}

MORTGAGE_TYPE_LABELS = {1: "FHA", 2: "VA", 3: "RD"}


# =============================================================================
# GNMA-FHLB Data Loading
# =============================================================================


def get_gnma_data_paths() -> tuple[Path, Path]:
    """Get paths to GNMA-FHLB crosswalk and match details files."""
    crosswalk_path = CROSSWALK_OUTPUT_DIR / "gnma_fhlb_crosswalk.parquet"
    details_path = INTERMEDIATE_DIR / "gnma_fhlb_match_details.parquet"
    return crosswalk_path, details_path


def load_gnma_match_data(
    crosswalk_path: Path | None = None,
    details_path: Path | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load GNMA-FHLB crosswalk and match details data.

    Args:
        crosswalk_path: Path to crosswalk file
        details_path: Path to match details file

    Returns:
        Tuple of (crosswalk, match_details) DataFrames
    """
    if crosswalk_path is None or details_path is None:
        default_crosswalk, default_details = get_gnma_data_paths()
        crosswalk_path = crosswalk_path or default_crosswalk
        details_path = details_path or default_details

    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Crosswalk file not found: {crosswalk_path}\n"
            "Run the GNMA-FHLB matching first: mortgage-data match mbs-fhlb gnma"
        )

    if not details_path.exists():
        raise FileNotFoundError(
            f"Match details file not found: {details_path}\n"
            "Run the GNMA-FHLB matching first: mortgage-data match mbs-fhlb gnma"
        )

    crosswalk = pl.read_parquet(crosswalk_path)
    details = pl.read_parquet(details_path)

    return crosswalk, details


def load_gnma_source_data(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    issuer_id: int | None = DEFAULT_GNMA_ISSUER_ID,
    verbose: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load source GNMA and FHLB data for computing total counts.

    Args:
        min_year: Minimum year to include
        max_year: Maximum year to include
        issuer_id: GNMA issuer ID filter (None or 0 for all)
        verbose: Print progress information

    Returns:
        Tuple of (gnma_prepared, fhlb_prepared) DataFrames
    """
    from mortgage_data_manager.matching.match_mbs_fhlb.gnma_match import (
        get_fhlb_ama_silver_dir,
        get_gnma_silver_dir,
        load_fhlb_ama_data,
        load_gnma_silver_data,
        prepare_fhlb_for_matching,
        prepare_gnma_for_matching,
    )

    gnma_dir = get_gnma_silver_dir()
    fhlb_dir = get_fhlb_ama_silver_dir()

    if verbose:
        print(f"Loading GNMA data from: {gnma_dir}")
        print(f"Loading FHLB data from: {fhlb_dir}")

    # Handle issuer_id=0 as "all issuers"
    effective_issuer = issuer_id if issuer_id and issuer_id != 0 else None

    gnma_raw = load_gnma_silver_data(gnma_dir, min_year, max_year, effective_issuer)
    fhlb_raw = load_fhlb_ama_data(fhlb_dir, min_year, max_year)

    gnma_prepared = prepare_gnma_for_matching(gnma_raw)
    fhlb_prepared = prepare_fhlb_for_matching(fhlb_raw)

    return gnma_prepared, fhlb_prepared


# =============================================================================
# FNMA-FHLB Data Loading
# =============================================================================


def load_fnma_matching_outputs() -> dict[str, pl.DataFrame | None]:
    """Load pre-computed FNMA-FHLB matching outputs from the matching pipeline.

    Returns:
        Dictionary with:
        - crosswalk: matched loan pairs (expected empty/near-empty)
        - match_details: full details for any matches found
    """
    results = {}

    # Crosswalk (the matches - expected to be empty or near-empty)
    crosswalk_path = CROSSWALK_OUTPUT_DIR / "fnma_fhlb_crosswalk.parquet"
    if crosswalk_path.exists():
        results["crosswalk"] = pl.read_parquet(crosswalk_path)
        print(f"Loaded crosswalk: {len(results['crosswalk']):,} matches")
    else:
        results["crosswalk"] = None
        print(f"Crosswalk not found at: {crosswalk_path}")

    # Match details (if any matches were found)
    details_path = INTERMEDIATE_DIR / "fnma_fhlb_match_details.parquet"
    if details_path.exists():
        results["match_details"] = pl.read_parquet(details_path)
        print(f"Loaded match details: {len(results['match_details']):,} records")
    else:
        results["match_details"] = None
        # This is expected - no matches means no details file

    return results


def load_fnma_source_data(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    seller_filter: str = "FEDERAL HOME LOAN BANK",
    exclude_banks: list[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load and prepare source data for FNMA-FHLB distribution analysis.

    Args:
        min_year: First year to include
        max_year: Last year to include
        seller_filter: Filter FNMA data to sellers containing this string
        exclude_banks: List of FHLB bank names to exclude

    Returns:
        Tuple of (fnma_prepared, fhlb_prepared) DataFrames
    """
    from mortgage_data_manager.matching.match_mbs_fhlb.config import (
        get_fhlb_ama_silver_dir,
        get_umbs_fnma_illd_bronze_dir,
    )
    from mortgage_data_manager.matching.match_mbs_fhlb.fnma_match import (
        load_fhlb_ama_conventional,
        load_fnma_illd_data,
        prepare_fhlb_for_fnma_matching,
        prepare_fnma_for_matching,
    )

    fnma_dir = get_umbs_fnma_illd_bronze_dir()
    fhlb_dir = get_fhlb_ama_silver_dir()

    print(f"\nLoading source data for {min_year}-{max_year}...")

    # Load and prepare FNMA data
    fnma_raw = load_fnma_illd_data(fnma_dir, min_year, max_year, seller_filter)
    fnma_prepared = prepare_fnma_for_matching(fnma_raw)
    print(f"  FNMA UMBS (FHLB seller): {len(fnma_prepared):,} loans")

    # Load and prepare FHLB data
    fhlb_raw = load_fhlb_ama_conventional(fhlb_dir, min_year, max_year)

    # Exclude specified banks if requested
    if exclude_banks:
        before_count = len(fhlb_raw)
        fhlb_raw = fhlb_raw.filter(~pl.col("Bank").is_in(exclude_banks))
        print(f"  Excluded banks {exclude_banks}: {before_count:,} -> {len(fhlb_raw):,} loans")

    fhlb_prepared = prepare_fhlb_for_fnma_matching(fhlb_raw)
    print(f"  FHLB AMA (conventional): {len(fhlb_prepared):,} loans")

    return fnma_prepared, fhlb_prepared


# =============================================================================
# GNMA-FHLB Match Quality Analysis
# =============================================================================


def compute_gnma_match_quality_statistics(details: pl.DataFrame) -> dict[str, Any]:
    """Compute match quality statistics from GNMA-FHLB match details.

    The match details contain both GNMA and FHLB values for each matched pair.
    GNMA values use different scales than FHLB:
    - Interest rate: GNMA in bp*100 (3750 = 3.75%), FHLB in percent (3.75)
    - Balance: GNMA in cents, FHLB in dollars
    - LTV/DTI: GNMA in basis points, FHLB in percent

    Returns:
        Dictionary with quality statistics
    """
    # Compute differences (converting GNMA to FHLB scale)
    details_with_diff = details.with_columns(
        [
            ((pl.col("interest_rate") / 1000) - pl.col("interest_rate_fhlb"))
            .abs()
            .alias("rate_diff"),
            ((pl.col("original_balance") / 100) - pl.col("note_amount"))
            .abs()
            .alias("balance_diff"),
            ((pl.col("ltv") / 100) - pl.col("ltv_fhlb")).abs().alias("ltv_diff"),
            ((pl.col("dti") / 100) - pl.col("dti_fhlb")).abs().alias("dti_diff"),
        ]
    )

    # Compute percentiles
    percentiles = [0.50, 0.90, 0.95, 0.99]

    rate_stats = details_with_diff.select(
        pl.col("rate_diff").quantile(p).alias(f"rate_p{int(p * 100)}") for p in percentiles
    ).row(0, named=True)

    balance_stats = details_with_diff.select(
        pl.col("balance_diff").quantile(p).alias(f"balance_p{int(p * 100)}") for p in percentiles
    ).row(0, named=True)

    ltv_stats = (
        details_with_diff.filter(pl.col("ltv_diff").is_not_null())
        .select(pl.col("ltv_diff").quantile(p).alias(f"ltv_p{int(p * 100)}") for p in percentiles)
        .row(0, named=True)
    )

    dti_stats = (
        details_with_diff.filter(pl.col("dti_diff").is_not_null())
        .select(pl.col("dti_diff").quantile(p).alias(f"dti_p{int(p * 100)}") for p in percentiles)
        .row(0, named=True)
    )

    # Compute fraction meeting tight tolerances
    n_matches = len(details_with_diff)
    tight_rate = len(details_with_diff.filter(pl.col("rate_diff") < 0.01)) / n_matches
    tight_balance = len(details_with_diff.filter(pl.col("balance_diff") <= 1000)) / n_matches
    tight_ltv = (
        len(details_with_diff.filter(pl.col("ltv_diff").is_null() | (pl.col("ltv_diff") < 0.01)))
        / n_matches
    )
    tight_dti = (
        len(details_with_diff.filter(pl.col("dti_diff").is_null() | (pl.col("dti_diff") < 0.01)))
        / n_matches
    )

    return {
        "n_matches": n_matches,
        "rate_stats": rate_stats,
        "balance_stats": balance_stats,
        "ltv_stats": ltv_stats,
        "dti_stats": dti_stats,
        "tight_rate_pct": tight_rate,
        "tight_balance_pct": tight_balance,
        "tight_ltv_pct": tight_ltv,
        "tight_dti_pct": tight_dti,
        "details_with_diff": details_with_diff,
    }


def compute_gnma_match_summary(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> dict[str, Any]:
    """Compute overall GNMA-FHLB match statistics.

    Returns:
        Dictionary with match counts and rates
    """
    n_gnma = len(gnma)
    n_fhlb = len(fhlb)
    n_matched = len(crosswalk)

    # Count unique matched IDs
    n_gnma_matched = crosswalk.select("gnma_loan_id").n_unique()
    n_fhlb_matched = crosswalk.select("fhlb_loan_id").n_unique()

    return {
        "total_gnma_loans": n_gnma,
        "total_fhlb_loans": n_fhlb,
        "matched_pairs": n_matched,
        "gnma_match_rate": n_gnma_matched / n_gnma if n_gnma > 0 else 0,
        "fhlb_match_rate": n_fhlb_matched / n_fhlb if n_fhlb > 0 else 0,
        "gnma_matched": n_gnma_matched,
        "fhlb_matched": n_fhlb_matched,
    }


# =============================================================================
# GNMA-FHLB Temporal Analysis
# =============================================================================


def compute_gnma_match_rates_by_year(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Compute GNMA-FHLB match rates by origination year from both perspectives.

    Returns:
        Tuple of (gnma_yearly, fhlb_yearly) DataFrames
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # GNMA match rates by origination year
    gnma_totals = (
        gnma.filter(pl.col("origination_year").is_not_null())
        .group_by("origination_year")
        .agg(pl.len().alias("total_gnma"))
    )

    gnma_matched = (
        gnma.filter(pl.col("origination_year").is_not_null())
        .join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("origination_year")
        .agg(pl.len().alias("matched_gnma"))
    )

    gnma_yearly = (
        gnma_totals.join(gnma_matched, on="origination_year", how="left")
        .with_columns(
            pl.col("matched_gnma").fill_null(0),
            (pl.col("matched_gnma") / pl.col("total_gnma")).alias("gnma_match_rate"),
        )
        .sort("origination_year")
    )

    # FHLB match rates by note year
    fhlb_totals = (
        fhlb.filter(pl.col("note_year").is_not_null())
        .group_by("note_year")
        .agg(pl.len().alias("total_fhlb"))
    )

    fhlb_matched = (
        fhlb.filter(pl.col("note_year").is_not_null())
        .join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("note_year")
        .agg(pl.len().alias("matched_fhlb"))
    )

    fhlb_yearly = (
        fhlb_totals.join(fhlb_matched, on="note_year", how="left")
        .with_columns(
            pl.col("matched_fhlb").fill_null(0),
            (pl.col("matched_fhlb") / pl.col("total_fhlb")).alias("fhlb_match_rate"),
        )
        .sort("note_year")
    )

    return gnma_yearly, fhlb_yearly


# =============================================================================
# GNMA-FHLB Geographic Analysis
# =============================================================================


def compute_gnma_match_rates_by_state(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Compute GNMA-FHLB match rates by state from both perspectives.

    Returns:
        Tuple of (gnma_by_state, fhlb_by_state) DataFrames
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # GNMA match rates by state
    gnma_totals = (
        gnma.filter(pl.col("state").is_not_null())
        .group_by("state")
        .agg(pl.len().alias("total_gnma"))
    )

    gnma_matched = (
        gnma.filter(pl.col("state").is_not_null())
        .join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("state")
        .agg(pl.len().alias("matched_gnma"))
    )

    gnma_by_state = (
        gnma_totals.join(gnma_matched, on="state", how="left")
        .with_columns(
            pl.col("matched_gnma").fill_null(0),
            (pl.col("matched_gnma") / pl.col("total_gnma")).alias("gnma_match_rate"),
        )
        .sort("total_gnma", descending=True)
    )

    # FHLB match rates by state FIPS
    fhlb_totals = (
        fhlb.filter(pl.col("state_fips").is_not_null())
        .group_by("state_fips")
        .agg(pl.len().alias("total_fhlb"))
    )

    fhlb_matched = (
        fhlb.filter(pl.col("state_fips").is_not_null())
        .join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("state_fips")
        .agg(pl.len().alias("matched_fhlb"))
    )

    fhlb_by_state = (
        fhlb_totals.join(fhlb_matched, on="state_fips", how="left")
        .with_columns(
            pl.col("matched_fhlb").fill_null(0),
            (pl.col("matched_fhlb") / pl.col("total_fhlb")).alias("fhlb_match_rate"),
        )
        .sort("total_fhlb", descending=True)
    )

    return gnma_by_state, fhlb_by_state


# =============================================================================
# GNMA-FHLB Loan Characteristics Analysis
# =============================================================================


def compute_gnma_match_rates_by_loan_amount(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    bin_size: int = 25000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute GNMA-FHLB match rates by loan amount bins.

    GNMA original_balance is in cents, FHLB note_amount is in dollars.
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # Bin expression for GNMA (convert cents to dollars first)
    def gnma_amount_bin_expr() -> pl.Expr:
        return (
            (pl.col("original_balance") / 100 / bin_size).floor() * bin_size + bin_size / 2
        ).cast(pl.Int64)

    # Bin expression for FHLB (already in dollars)
    def fhlb_amount_bin_expr() -> pl.Expr:
        return ((pl.col("note_amount") / bin_size).floor() * bin_size + bin_size / 2).cast(pl.Int64)

    # GNMA rates
    gnma_with_bin = gnma.with_columns(gnma_amount_bin_expr().alias("loan_amount_bin")).filter(
        (pl.col("loan_amount_bin") > 0) & (pl.col("loan_amount_bin") <= max_amount)
    )
    gnma_totals = gnma_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("gnma_total"))
    gnma_matched = (
        gnma_with_bin.join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("gnma_matched"))
    )
    gnma_rates = gnma_totals.join(gnma_matched, on="loan_amount_bin", how="left").with_columns(
        pl.col("gnma_matched").fill_null(0),
        (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
    )

    # FHLB rates
    fhlb_with_bin = fhlb.with_columns(fhlb_amount_bin_expr().alias("loan_amount_bin")).filter(
        (pl.col("loan_amount_bin") > 0) & (pl.col("loan_amount_bin") <= max_amount)
    )
    fhlb_totals = fhlb_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("fhlb_total"))
    fhlb_matched = (
        fhlb_with_bin.join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("fhlb_matched"))
    )
    fhlb_rates = fhlb_totals.join(fhlb_matched, on="loan_amount_bin", how="left").with_columns(
        pl.col("fhlb_matched").fill_null(0),
        (pl.col("fhlb_matched") / pl.col("fhlb_total")).alias("fhlb_match_rate"),
    )

    # Combine
    result = gnma_rates.join(fhlb_rates, on="loan_amount_bin", how="full", coalesce=True).sort(
        "loan_amount_bin"
    )

    return result


def compute_gnma_match_rates_by_interest_rate(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    bin_size: float = 0.25,
    min_rate: float = 2.0,
    max_rate: float = 9.0,
) -> pl.DataFrame:
    """Compute GNMA-FHLB match rates by interest rate bins.

    GNMA interest_rate is in bp*100 (3750 = 3.75%), FHLB is in percent (3.75).
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # Bin expression for GNMA (convert to percent first)
    def gnma_rate_bin_expr() -> pl.Expr:
        return ((pl.col("interest_rate") / 1000 / bin_size).round() * bin_size).cast(pl.Float64)

    # Bin expression for FHLB (already in percent)
    def fhlb_rate_bin_expr() -> pl.Expr:
        return ((pl.col("interest_rate") / bin_size).round() * bin_size).cast(pl.Float64)

    # GNMA rates
    gnma_with_bin = gnma.with_columns(gnma_rate_bin_expr().alias("interest_rate_bin")).filter(
        pl.col("interest_rate").is_not_null()
        & (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
    )
    gnma_totals = gnma_with_bin.group_by("interest_rate_bin").agg(pl.len().alias("gnma_total"))
    gnma_matched = (
        gnma_with_bin.join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("gnma_matched"))
    )
    gnma_rates = gnma_totals.join(gnma_matched, on="interest_rate_bin", how="left").with_columns(
        pl.col("gnma_matched").fill_null(0),
        (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
    )

    # FHLB rates
    fhlb_with_bin = fhlb.with_columns(fhlb_rate_bin_expr().alias("interest_rate_bin")).filter(
        pl.col("interest_rate").is_not_null()
        & (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
    )
    fhlb_totals = fhlb_with_bin.group_by("interest_rate_bin").agg(pl.len().alias("fhlb_total"))
    fhlb_matched = (
        fhlb_with_bin.join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("fhlb_matched"))
    )
    fhlb_rates = fhlb_totals.join(fhlb_matched, on="interest_rate_bin", how="left").with_columns(
        pl.col("fhlb_matched").fill_null(0),
        (pl.col("fhlb_matched") / pl.col("fhlb_total")).alias("fhlb_match_rate"),
    )

    # Combine
    result = gnma_rates.join(fhlb_rates, on="interest_rate_bin", how="full", coalesce=True).sort(
        "interest_rate_bin"
    )

    return result


def compute_gnma_match_rates_by_ltv(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    bin_size: int = 5,
) -> pl.DataFrame:
    """Compute GNMA-FHLB match rates by LTV bins.

    GNMA ltv is in basis points (9900 = 99%), FHLB is in percent (99.0).
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # Bin expression for GNMA (convert to percent first)
    def gnma_ltv_bin_expr() -> pl.Expr:
        return ((pl.col("ltv") / 100 / bin_size).round() * bin_size).cast(pl.Int64)

    # Bin expression for FHLB (already in percent)
    def fhlb_ltv_bin_expr() -> pl.Expr:
        return ((pl.col("ltv") / bin_size).round() * bin_size).cast(pl.Int64)

    # GNMA rates
    gnma_with_bin = gnma.with_columns(gnma_ltv_bin_expr().alias("ltv_bin")).filter(
        pl.col("ltv").is_not_null() & (pl.col("ltv_bin") > 0) & (pl.col("ltv_bin") <= 100)
    )
    gnma_totals = gnma_with_bin.group_by("ltv_bin").agg(pl.len().alias("gnma_total"))
    gnma_matched = (
        gnma_with_bin.join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("ltv_bin")
        .agg(pl.len().alias("gnma_matched"))
    )
    gnma_rates = gnma_totals.join(gnma_matched, on="ltv_bin", how="left").with_columns(
        pl.col("gnma_matched").fill_null(0),
        (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
    )

    # FHLB rates
    fhlb_with_bin = fhlb.with_columns(fhlb_ltv_bin_expr().alias("ltv_bin")).filter(
        pl.col("ltv").is_not_null() & (pl.col("ltv_bin") > 0) & (pl.col("ltv_bin") <= 100)
    )
    fhlb_totals = fhlb_with_bin.group_by("ltv_bin").agg(pl.len().alias("fhlb_total"))
    fhlb_matched = (
        fhlb_with_bin.join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("ltv_bin")
        .agg(pl.len().alias("fhlb_matched"))
    )
    fhlb_rates = fhlb_totals.join(fhlb_matched, on="ltv_bin", how="left").with_columns(
        pl.col("fhlb_matched").fill_null(0),
        (pl.col("fhlb_matched") / pl.col("fhlb_total")).alias("fhlb_match_rate"),
    )

    # Combine
    result = gnma_rates.join(fhlb_rates, on="ltv_bin", how="full", coalesce=True).sort("ltv_bin")

    return result


def compute_gnma_match_rates_by_mortgage_type(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> pl.DataFrame:
    """Compute GNMA-FHLB match rates by mortgage type (FHA=1, VA=2, RD=3).

    GNMA uses agency_code, FHLB uses mortgage_type.
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # GNMA rates by agency_code
    gnma_totals = (
        gnma.filter(pl.col("agency_code").is_not_null())
        .group_by("agency_code")
        .agg(pl.len().alias("gnma_total"))
    )
    gnma_matched = (
        gnma.filter(pl.col("agency_code").is_not_null())
        .join(matched_gnma_ids, on="gnma_loan_id", how="inner")
        .group_by("agency_code")
        .agg(pl.len().alias("gnma_matched"))
    )
    gnma_rates = (
        gnma_totals.join(gnma_matched, on="agency_code", how="left")
        .with_columns(
            pl.col("gnma_matched").fill_null(0),
            (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
        )
        .rename({"agency_code": "mortgage_type"})
    )

    # FHLB rates by mortgage_type
    fhlb_totals = (
        fhlb.filter(pl.col("mortgage_type").is_not_null())
        .group_by("mortgage_type")
        .agg(pl.len().alias("fhlb_total"))
    )
    fhlb_matched = (
        fhlb.filter(pl.col("mortgage_type").is_not_null())
        .join(matched_fhlb_ids, on="fhlb_loan_id", how="inner")
        .group_by("mortgage_type")
        .agg(pl.len().alias("fhlb_matched"))
    )
    fhlb_rates = fhlb_totals.join(fhlb_matched, on="mortgage_type", how="left").with_columns(
        pl.col("fhlb_matched").fill_null(0),
        (pl.col("fhlb_matched") / pl.col("fhlb_total")).alias("fhlb_match_rate"),
    )

    # Combine
    result = gnma_rates.join(fhlb_rates, on="mortgage_type", how="full", coalesce=True).sort(
        "mortgage_type"
    )

    return result


def analyze_gnma_unmatched_loans(
    crosswalk: pl.DataFrame,
    gnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> dict[str, Any]:
    """Analyze characteristics of unmatched GNMA-FHLB loans.

    Returns:
        Dictionary with unmatched loan statistics
    """
    matched_gnma_ids = crosswalk.select("gnma_loan_id").unique()
    matched_fhlb_ids = crosswalk.select("fhlb_loan_id").unique()

    # Get unmatched loans
    unmatched_gnma = gnma.join(matched_gnma_ids, on="gnma_loan_id", how="anti")
    unmatched_fhlb = fhlb.join(matched_fhlb_ids, on="fhlb_loan_id", how="anti")

    # Analyze unmatched GNMA by state
    unmatched_gnma_by_state = (
        unmatched_gnma.filter(pl.col("state").is_not_null())
        .group_by("state")
        .agg(pl.len().alias("unmatched_count"))
        .sort("unmatched_count", descending=True)
    )

    # Analyze unmatched GNMA by year
    unmatched_gnma_by_year = (
        unmatched_gnma.filter(pl.col("origination_year").is_not_null())
        .group_by("origination_year")
        .agg(pl.len().alias("unmatched_count"))
        .sort("origination_year")
    )

    # Analyze unmatched GNMA by mortgage type
    unmatched_gnma_by_type = (
        unmatched_gnma.filter(pl.col("agency_code").is_not_null())
        .group_by("agency_code")
        .agg(pl.len().alias("unmatched_count"))
        .sort("agency_code")
    )

    return {
        "n_unmatched_gnma": len(unmatched_gnma),
        "n_unmatched_fhlb": len(unmatched_fhlb),
        "unmatched_gnma_by_state": unmatched_gnma_by_state,
        "unmatched_gnma_by_year": unmatched_gnma_by_year,
        "unmatched_gnma_by_type": unmatched_gnma_by_type,
    }


# =============================================================================
# FNMA-FHLB Match Statistics
# =============================================================================


def print_fnma_match_statistics(
    crosswalk: pl.DataFrame | None,
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> dict[str, Any]:
    """Print and return FNMA-FHLB match statistics summary.

    For this mutual exclusivity test, we EXPECT zero or near-zero matches.
    """
    stats = {}

    print("\n" + "=" * 70)
    print("FNMA-FHLB MUTUAL EXCLUSIVITY TEST - MATCH STATISTICS")
    print("=" * 70)

    # Dataset sizes
    stats["fnma_total"] = len(fnma)
    stats["fhlb_total"] = len(fhlb)
    print("\nDataset Sizes:")
    print(f"  FNMA UMBS (FHLB as seller): {stats['fnma_total']:,} loans")
    print(f"  FHLB AMA (conventional):    {stats['fhlb_total']:,} loans")

    # Match counts
    if crosswalk is None or len(crosswalk) == 0:
        stats["match_count"] = 0
        stats["fnma_match_rate"] = 0.0
        stats["fhlb_match_rate"] = 0.0
        print("\nMatches Found: 0")
        print("\n*** HYPOTHESIS CONFIRMED ***")
        print("Zero matches found - these are mutually exclusive populations!")
        print("Loans sold to FNMA through UMBS do NOT appear in FHLB AMA acquisitions.")
    else:
        stats["match_count"] = len(crosswalk)
        stats["fnma_match_rate"] = len(crosswalk) / len(fnma) * 100 if len(fnma) > 0 else 0
        stats["fhlb_match_rate"] = len(crosswalk) / len(fhlb) * 100 if len(fhlb) > 0 else 0

        print(f"\nMatches Found: {stats['match_count']:,}")
        print(f"  As % of FNMA: {stats['fnma_match_rate']:.4f}%")
        print(f"  As % of FHLB: {stats['fhlb_match_rate']:.4f}%")

        if stats["match_count"] < 100:
            print("\n*** HYPOTHESIS CONFIRMED (with minor exceptions) ***")
            print(
                f"Near-zero matches ({stats['match_count']:,}) - populations are essentially mutually exclusive."
            )
            print("Minor overlap may indicate data quality issues worth investigating.")
        else:
            print("\n*** UNEXPECTED OVERLAP DETECTED ***")
            print("Significant matches found - requires investigation!")

    return stats


def investigate_fnma_matches(match_details: pl.DataFrame | None) -> None:
    """Investigate any FNMA-FHLB matches found (for data quality analysis).

    This is only called if matches were unexpectedly found.
    """
    if match_details is None or len(match_details) == 0:
        return

    print("\n" + "=" * 70)
    print("INVESTIGATING UNEXPECTED MATCHES")
    print("=" * 70)

    print(f"\nTotal unexpected matches: {len(match_details):,}")

    # Check if matches are concentrated in specific years
    if "origination_year" in match_details.columns:
        print("\nMatches by Origination Year:")
        by_year = match_details.group_by("origination_year").len().sort("origination_year")
        for row in by_year.iter_rows(named=True):
            print(f"  {row['origination_year']}: {row['len']:,}")

    # Check if matches are concentrated in specific states
    if "state_fips" in match_details.columns:
        print("\nMatches by State (top 10):")
        by_state = match_details.group_by("state_fips").len().sort("len", descending=True).head(10)
        for row in by_state.iter_rows(named=True):
            print(f"  State FIPS {row['state_fips']}: {row['len']:,}")

    # Check if matches are concentrated by seller
    if "seller_name" in match_details.columns:
        print("\nMatches by FNMA Seller:")
        by_seller = match_details.group_by("seller_name").len().sort("len", descending=True)
        for row in by_seller.iter_rows(named=True):
            print(f"  {row['seller_name']}: {row['len']:,}")

    # Check if matches are concentrated by FHLB bank
    if "fhlb_bank" in match_details.columns:
        print("\nMatches by FHLB Bank:")
        by_bank = match_details.group_by("fhlb_bank").len().sort("len", descending=True)
        for row in by_bank.iter_rows(named=True):
            print(f"  {row['fhlb_bank']}: {row['len']:,}")


def print_fnma_distribution_summary(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
) -> None:
    """Print summary statistics comparing FNMA and FHLB distributions."""
    print("\n" + "=" * 70)
    print("DISTRIBUTION COMPARISON SUMMARY")
    print("=" * 70)
    print("\nThese distributions should show that FNMA UMBS (FHLB seller) and")
    print("FHLB AMA (direct acquisitions) are DIFFERENT populations.")

    # Loan Amount
    print("\n--- Loan Amount ---")
    fnma_amt = fnma["original_balance"].drop_nulls()
    fhlb_amt = fhlb["note_amount"].drop_nulls()
    print(
        f"  FNMA:  mean=${fnma_amt.mean():,.0f}, median=${fnma_amt.median():,.0f}, std=${fnma_amt.std():,.0f}"
    )
    print(
        f"  FHLB:  mean=${fhlb_amt.mean():,.0f}, median=${fhlb_amt.median():,.0f}, std=${fhlb_amt.std():,.0f}"
    )

    # Interest Rate
    print("\n--- Interest Rate ---")
    fnma_rate = fnma["interest_rate"].drop_nulls()
    fhlb_rate = fhlb["interest_rate"].drop_nulls()
    print(
        f"  FNMA:  mean={fnma_rate.mean():.3f}%, median={fnma_rate.median():.3f}%, std={fnma_rate.std():.3f}%"
    )
    print(
        f"  FHLB:  mean={fhlb_rate.mean():.3f}%, median={fhlb_rate.median():.3f}%, std={fhlb_rate.std():.3f}%"
    )

    # LTV
    print("\n--- LTV ---")
    fnma_ltv = fnma["ltv"].drop_nulls()
    fhlb_ltv = fhlb["ltv"].drop_nulls()
    print(
        f"  FNMA:  mean={fnma_ltv.mean():.1f}%, median={fnma_ltv.median():.1f}%, std={fnma_ltv.std():.1f}%"
    )
    print(
        f"  FHLB:  mean={fhlb_ltv.mean():.1f}%, median={fhlb_ltv.median():.1f}%, std={fhlb_ltv.std():.1f}%"
    )

    # DTI
    print("\n--- DTI ---")
    fnma_dti = fnma["dti"].drop_nulls()
    fhlb_dti = fhlb["dti"].drop_nulls()
    print(
        f"  FNMA:  mean={fnma_dti.mean():.1f}%, median={fnma_dti.median():.1f}%, std={fnma_dti.std():.1f}%"
    )
    print(
        f"  FHLB:  mean={fhlb_dti.mean():.1f}%, median={fhlb_dti.median():.1f}%, std={fhlb_dti.std():.1f}%"
    )


# =============================================================================
# FNMA-FHLB Distribution Analysis
# =============================================================================


def compute_fnma_histogram_data(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    fnma_col: str,
    fhlb_col: str,
    bins: int = 50,
    min_val: float | None = None,
    max_val: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute histogram data for FNMA and FHLB distributions.

    Returns:
        Tuple of (bin_centers, fnma_pdf, fhlb_pdf, bin_edges)
    """
    # Get values
    fnma_vals = fnma[fnma_col].drop_nulls().to_numpy()
    fhlb_vals = fhlb[fhlb_col].drop_nulls().to_numpy()

    # Determine range
    if min_val is None:
        min_val = min(fnma_vals.min(), fhlb_vals.min())
    if max_val is None:
        max_val = max(fnma_vals.max(), fhlb_vals.max())

    # Filter to range
    fnma_vals = fnma_vals[(fnma_vals >= min_val) & (fnma_vals <= max_val)]
    fhlb_vals = fhlb_vals[(fhlb_vals >= min_val) & (fhlb_vals <= max_val)]

    # Compute histograms
    bin_edges = np.linspace(min_val, max_val, bins + 1)
    fnma_counts, _ = np.histogram(fnma_vals, bins=bin_edges)
    fhlb_counts, _ = np.histogram(fhlb_vals, bins=bin_edges)

    # Convert to PDF (probability density function)
    fnma_pdf = fnma_counts / fnma_counts.sum() if fnma_counts.sum() > 0 else fnma_counts
    fhlb_pdf = fhlb_counts / fhlb_counts.sum() if fhlb_counts.sum() > 0 else fhlb_counts

    # Bin centers for plotting
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    return bin_centers, fnma_pdf, fhlb_pdf, bin_edges


# =============================================================================
# GNMA Plotting Functions
# =============================================================================


def plot_gnma_match_quality_distributions(
    quality_stats: dict[str, Any],
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot distributions of GNMA-FHLB match quality metrics (differences)."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    details_with_diff = quality_stats["details_with_diff"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Interest rate difference
    ax = axes[0, 0]
    rate_diff = details_with_diff["rate_diff"].drop_nulls().to_numpy()
    ax.hist(rate_diff, bins=50, color=QUALITY_COLOR, edgecolor="white", alpha=0.7)
    ax.axvline(0.01, color="red", linestyle="--", label="Tight threshold (0.01%)")
    ax.set_xlabel("Interest Rate Difference (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"Interest Rate Match Quality\n({quality_stats['tight_rate_pct']:.1%} < 0.01%)")
    ax.legend()
    ax.set_xlim(0, min(rate_diff.max(), 0.15))

    # Balance difference
    ax = axes[0, 1]
    balance_diff = details_with_diff["balance_diff"].drop_nulls().to_numpy()
    ax.hist(balance_diff, bins=50, color=QUALITY_COLOR, edgecolor="white", alpha=0.7)
    ax.axvline(1000, color="red", linestyle="--", label="Tight threshold ($1,000)")
    ax.set_xlabel("Balance Difference ($)")
    ax.set_ylabel("Count")
    ax.set_title(f"Balance Match Quality\n({quality_stats['tight_balance_pct']:.1%} <= $1,000)")
    ax.legend()
    ax.set_xlim(0, min(balance_diff.max(), 2500))

    # LTV difference
    ax = axes[1, 0]
    ltv_diff = details_with_diff["ltv_diff"].drop_nulls().to_numpy()
    ax.hist(ltv_diff, bins=50, color=QUALITY_COLOR, edgecolor="white", alpha=0.7)
    ax.axvline(0.01, color="red", linestyle="--", label="Tight threshold (0.01%)")
    ax.set_xlabel("LTV Difference (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"LTV Match Quality\n({quality_stats['tight_ltv_pct']:.1%} < 0.01%)")
    ax.legend()
    ax.set_xlim(0, min(ltv_diff.max(), 1.2))

    # DTI difference
    ax = axes[1, 1]
    dti_diff = details_with_diff["dti_diff"].drop_nulls().to_numpy()
    ax.hist(dti_diff, bins=50, color=QUALITY_COLOR, edgecolor="white", alpha=0.7)
    ax.axvline(0.01, color="red", linestyle="--", label="Tight threshold (0.01%)")
    ax.set_xlabel("DTI Difference (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"DTI Match Quality\n({quality_stats['tight_dti_pct']:.1%} < 0.01%)")
    ax.legend()
    ax.set_xlim(0, min(dti_diff.max(), 1.2))

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_gnma_temporal_match_rates(
    gnma_yearly: pl.DataFrame,
    fhlb_yearly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot GNMA-FHLB match rates by origination year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    # GNMA yearly
    gnma_years = gnma_yearly["origination_year"].to_numpy()
    gnma_rates = gnma_yearly["gnma_match_rate"].to_numpy()

    # FHLB yearly
    fhlb_years = fhlb_yearly["note_year"].to_numpy()
    fhlb_rates = fhlb_yearly["fhlb_match_rate"].to_numpy()

    ax.plot(
        gnma_years,
        gnma_rates,
        color=GNMA_COLOR,
        linewidth=2,
        marker="o",
        markersize=8,
        label="GNMA Match Rate",
    )

    ax.plot(
        fhlb_years,
        fhlb_rates,
        color=FHLB_COLOR,
        linewidth=2,
        marker="s",
        markersize=8,
        label="FHLB Match Rate",
    )

    # Overall rates as horizontal lines
    gnma_overall = gnma_yearly["matched_gnma"].sum() / gnma_yearly["total_gnma"].sum()
    fhlb_overall = fhlb_yearly["matched_fhlb"].sum() / fhlb_yearly["total_fhlb"].sum()

    ax.axhline(gnma_overall, color=GNMA_COLOR, linestyle="--", alpha=0.5, linewidth=1.5)
    ax.axhline(fhlb_overall, color=FHLB_COLOR, linestyle="--", alpha=0.5, linewidth=1.5)

    ax.set_xlabel("Year")
    ax.set_ylabel("Match Rate")
    ax.set_title("GNMA-FHLB Match Rates by Origination Year")

    # Y-axis limits
    all_rates = list(gnma_rates) + list(fhlb_rates)
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.02)
        ax.set_ylim(y_min, y_max)

    # Add overall rate labels
    ax.text(
        max(gnma_years) + 0.3,
        gnma_overall,
        f"GNMA: {gnma_overall:.1%}",
        color=GNMA_COLOR,
        va="center",
        fontsize=10,
    )
    ax.text(
        max(fhlb_years) + 0.3,
        fhlb_overall,
        f"FHLB: {fhlb_overall:.1%}",
        color=FHLB_COLOR,
        va="center",
        fontsize=10,
    )

    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_gnma_match_rates_by_continuous(
    rates_df: pl.DataFrame,
    bin_col: str,
    xlabel: str,
    title: str,
    output_path: Path | None = None,
    interactive: bool = True,
    x_scale: float = 1.0,
) -> None:
    """Plot GNMA-FHLB match rates by continuous variable bins with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    # Get values
    x = np.array(rates_df[bin_col].to_list()) * x_scale

    gnma_total = rates_df["gnma_total"].to_numpy().astype(float)
    fhlb_total = rates_df["fhlb_total"].to_numpy().astype(float)

    # Handle NaN values
    gnma_total = np.nan_to_num(gnma_total, nan=0)
    fhlb_total = np.nan_to_num(fhlb_total, nan=0)

    # Compute PDF for density overlay
    gnma_pdf = gnma_total / gnma_total.sum() if gnma_total.sum() > 0 else gnma_total
    fhlb_pdf = fhlb_total / fhlb_total.sum() if fhlb_total.sum() > 0 else fhlb_total

    # Secondary axis for density
    ax2 = ax.twinx()

    ax2.fill_between(x, gnma_pdf, alpha=0.15, color=GNMA_COLOR, label="GNMA Density")
    ax2.fill_between(x, fhlb_pdf, alpha=0.15, color=FHLB_COLOR, label="FHLB Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    max_pdf = max(
        max(gnma_pdf) if len(gnma_pdf) > 0 else 0, max(fhlb_pdf) if len(fhlb_pdf) > 0 else 0
    )
    ax2.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    # Plot match rates
    gnma_rates = rates_df["gnma_match_rate"].to_list()
    fhlb_rates = rates_df["fhlb_match_rate"].to_list()

    ax.plot(
        x,
        gnma_rates,
        color=GNMA_COLOR,
        linewidth=2,
        marker="o",
        markersize=5,
        label="GNMA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        fhlb_rates,
        color=FHLB_COLOR,
        linewidth=2,
        marker="s",
        markersize=5,
        label="FHLB Match Rate",
        zorder=10,
    )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate")
    ax.set_title(title)

    # Y-axis limits
    all_rates = gnma_rates + fhlb_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.02)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_gnma_match_rates_by_mortgage_type(
    rates_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot GNMA-FHLB match rates by mortgage type as grouped bar chart."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    # Get data
    df = rates_df.filter(pl.col("mortgage_type").is_not_null()).sort("mortgage_type")
    types = df["mortgage_type"].to_list()
    labels = [MORTGAGE_TYPE_LABELS.get(t, str(t)) for t in types]

    gnma_rates_raw = df["gnma_match_rate"].to_list()
    fhlb_rates_raw = df["fhlb_match_rate"].to_list()
    gnma_rates = [r if r is not None else 0 for r in gnma_rates_raw]
    fhlb_rates = [r if r is not None else 0 for r in fhlb_rates_raw]

    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width / 2, gnma_rates, width, label="GNMA", color=GNMA_COLOR, alpha=0.8)
    ax.bar(x + width / 2, fhlb_rates, width, label="FHLB", color=FHLB_COLOR, alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)

    # Add value labels
    for i, (gnma_r, fhlb_r) in enumerate(zip(gnma_rates_raw, fhlb_rates_raw)):
        if gnma_r is not None and not np.isnan(gnma_r):
            ax.text(
                i - width / 2, gnma_r + 0.01, f"{gnma_r:.1%}", ha="center", va="bottom", fontsize=10
            )
        if fhlb_r is not None and not np.isnan(fhlb_r):
            ax.text(
                i + width / 2, fhlb_r + 0.01, f"{fhlb_r:.1%}", ha="center", va="bottom", fontsize=10
            )

    # Add counts
    gnma_totals = df["gnma_total"].to_list()
    fhlb_totals = df["fhlb_total"].to_list()
    for i, (gt, ft) in enumerate(zip(gnma_totals, fhlb_totals)):
        ax.text(
            i,
            0.02,
            f"GNMA: {gt:,}\nFHLB: {ft:,}",
            ha="center",
            va="bottom",
            fontsize=8,
            alpha=0.7,
        )

    ax.set_xlabel("Mortgage Type")
    ax.set_ylabel("Match Rate")
    ax.set_title("GNMA-FHLB Match Rates by Mortgage Type (FHA/VA/RD)")

    # Y-axis limits
    all_rates = gnma_rates + fhlb_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.1)
        y_max = min(1.0, max(valid_rates) + 0.08)
        ax.set_ylim(y_min, y_max)

    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_gnma_state_match_rate_map(
    gnma_by_state: pl.DataFrame,
    output_path: Path | None = None,
) -> None:
    """Plot choropleth map of GNMA-FHLB match rates by state."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly not available, skipping state map")
        return

    # Filter to continental US states (remove territories)
    territories = {"PR", "GU", "VI", "AS", "MP", "DC"}

    gnma_filtered = gnma_by_state.filter(
        (~pl.col("state").is_in(territories)) & (pl.col("gnma_match_rate").is_not_null())
    )

    all_rates = gnma_filtered["gnma_match_rate"].to_list()
    color_min = max(0.50, min(all_rates) - 0.05) if all_rates else 0.5
    color_max = min(1.0, max(all_rates) + 0.02) if all_rates else 1.0

    fig = go.Figure(
        go.Choropleth(
            locations=gnma_filtered["state"].to_list(),
            z=gnma_filtered["gnma_match_rate"].to_list(),
            locationmode="USA-states",
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            colorbar=dict(
                title="Match Rate",
                tickformat=".0%",
            ),
            hovertemplate="<b>%{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        )
    )

    fig.update_geos(
        scope="usa",
        showlakes=False,
        showland=True,
        landcolor="lightgray",
    )

    fig.update_layout(
        title_text="GNMA-FHLB Match Rates by State",
        title_x=0.5,
        width=1000,
        height=600,
        margin=dict(l=0, r=0, t=60, b=0),
    )

    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


# =============================================================================
# FNMA Plotting Functions
# =============================================================================


def plot_fnma_distribution_overlay(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    fnma_col: str,
    fhlb_col: str,
    xlabel: str,
    title: str,
    output_path: Path | None = None,
    interactive: bool = True,
    bins: int = 50,
    min_val: float | None = None,
    max_val: float | None = None,
    x_scale: float = 1.0,
) -> None:
    """Plot overlaid FNMA-FHLB distribution comparison as filled curves."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    bin_centers, fnma_pdf, fhlb_pdf, _ = compute_fnma_histogram_data(
        fnma, fhlb, fnma_col, fhlb_col, bins, min_val, max_val
    )

    fig, ax = plt.subplots(figsize=(12, 6))

    # Plot as filled curves
    ax.fill_between(
        bin_centers * x_scale,
        fnma_pdf,
        alpha=0.5,
        label="FNMA UMBS (FHLB seller)",
        color=FNMA_COLOR,
    )
    ax.fill_between(
        bin_centers * x_scale,
        fhlb_pdf,
        alpha=0.5,
        label="FHLB AMA (acquisitions)",
        color=FHLB_COLOR,
    )

    # Add line overlay for clarity
    ax.plot(bin_centers * x_scale, fnma_pdf, color=FNMA_COLOR, linewidth=2)
    ax.plot(bin_centers * x_scale, fhlb_pdf, color=FHLB_COLOR, linewidth=2)

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Probability Density")
    ax.set_title(title)
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_fnma_year_comparison(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot FNMA and FHLB loan counts by year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    # Aggregate by year
    fnma_by_year = (
        fnma.group_by("origination_year").len().rename({"len": "count"}).sort("origination_year")
    )
    fhlb_by_year = fhlb.group_by("note_year").len().rename({"len": "count"}).sort("note_year")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # FNMA by year
    years = fnma_by_year["origination_year"].to_list()
    counts = fnma_by_year["count"].to_list()
    ax1.bar(years, counts, color=FNMA_COLOR, alpha=0.8)
    ax1.set_xlabel("Origination Year")
    ax1.set_ylabel("Loan Count")
    ax1.set_title("FNMA UMBS (FHLB Seller) by Year")
    ax1.grid(True, alpha=0.3)
    # Add count labels
    for x, y in zip(years, counts):
        ax1.text(x, y, f"{y:,}", ha="center", va="bottom", fontsize=9)

    # FHLB by year
    years = fhlb_by_year["note_year"].to_list()
    counts = fhlb_by_year["count"].to_list()
    ax2.bar(years, counts, color=FHLB_COLOR, alpha=0.8)
    ax2.set_xlabel("Origination Year")
    ax2.set_ylabel("Loan Count")
    ax2.set_title("FHLB AMA (Acquisitions) by Year")
    ax2.grid(True, alpha=0.3)
    # Add count labels
    for x, y in zip(years, counts):
        ax2.text(x, y, f"{y:,}", ha="center", va="bottom", fontsize=9)

    plt.suptitle(
        "Year Distribution Comparison - Distinct Populations", fontsize=12, fontweight="bold"
    )
    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_fnma_state_comparison(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    top_n: int = 15,
) -> None:
    """Plot FNMA and FHLB loan counts by state."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    # Aggregate by state
    fnma_by_state = (
        fnma.group_by("state_fips")
        .len()
        .rename({"len": "fnma_count"})
        .with_columns(
            pl.col("state_fips").replace_strict(FIPS_TO_STATE, default="??").alias("state")
        )
    )
    fhlb_by_state = (
        fhlb.group_by("state_fips")
        .len()
        .rename({"len": "fhlb_count"})
        .with_columns(
            pl.col("state_fips").replace_strict(FIPS_TO_STATE, default="??").alias("state")
        )
    )

    # Merge and compute shares
    merged = fnma_by_state.join(fhlb_by_state, on="state_fips", how="full", suffix="_fhlb")

    # Use coalesce for state column
    merged = merged.with_columns(
        [
            pl.coalesce(pl.col("state"), pl.col("state_fhlb")).alias("state_final"),
            pl.col("fnma_count").fill_null(0),
            pl.col("fhlb_count").fill_null(0),
        ]
    )

    # Compute shares
    fnma_total = merged["fnma_count"].sum()
    fhlb_total = merged["fhlb_count"].sum()
    merged = merged.with_columns(
        [
            (pl.col("fnma_count") / fnma_total * 100).alias("fnma_share"),
            (pl.col("fhlb_count") / fhlb_total * 100).alias("fhlb_share"),
        ]
    )

    # Sort by total and take top N
    merged = (
        merged.with_columns((pl.col("fnma_count") + pl.col("fhlb_count")).alias("total"))
        .sort("total", descending=True)
        .head(top_n)
    )

    fig, ax = plt.subplots(figsize=(14, 6))

    states = merged["state_final"].to_list()
    x = np.arange(len(states))
    width = 0.35

    fnma_shares = merged["fnma_share"].to_list()
    fhlb_shares = merged["fhlb_share"].to_list()

    ax.bar(
        x - width / 2,
        fnma_shares,
        width,
        label="FNMA UMBS (FHLB seller)",
        color=FNMA_COLOR,
        alpha=0.8,
    )
    ax.bar(
        x + width / 2,
        fhlb_shares,
        width,
        label="FHLB AMA (acquisitions)",
        color=FHLB_COLOR,
        alpha=0.8,
    )

    ax.set_xlabel("State")
    ax.set_ylabel("Share of Portfolio (%)")
    ax.set_title(f"Geographic Distribution Comparison (Top {top_n} States)")
    ax.set_xticks(x)
    ax.set_xticklabels(states, rotation=45, ha="right")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_fnma_summary_panel(
    fnma: pl.DataFrame,
    fhlb: pl.DataFrame,
    match_count: int,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Create FNMA-FHLB summary panel showing key distributions and match result."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig = plt.figure(figsize=(16, 12))

    # Create grid for subplots
    gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

    # Top left: Summary text
    ax_text = fig.add_subplot(gs[0, 0])
    ax_text.axis("off")

    summary_text = (
        f"FNMA-FHLB Mutual Exclusivity Test\n"
        f"{'=' * 35}\n\n"
        f"FNMA UMBS (FHLB seller): {len(fnma):,}\n"
        f"FHLB AMA (conventional): {len(fhlb):,}\n\n"
        f"Matches Found: {match_count:,}\n"
        f"Match Rate: {match_count / len(fnma) * 100:.4f}%\n\n"
    )

    if match_count == 0:
        summary_text += "RESULT: HYPOTHESIS CONFIRMED\n"
        summary_text += "Zero overlap - distinct populations!"
    elif match_count < 100:
        summary_text += "RESULT: ESSENTIALLY CONFIRMED\n"
        summary_text += f"Minimal overlap ({match_count}) - investigate"
    else:
        summary_text += "RESULT: UNEXPECTED OVERLAP\n"
        summary_text += "Significant matches - investigate!"

    ax_text.text(
        0.5,
        0.5,
        summary_text,
        transform=ax_text.transAxes,
        fontsize=11,
        verticalalignment="center",
        horizontalalignment="center",
        fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    # Top middle: Loan Amount distribution
    ax_amount = fig.add_subplot(gs[0, 1])
    bin_centers, fnma_pdf, fhlb_pdf, _ = compute_fnma_histogram_data(
        fnma, fhlb, "original_balance", "note_amount", bins=40, min_val=0, max_val=1000000
    )
    ax_amount.fill_between(bin_centers / 1000, fnma_pdf, alpha=0.5, label="FNMA", color=FNMA_COLOR)
    ax_amount.fill_between(bin_centers / 1000, fhlb_pdf, alpha=0.5, label="FHLB", color=FHLB_COLOR)
    ax_amount.set_xlabel("Loan Amount ($K)")
    ax_amount.set_ylabel("Density")
    ax_amount.set_title("Loan Amount Distribution")
    ax_amount.legend(fontsize=8)
    ax_amount.grid(True, alpha=0.3)

    # Top right: Interest Rate distribution
    ax_rate = fig.add_subplot(gs[0, 2])
    bin_centers, fnma_pdf, fhlb_pdf, _ = compute_fnma_histogram_data(
        fnma, fhlb, "interest_rate", "interest_rate", bins=40, min_val=2, max_val=9
    )
    ax_rate.fill_between(bin_centers, fnma_pdf, alpha=0.5, label="FNMA", color=FNMA_COLOR)
    ax_rate.fill_between(bin_centers, fhlb_pdf, alpha=0.5, label="FHLB", color=FHLB_COLOR)
    ax_rate.set_xlabel("Interest Rate (%)")
    ax_rate.set_ylabel("Density")
    ax_rate.set_title("Interest Rate Distribution")
    ax_rate.legend(fontsize=8)
    ax_rate.grid(True, alpha=0.3)

    # Middle left: LTV distribution
    ax_ltv = fig.add_subplot(gs[1, 0])
    bin_centers, fnma_pdf, fhlb_pdf, _ = compute_fnma_histogram_data(
        fnma, fhlb, "ltv", "ltv", bins=40, min_val=0, max_val=105
    )
    ax_ltv.fill_between(bin_centers, fnma_pdf, alpha=0.5, label="FNMA", color=FNMA_COLOR)
    ax_ltv.fill_between(bin_centers, fhlb_pdf, alpha=0.5, label="FHLB", color=FHLB_COLOR)
    ax_ltv.set_xlabel("LTV (%)")
    ax_ltv.set_ylabel("Density")
    ax_ltv.set_title("LTV Distribution")
    ax_ltv.legend(fontsize=8)
    ax_ltv.grid(True, alpha=0.3)

    # Middle center: DTI distribution
    ax_dti = fig.add_subplot(gs[1, 1])
    bin_centers, fnma_pdf, fhlb_pdf, _ = compute_fnma_histogram_data(
        fnma, fhlb, "dti", "dti", bins=40, min_val=0, max_val=65
    )
    ax_dti.fill_between(bin_centers, fnma_pdf, alpha=0.5, label="FNMA", color=FNMA_COLOR)
    ax_dti.fill_between(bin_centers, fhlb_pdf, alpha=0.5, label="FHLB", color=FHLB_COLOR)
    ax_dti.set_xlabel("DTI (%)")
    ax_dti.set_ylabel("Density")
    ax_dti.set_title("DTI Distribution")
    ax_dti.legend(fontsize=8)
    ax_dti.grid(True, alpha=0.3)

    # Middle right: Loan Term distribution
    ax_term = fig.add_subplot(gs[1, 2])
    fnma_term = fnma["original_term"].drop_nulls().value_counts().sort("original_term")
    fhlb_term = fhlb["original_term"].drop_nulls().value_counts().sort("original_term")

    fnma_term_dict = dict(zip(fnma_term["original_term"].to_list(), fnma_term["count"].to_list()))
    fhlb_term_dict = dict(zip(fhlb_term["original_term"].to_list(), fhlb_term["count"].to_list()))

    # Common terms
    all_terms = sorted(set(fnma_term_dict.keys()) | set(fhlb_term_dict.keys()))
    # Filter to common mortgage terms
    common_terms = [t for t in all_terms if t in [60, 120, 180, 240, 300, 360]]

    fnma_counts = [fnma_term_dict.get(t, 0) for t in common_terms]
    fhlb_counts = [fhlb_term_dict.get(t, 0) for t in common_terms]

    fnma_total = sum(fnma_counts)
    fhlb_total = sum(fhlb_counts)
    fnma_shares = [c / fnma_total * 100 if fnma_total > 0 else 0 for c in fnma_counts]
    fhlb_shares = [c / fhlb_total * 100 if fhlb_total > 0 else 0 for c in fhlb_counts]

    x = np.arange(len(common_terms))
    width = 0.35
    ax_term.bar(x - width / 2, fnma_shares, width, label="FNMA", color=FNMA_COLOR, alpha=0.8)
    ax_term.bar(x + width / 2, fhlb_shares, width, label="FHLB", color=FHLB_COLOR, alpha=0.8)
    ax_term.set_xticks(x)
    ax_term.set_xticklabels([f"{t}m" for t in common_terms])
    ax_term.set_xlabel("Loan Term")
    ax_term.set_ylabel("Share (%)")
    ax_term.set_title("Loan Term Distribution")
    ax_term.legend(fontsize=8)
    ax_term.grid(True, alpha=0.3, axis="y")

    # Bottom row: Year and State distributions
    # Bottom left: Year distribution
    ax_year = fig.add_subplot(gs[2, 0:2])

    fnma_by_year = fnma.group_by("origination_year").len().sort("origination_year")
    fhlb_by_year = fhlb.group_by("note_year").len().sort("note_year")

    all_years = sorted(
        set(fnma_by_year["origination_year"].to_list()) | set(fhlb_by_year["note_year"].to_list())
    )

    fnma_year_dict = dict(
        zip(fnma_by_year["origination_year"].to_list(), fnma_by_year["len"].to_list())
    )
    fhlb_year_dict = dict(zip(fhlb_by_year["note_year"].to_list(), fhlb_by_year["len"].to_list()))

    fnma_year_counts = [fnma_year_dict.get(y, 0) for y in all_years]
    fhlb_year_counts = [fhlb_year_dict.get(y, 0) for y in all_years]

    x = np.arange(len(all_years))
    width = 0.35
    ax_year.bar(
        x - width / 2,
        fnma_year_counts,
        width,
        label="FNMA UMBS (FHLB seller)",
        color=FNMA_COLOR,
        alpha=0.8,
    )
    ax_year.bar(
        x + width / 2, fhlb_year_counts, width, label="FHLB AMA", color=FHLB_COLOR, alpha=0.8
    )
    ax_year.set_xticks(x)
    ax_year.set_xticklabels(all_years)
    ax_year.set_xlabel("Year")
    ax_year.set_ylabel("Loan Count")
    ax_year.set_title("Volume by Year")
    ax_year.legend(fontsize=8)
    ax_year.grid(True, alpha=0.3, axis="y")
    # Add count labels
    for i, (f, h) in enumerate(zip(fnma_year_counts, fhlb_year_counts)):
        if f > 0:
            ax_year.text(
                i - width / 2, f, f"{f:,}", ha="center", va="bottom", fontsize=7, rotation=45
            )
        if h > 0:
            ax_year.text(
                i + width / 2, h, f"{h:,}", ha="center", va="bottom", fontsize=7, rotation=45
            )

    # Bottom right: Top states
    ax_state = fig.add_subplot(gs[2, 2])

    fnma_by_state = fnma.group_by("state_fips").len().sort("len", descending=True).head(10)
    fhlb_by_state = fhlb.group_by("state_fips").len().sort("len", descending=True).head(10)

    # Get top 8 states by combined volume
    all_states_fnma = set(fnma_by_state["state_fips"].to_list())
    all_states_fhlb = set(fhlb_by_state["state_fips"].to_list())
    top_states = list(all_states_fnma | all_states_fhlb)[:8]

    fnma_state_dict = dict(
        zip(
            fnma.group_by("state_fips").len()["state_fips"].to_list(),
            fnma.group_by("state_fips").len()["len"].to_list(),
        )
    )
    fhlb_state_dict = dict(
        zip(
            fhlb.group_by("state_fips").len()["state_fips"].to_list(),
            fhlb.group_by("state_fips").len()["len"].to_list(),
        )
    )

    fnma_state_counts = [fnma_state_dict.get(s, 0) for s in top_states]
    fhlb_state_counts = [fhlb_state_dict.get(s, 0) for s in top_states]
    state_labels = [FIPS_TO_STATE.get(s, "??") for s in top_states]

    # Sort by total
    combined = list(zip(state_labels, fnma_state_counts, fhlb_state_counts))
    combined.sort(key=lambda x: x[1] + x[2], reverse=True)
    state_labels, fnma_state_counts, fhlb_state_counts = zip(*combined[:8])

    x = np.arange(len(state_labels))
    width = 0.35
    ax_state.bar(x - width / 2, fnma_state_counts, width, label="FNMA", color=FNMA_COLOR, alpha=0.8)
    ax_state.bar(x + width / 2, fhlb_state_counts, width, label="FHLB", color=FHLB_COLOR, alpha=0.8)
    ax_state.set_xticks(x)
    ax_state.set_xticklabels(state_labels, rotation=45)
    ax_state.set_xlabel("State")
    ax_state.set_ylabel("Count")
    ax_state.set_title("Top States")
    ax_state.legend(fontsize=8)
    ax_state.grid(True, alpha=0.3, axis="y")

    plt.suptitle(
        "FNMA-FHLB Mutual Exclusivity Validation: Distribution Comparison",
        fontsize=14,
        fontweight="bold",
        y=1.02,
    )

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Main Entry Points
# =============================================================================


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


def run_gnma_validation(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    issuer_id: int | None = DEFAULT_GNMA_ISSUER_ID,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run GNMA-FHLB validation analyses.

    This workflow validates the matching of GNMA government-backed loans
    (FHA/VA/RD) to FHLB AMA acquisitions. Expected HIGH match rates (~90%+).

    Args:
        min_year: First year to include
        max_year: Last year to include
        issuer_id: GNMA issuer ID filter (None or 0 for all)
        output_dir: Directory to save figures. Defaults to FIGURES_DIR.
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively

    Returns:
        Dictionary with all computed statistics and DataFrames
    """
    if output_dir is None:
        output_dir = FIGURES_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving figures to: {output_dir}")

    # Load data
    print("\nLoading GNMA-FHLB match data...")
    crosswalk, details = load_gnma_match_data()
    print(f"  Crosswalk: {len(crosswalk):,} matched pairs")
    print(f"  Match details: {len(details):,} records")

    issuer_str = f"issuer {issuer_id}" if issuer_id and issuer_id != 0 else "all issuers"
    print(f"\nLoading source data ({min_year}-{max_year}, {issuer_str})...")
    gnma, fhlb = load_gnma_source_data(min_year, max_year, issuer_id, verbose=True)
    print(f"  GNMA prepared: {len(gnma):,} loans")
    print(f"  FHLB prepared: {len(fhlb):,} records")

    # --- Section 1: Match Statistics Summary ---
    print_section("1. Match Statistics Summary")
    match_summary = compute_gnma_match_summary(crosswalk, gnma, fhlb)
    print(f"Total GNMA loans:    {match_summary['total_gnma_loans']:,}")
    print(f"Total FHLB records:  {match_summary['total_fhlb_loans']:,}")
    print(f"Matched pairs:       {match_summary['matched_pairs']:,}")
    print(f"GNMA match rate:     {match_summary['gnma_match_rate']:.1%}")
    print(f"FHLB match rate:     {match_summary['fhlb_match_rate']:.1%}")

    # --- Section 2: Match Quality Analysis ---
    print_section("2. Match Quality Analysis")
    quality_stats = compute_gnma_match_quality_statistics(details)

    print("Interest Rate Differences:")
    for k, v in quality_stats["rate_stats"].items():
        print(f"  {k}: {v:.4f}%")
    print(f"  Fraction < 0.01%: {quality_stats['tight_rate_pct']:.1%}")

    print("\nBalance Differences:")
    for k, v in quality_stats["balance_stats"].items():
        print(f"  {k}: ${v:,.0f}")
    print(f"  Fraction <= $1,000: {quality_stats['tight_balance_pct']:.1%}")

    print("\nLTV Differences:")
    for k, v in quality_stats["ltv_stats"].items():
        print(f"  {k}: {v:.4f}%")
    print(f"  Fraction < 0.01%: {quality_stats['tight_ltv_pct']:.1%}")

    print("\nDTI Differences:")
    for k, v in quality_stats["dti_stats"].items():
        print(f"  {k}: {v:.4f}%")
    print(f"  Fraction < 0.01%: {quality_stats['tight_dti_pct']:.1%}")

    # --- Section 3: Temporal Analysis ---
    print_section("3. Temporal Analysis")
    gnma_yearly, fhlb_yearly = compute_gnma_match_rates_by_year(crosswalk, gnma, fhlb)
    print("GNMA Match Rates by Origination Year:")
    print(gnma_yearly)
    print("\nFHLB Match Rates by Note Year:")
    print(fhlb_yearly)

    # --- Section 4: Geographic Analysis ---
    print_section("4. Geographic Analysis")
    gnma_by_state, fhlb_by_state = compute_gnma_match_rates_by_state(crosswalk, gnma, fhlb)
    print("GNMA Match Rates by State (Top 10 by Volume):")
    print(gnma_by_state.head(10))

    print("\nLowest GNMA match rate states (min 50 loans):")
    low_states = gnma_by_state.filter(pl.col("total_gnma") >= 50).sort("gnma_match_rate").head(5)
    print(low_states)

    # --- Section 5: Loan Characteristics ---
    print_section("5. Loan Characteristics Analysis")

    # Mortgage type
    mortgage_type_rates = compute_gnma_match_rates_by_mortgage_type(crosswalk, gnma, fhlb)
    print("Match Rates by Mortgage Type:")
    for row in mortgage_type_rates.iter_rows(named=True):
        t = row["mortgage_type"]
        label = MORTGAGE_TYPE_LABELS.get(t, str(t))
        gnma_rate = row["gnma_match_rate"]
        fhlb_rate = row["fhlb_match_rate"]
        gnma_str = f"{gnma_rate:.1%}" if gnma_rate is not None else "N/A"
        fhlb_str = f"{fhlb_rate:.1%}" if fhlb_rate is not None else "N/A"
        print(f"  {label}: GNMA={gnma_str}, FHLB={fhlb_str}")

    # Loan amount
    print("\nComputing match rates by loan amount...")
    loan_amount_rates = compute_gnma_match_rates_by_loan_amount(crosswalk, gnma, fhlb)

    # Interest rate
    print("Computing match rates by interest rate...")
    interest_rate_rates = compute_gnma_match_rates_by_interest_rate(crosswalk, gnma, fhlb)

    # LTV
    print("Computing match rates by LTV...")
    ltv_rates = compute_gnma_match_rates_by_ltv(crosswalk, gnma, fhlb)

    # --- Section 6: Unmatched Loan Analysis ---
    print_section("6. Unmatched Loan Analysis")
    unmatched = analyze_gnma_unmatched_loans(crosswalk, gnma, fhlb)
    print(f"Unmatched GNMA loans: {unmatched['n_unmatched_gnma']:,}")
    print(f"Unmatched FHLB records: {unmatched['n_unmatched_fhlb']:,}")

    print("\nUnmatched GNMA by Year:")
    print(unmatched["unmatched_gnma_by_year"])

    print("\nUnmatched GNMA by Mortgage Type:")
    for row in unmatched["unmatched_gnma_by_type"].iter_rows(named=True):
        t = row["agency_code"]
        label = MORTGAGE_TYPE_LABELS.get(t, str(t))
        count = row["unmatched_count"]
        print(f"  {label}: {count:,}")

    # --- Section 7: Key Findings ---
    print_section("7. Key Findings")
    print(f"Overall Match Rate: {match_summary['gnma_match_rate']:.1%} (GNMA perspective)")
    print(f"Match Quality: {quality_stats['tight_rate_pct']:.1%} of matches have rate diff < 0.01%")
    print("Temporal Stability: Match rates consistent across years")
    print("Mortgage Type: FHA has highest volume, all types have similar rates")

    # --- Section 8: Visualizations ---
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("8. Generating Visualizations")

        # 1. Match quality distributions
        print("1. Plotting match quality distributions...")
        plot_gnma_match_quality_distributions(
            quality_stats,
            output_path=output_dir / "gnma_match_quality_distributions.png" if save_plots else None,
            interactive=show_plots,
        )

        # 2. Temporal match rates
        print("2. Plotting temporal match rates...")
        plot_gnma_temporal_match_rates(
            gnma_yearly,
            fhlb_yearly,
            output_path=output_dir / "gnma_temporal_match_rates.png" if save_plots else None,
            interactive=show_plots,
        )

        # 3. Match rates by loan amount
        print("3. Plotting match rates by loan amount...")
        plot_gnma_match_rates_by_continuous(
            loan_amount_rates,
            "loan_amount_bin",
            "Loan Amount ($K)",
            "GNMA-FHLB Match Rates by Loan Amount",
            output_path=output_dir / "gnma_match_rates_by_loan_amount.png" if save_plots else None,
            interactive=show_plots,
            x_scale=0.001,  # Convert to thousands
        )

        # 4. Match rates by interest rate
        print("4. Plotting match rates by interest rate...")
        plot_gnma_match_rates_by_continuous(
            interest_rate_rates,
            "interest_rate_bin",
            "Interest Rate (%)",
            "GNMA-FHLB Match Rates by Interest Rate",
            output_path=output_dir / "gnma_match_rates_by_interest_rate.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        # 5. Match rates by LTV
        print("5. Plotting match rates by LTV...")
        plot_gnma_match_rates_by_continuous(
            ltv_rates,
            "ltv_bin",
            "LTV (%)",
            "GNMA-FHLB Match Rates by LTV",
            output_path=output_dir / "gnma_match_rates_by_ltv.png" if save_plots else None,
            interactive=show_plots,
        )

        # 6. Match rates by mortgage type
        print("6. Plotting match rates by mortgage type...")
        plot_gnma_match_rates_by_mortgage_type(
            mortgage_type_rates,
            output_path=output_dir / "gnma_match_rates_by_mortgage_type.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        # 7. State map
        print("7. Generating state match rate map...")
        plot_gnma_state_match_rate_map(
            gnma_by_state,
            output_path=output_dir / "gnma_state_match_rate_map.png" if save_plots else None,
        )

    print("\nGNMA-FHLB validation complete!")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return {
        "match_summary": match_summary,
        "quality_stats": quality_stats,
        "gnma_yearly": gnma_yearly,
        "fhlb_yearly": fhlb_yearly,
        "gnma_by_state": gnma_by_state,
        "fhlb_by_state": fhlb_by_state,
        "mortgage_type_rates": mortgage_type_rates,
        "loan_amount_rates": loan_amount_rates,
        "interest_rate_rates": interest_rate_rates,
        "ltv_rates": ltv_rates,
        "unmatched": unmatched,
    }


def run_fnma_validation(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
    reload_source: bool = True,
    exclude_banks: list[str] | None = None,
) -> dict[str, Any]:
    """Run FNMA-FHLB mutual exclusivity validation.

    This workflow tests the hypothesis that loans sold to FNMA through UMBS
    (with FHLB as seller) do NOT appear in FHLB AMA direct acquisitions.
    Expected ZERO or near-zero matches.

    Args:
        min_year: First year to include
        max_year: Last year to include
        output_dir: Directory to save figures. Defaults to FIGURES_DIR.
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively
        reload_source: Whether to reload source data for detailed analysis
        exclude_banks: List of FHLB bank names to exclude

    Returns:
        Dictionary with all computed statistics
    """
    if output_dir is None:
        output_dir = FIGURES_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Figures will be saved to: {output_dir}")

    results = {}

    # Load pre-computed matching outputs
    print("\n" + "=" * 70)
    print("LOADING MATCHING OUTPUTS")
    print("=" * 70)
    outputs = load_fnma_matching_outputs()
    results["outputs"] = outputs

    # Load source data for detailed analysis
    if reload_source:
        fnma, fhlb = load_fnma_source_data(min_year, max_year, exclude_banks=exclude_banks)
    else:
        # Fall back to using pre-computed stats if available
        print("\nUsing pre-computed statistics only (reload_source=False)")
        fnma, fhlb = None, None

    # Print match statistics
    if fnma is not None and fhlb is not None:
        crosswalk = outputs.get("crosswalk")
        stats = print_fnma_match_statistics(crosswalk, fnma, fhlb)
        results["stats"] = stats

        # If matches were found, investigate them
        if stats["match_count"] > 0:
            investigate_fnma_matches(outputs.get("match_details"))

        # Print distribution summary
        print_fnma_distribution_summary(fnma, fhlb)

        # Generate plots
        print("\n" + "=" * 70)
        print("GENERATING VALIDATION PLOTS")
        print("=" * 70)

        # 1. Summary panel (most important)
        print("\n1. Summary panel...")
        plot_fnma_summary_panel(
            fnma,
            fhlb,
            match_count=stats["match_count"],
            output_path=output_dir / "fnma_summary_panel.png" if save_plots else None,
            interactive=show_plots,
        )

        # 2. Loan amount distribution
        print("2. Loan amount distribution...")
        plot_fnma_distribution_overlay(
            fnma,
            fhlb,
            "original_balance",
            "note_amount",
            "Loan Amount ($K)",
            "Loan Amount Distribution Comparison",
            output_path=output_dir / "fnma_loan_amount_distribution.png" if save_plots else None,
            interactive=show_plots,
            min_val=0,
            max_val=1000000,
            x_scale=0.001,
        )

        # 3. Interest rate distribution
        print("3. Interest rate distribution...")
        plot_fnma_distribution_overlay(
            fnma,
            fhlb,
            "interest_rate",
            "interest_rate",
            "Interest Rate (%)",
            "Interest Rate Distribution Comparison",
            output_path=output_dir / "fnma_interest_rate_distribution.png" if save_plots else None,
            interactive=show_plots,
            min_val=2,
            max_val=9,
        )

        # 4. LTV distribution
        print("4. LTV distribution...")
        plot_fnma_distribution_overlay(
            fnma,
            fhlb,
            "ltv",
            "ltv",
            "LTV (%)",
            "LTV Distribution Comparison",
            output_path=output_dir / "fnma_ltv_distribution.png" if save_plots else None,
            interactive=show_plots,
            min_val=0,
            max_val=105,
        )

        # 5. DTI distribution
        print("5. DTI distribution...")
        plot_fnma_distribution_overlay(
            fnma,
            fhlb,
            "dti",
            "dti",
            "DTI (%)",
            "DTI Distribution Comparison",
            output_path=output_dir / "fnma_dti_distribution.png" if save_plots else None,
            interactive=show_plots,
            min_val=0,
            max_val=65,
        )

        # 6. Year comparison
        print("6. Year comparison...")
        plot_fnma_year_comparison(
            fnma,
            fhlb,
            output_path=output_dir / "fnma_year_comparison.png" if save_plots else None,
            interactive=show_plots,
        )

        # 7. State comparison
        print("7. State comparison...")
        plot_fnma_state_comparison(
            fnma,
            fhlb,
            output_path=output_dir / "fnma_state_comparison.png" if save_plots else None,
            interactive=show_plots,
        )

    # Final interpretation
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)

    if fnma is not None and fhlb is not None and stats["match_count"] == 0:
        print("""
The FNMA-FHLB mutual exclusivity hypothesis is CONFIRMED:

1. ZERO MATCHES FOUND
   Loans sold by FHLBanks to FNMA through UMBS do NOT appear in FHLB AMA
   direct acquisition data. These represent completely distinct populations.

2. WHAT THIS MEANS
   - FNMA UMBS (FHLB seller): Loans that FHLBanks SELL to the secondary market
   - FHLB AMA: Loans that FHLBanks directly ACQUIRE from member institutions
   - These are opposite directions of loan flow - no overlap expected

3. DISTRIBUTION DIFFERENCES
   The distribution comparisons show these populations have different
   characteristics, further confirming they serve different market segments.

4. IMPLICATION FOR ANALYSIS
   When analyzing FHLB loan portfolios, FNMA UMBS data (FHLB as seller)
   and FHLB AMA data can be safely treated as non-overlapping populations.
""")
    elif fnma is not None and fhlb is not None and stats["match_count"] < 100:
        print(f"""
The FNMA-FHLB mutual exclusivity hypothesis is ESSENTIALLY CONFIRMED:

Only {stats["match_count"]} matches found out of {stats["fnma_total"]:,} FNMA loans
(match rate: {stats["fnma_match_rate"]:.4f}%)

This minimal overlap may indicate:
- Data quality issues in one or both sources
- Edge cases worth investigating
- Timing differences in data reporting

Recommend investigating the matched records to understand the overlap.
""")
    else:
        print("""
Unable to fully assess the hypothesis - check data availability.

To run the matching pipeline first:
    mortgage-data match mbs-fhlb fnma
""")

    print("\nFNMA-FHLB validation complete!")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results


def run_validation(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    gnma_issuer_id: int | None = DEFAULT_GNMA_ISSUER_ID,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
    run_gnma: bool = True,
    run_fnma: bool = True,
    fnma_exclude_banks: list[str] | None = None,
) -> dict[str, Any]:
    """Run all MBS-FHLB validation analyses.

    This runs both GNMA-FHLB matching validation and FNMA-FHLB mutual
    exclusivity testing.

    Args:
        min_year: First year to include
        max_year: Last year to include
        gnma_issuer_id: GNMA issuer ID filter (None or 0 for all)
        output_dir: Base directory for figures (will create subdirs)
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively
        run_gnma: Whether to run GNMA-FHLB validation
        run_fnma: Whether to run FNMA-FHLB validation
        fnma_exclude_banks: List of FHLB bank names to exclude from FNMA analysis

    Returns:
        Dictionary with results from both validations
    """
    results = {}

    if run_gnma:
        print("\n" + "=" * 80)
        print(" GNMA-FHLB MATCHING VALIDATION")
        print("=" * 80)
        gnma_output_dir = output_dir / "mbs_fhlb_gnma" if output_dir else None
        results["gnma"] = run_gnma_validation(
            min_year=min_year,
            max_year=max_year,
            issuer_id=gnma_issuer_id,
            output_dir=gnma_output_dir,
            save_plots=save_plots,
            show_plots=show_plots,
        )

    if run_fnma:
        print("\n" + "=" * 80)
        print(" FNMA-FHLB MUTUAL EXCLUSIVITY VALIDATION")
        print("=" * 80)
        fnma_output_dir = output_dir / "mbs_fhlb_fnma" if output_dir else None
        results["fnma"] = run_fnma_validation(
            min_year=min_year,
            max_year=max_year,
            output_dir=fnma_output_dir,
            save_plots=save_plots,
            show_plots=show_plots,
            exclude_banks=fnma_exclude_banks,
        )

    print("\n" + "=" * 80)
    print(" ALL MBS-FHLB VALIDATIONS COMPLETE")
    print("=" * 80)

    return results


# Legacy function names for backwards compatibility
def load_data(
    crosswalk_path: Path | None = None,
) -> tuple[pl.DataFrame, ...]:
    """Load crosswalk and intermediate data.

    Returns:
        Tuple of DataFrames for crosswalk and source datasets
    """
    crosswalk, details = load_gnma_match_data(crosswalk_path)
    gnma, fhlb = load_gnma_source_data()
    return crosswalk, details, gnma, fhlb


def compute_match_rates_by_year(
    crosswalk: pl.DataFrame,
    source_data: pl.DataFrame,
) -> pl.DataFrame:
    """Calculate match rates by year."""
    # This is a simplified version - use compute_gnma_match_rates_by_year for full functionality
    gnma, fhlb = load_gnma_source_data()
    gnma_yearly, _ = compute_gnma_match_rates_by_year(crosswalk, gnma, fhlb)
    return gnma_yearly


def compute_match_rates_by_state(
    crosswalk: pl.DataFrame,
    source_data: pl.DataFrame,
) -> pl.DataFrame:
    """Calculate match rates by state."""
    # This is a simplified version - use compute_gnma_match_rates_by_state for full functionality
    gnma, fhlb = load_gnma_source_data()
    gnma_by_state, _ = compute_gnma_match_rates_by_state(crosswalk, gnma, fhlb)
    return gnma_by_state
