#!/usr/bin/env python3
"""FNMA (Fannie Mae) - FHFA Matching Logic using Polars LazyFrames.

Matches FNMA loan-level disclosure data with FHFA sf_c (Federal Housing
Finance Agency single-family census tract file) to create a crosswalk
between the two datasets.

The matching uses a multi-field approach:
1. Exact match on categorical fields: state, MSA, channel, units, borrowers,
   FTHB, purpose, occupancy, property type, term
2. Exact match on interest rate (most reliable field, ~100% exact in valid matches)
3. Tolerance-based matching accounting for FHFA binning:
   - Amount: $5,000 tolerance (FHFA uses $10k bins centered at $5k)
   - LTV: 1% tolerance (both datasets use integer values)
   - DTI: 10% tolerance (FHFA uses 10-point bins for DTI ≤35)
4. Mutual best-match scoring to resolve duplicates
5. Match quality tier assignment for confidence assessment

Match Quality Tiers:
- Tier 1: Exact rate + exact LTV (highest confidence)
- Tier 2: Exact rate only (high confidence)
- Tier 3: Tolerance-based match (moderate confidence)

Expected match rates (2023 data):
- ~95% of FNMA loans can be matched to FHFA records
- FNMA data covers ~90% of FHFA Fannie Mae records
- Of matched loans: 99.9% have exact interest rate AND exact LTV

IMPORTANT: This module uses Polars LazyFrames throughout for memory efficiency.
Data is only collected/materialized when writing output files via sink_parquet().
"""

# Standard library imports

from __future__ import annotations

from dataclasses import dataclass

# Third-party imports
import polars as pl

# Local application imports
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_fhfa.config import (
    CROSSWALK_OUTPUT_DIR,
    get_fhfa_config,
    get_fnma_config,
)

logger = get_logger(__name__)


@dataclass
class MatchingTolerances:
    """Configuration for FNMA-FHFA matching tolerances.

    Optimized based on empirical analysis of 2023 FNMA-FHFA matching.

    IMPORTANT: FHFA uses binned values similar to HMDA:
    - Loan amount: $10k bins centered at midpoint ($5k, $15k, $25k, etc.)
    - LTV: Integer values only (1% precision)
    - DTI: 10=<20%, 20=20-29%, 30=30-35%, 36-49=exact, 50=50-60%, 60=>60%

    Tolerance rationale:
    - Interest rate: Exact match required (most reliable field)
    - Loan amount: When round_amount_to_fhfa_bins=True (default), FNMA amounts
      are pre-rounded to FHFA bin centers so amount_tolerance=0 gives exact
      matches. When disabled, $5,000 tolerance accounts for FHFA $10k binning.
    - LTV: 1% tolerance (exact in 99.8% of valid matches)
    - DTI: Smart bin-aware matching via build_dti_match_expr(); tolerance
      applies to edge cases and exact range (36-49)

    Attributes:
        rate_tolerance: Interest rate tolerance in percentage points (default: 0.01)
        amount_tolerance: Loan amount tolerance in dollars (default: 0 when rounding, 5000 otherwise)
        ltv_tolerance: LTV tolerance in percentage points (default: 1.0%)
        dti_tolerance: DTI tolerance in percentage points (default: 1.0%)
        term_tolerance: Loan term tolerance in months (default: 0 = exact)
        round_amount_to_fhfa_bins: Pre-round FNMA loan amounts to FHFA $10k bin centers before matching.
            Loans on exact $10k boundaries are duplicated into both adjacent bins.
            When True, amount_tolerance defaults to 0 (exact match on bin centers).
        apply_post_match_filter: Whether to apply post-match filtering (default: False)
    """

    # Candidate-finding tolerances (optimized defaults)
    rate_tolerance: float = 0.01  # Small tolerance for FHFA rounding differences (esp. early years)
    amount_tolerance: float = 0.0  # Exact match on rounded bin centers (default with rounding)
    ltv_tolerance: float = 1.0  # Tight LTV tolerance
    dti_tolerance: float = 1.0  # 1% for exact range (36-49); also controls bin 30 boundary
    term_tolerance: int = 0  # Exact term match

    # Post-match tolerances (not used by default)
    post_rate_tolerance: float = 0.01
    post_amount_tolerance: float = 0.0

    # Behavior
    round_amount_to_fhfa_bins: bool = True  # Pre-round FNMA amounts to FHFA bin centers
    apply_post_match_filter: bool = False

    def __post_init__(self):
        """Validate tolerances."""
        if self.post_rate_tolerance > self.rate_tolerance:
            raise ValueError("post_rate_tolerance must be <= rate_tolerance")
        if self.post_amount_tolerance > self.amount_tolerance:
            raise ValueError("post_amount_tolerance must be <= amount_tolerance")


# Default tolerances (optimized for high-quality 1:1 matching)
DEFAULT_TOLERANCES = MatchingTolerances()

# Legacy tolerances without bin rounding (for relaxed matching rounds)
TOLERANCES_NO_BIN_ROUNDING = MatchingTolerances(
    amount_tolerance=5000.0,
    post_amount_tolerance=5000.0,
    round_amount_to_fhfa_bins=False,
)


# State FIPS code mapping (2-letter to FIPS numeric)
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


def build_dti_match_expr(
    fnma_col: str = "dti",
    fhfa_col: str = "dti_fhfa",
    tolerance: float = 2.0,
) -> pl.Expr:
    """Build a Polars expression for smart DTI matching accounting for FHFA binning.

    FHFA DTI dictionary:
    - 10 = Less than 20% (DTI 0-19)
    - 20 = 20 to less than 30% (DTI 20-29)
    - 30 = 30 to less than 36% (DTI 30-35)
    - Actual value = 36 to 49% (exact)
    - 50 = 50 to 60% (DTI 50-60)
    - 60 = Greater than 60% (DTI >60)
    - 99 = Not available

    For binned ranges, the FNMA DTI must fall within the correct bin.
    If FNMA DTI is within `tolerance` of a bin edge, the adjacent bin is
    also accepted.

    For the exact range (36-49), FHFA has 1-point precision, so we require
    FNMA DTI to be within `tolerance` of the FHFA value.

    Args:
        fnma_col: Name of the FNMA DTI column
        fhfa_col: Name of the FHFA DTI column
        tolerance: Tolerance for edge cases and exact range matching (default: 2.0)

    Returns:
        Boolean expression that is True if DTI matches

    Examples:
        - FNMA DTI 15, FHFA DTI 10: Match (15 is in 0-19 bin → 10)
        - FNMA DTI 19, FHFA DTI 20: Match if tolerance ≥ 1 (19 is within 1 of edge 20)
        - FNMA DTI 42, FHFA DTI 43: Match if tolerance ≥ 1
        - FNMA DTI 42, FHFA DTI 45: No match (difference 3 > tolerance 2)
    """
    fnma = pl.col(fnma_col)
    fhfa = pl.col(fhfa_col)

    # Allow null values to pass (also handle FHFA 99 = not available)
    null_ok = fnma.is_null() | fhfa.is_null() | (fhfa == 99)

    # For exact range (36-49): tolerance check with bin boundary handling
    # When FNMA DTI is near 36, also accept FHFA bin 30 (which covers 30-35.99%)
    # This handles cases where slight DTI calculation differences put the loan
    # on different sides of the 36 boundary
    exact_range = (fnma >= 36) & (fnma <= 49)
    exact_match = (
        ((fnma - fhfa).abs() <= tolerance)  # Normal exact match
        | ((fhfa == 30) & (fnma < 36 + tolerance))  # Near lower boundary, allow bin 30
    )

    # Bin edges: 20, 30, 36, 50, 60
    # DTI 0-19 → bin 10
    bin_10 = fnma < 20
    match_10 = (fhfa == 10) | (
        (fhfa == 20) & (fnma >= 20 - tolerance)  # Near upper edge (20)
    )

    # DTI 20-29 → bin 20
    bin_20 = (fnma >= 20) & (fnma < 30)
    match_20 = (
        (fhfa == 20)
        | (
            (fhfa == 10) & (fnma < 20 + tolerance)  # Near lower edge (20)
        )
        | (
            (fhfa == 30) & (fnma >= 30 - tolerance)  # Near upper edge (30)
        )
    )

    # DTI 30-35 → bin 30
    bin_30 = (fnma >= 30) & (fnma < 36)
    match_30 = (
        (fhfa == 30)
        | (
            (fhfa == 20) & (fnma < 30 + tolerance)  # Near lower edge (30)
        )
        | (
            # Near upper edge (36) - could match exact 36 if within tolerance
            ((fnma - fhfa).abs() <= tolerance) & (fhfa >= 36) & (fhfa <= 49)
        )
    )

    # DTI 50-60 → bin 50
    bin_50 = (fnma >= 50) & (fnma <= 60)
    match_50 = (
        (fhfa == 50)
        | (
            # Near lower edge (50) - could match exact 49 if within tolerance
            ((fnma - fhfa).abs() <= tolerance) & (fhfa >= 36) & (fhfa <= 49)
        )
        | (
            (fhfa == 60) & (fnma >= 60 - tolerance)  # Near upper edge (60)
        )
    )

    # DTI > 60 → bin 60
    bin_60 = fnma > 60
    match_60 = (fhfa == 60) | (
        (fhfa == 50) & (fnma < 60 + tolerance)  # Near lower edge (60)
    )

    # Combine all cases
    return null_ok | (
        (exact_range & exact_match)
        | (bin_10 & match_10)
        | (bin_20 & match_20)
        | (bin_30 & match_30)
        | (bin_50 & match_50)
        | (bin_60 & match_60)
    )


def load_fhfa_data(
    year: int,
) -> pl.LazyFrame:
    """Load FHFA sf_c data for Fannie Mae loans as a LazyFrame.

    Args:
        year: Year to load

    Returns:
        FHFA data filtered to Fannie Mae (enterprise_flag=1)
    """
    fhfa_config = get_fhfa_config()
    fhfa_file = fhfa_config.FHFA_SILVER_DIR / "sf_c" / f"sf_c_{year}.parquet"

    if not fhfa_file.exists():
        raise FileNotFoundError(f"FHFA file not found: {fhfa_file}")

    logger.debug("Scanning FHFA data from %s...", fhfa_file)

    # Use scan_parquet for lazy loading
    lf = pl.scan_parquet(fhfa_file)

    # Filter to Fannie Mae (enterprise_flag = 1)
    lf = lf.filter(pl.col("enterprise_flag") == 1)

    return lf


def load_fnma_data(
    year: int,
) -> pl.LazyFrame:
    """Load FNMA issuances data for a given year as a LazyFrame.

    Args:
        year: Year to load

    Returns:
        FNMA issuances data filtered to the specified year
    """
    fnma_config = get_fnma_config()
    issuances_dir = fnma_config.FNMA_SILVER_DIR / "issuances"

    # Find quarterly files for the year
    quarterly_files = sorted(issuances_dir.glob(f"{year}Q*.parquet"))

    if not quarterly_files:
        raise FileNotFoundError(f"No FNMA files found for year {year} in {issuances_dir}")

    logger.debug("Scanning %d FNMA quarterly files for %d...", len(quarterly_files), year)

    # Scan and concat all quarterly files lazily
    lf = pl.concat([pl.scan_parquet(f) for f in quarterly_files], how="diagonal_relaxed")

    # Note: We intentionally do NOT filter by origination year here.
    # FNMA quarterly files (e.g., 2024Q1.parquet) contain loans securitized
    # in that quarter, regardless of when they were originated. A loan
    # originated in late 2023 may be securitized in early 2024, and we
    # want to match it to FHFA 2024 acquisitions.

    return lf


def prepare_fhfa_for_matching(lf: pl.LazyFrame, year: int | None = None) -> pl.LazyFrame:
    """Prepare FHFA data for matching by selecting and standardizing columns.

    Args:
        lf: Raw FHFA sf_c data
        year: The data year. Retained for parity with the FHLMC preparer.

    Returns:
        Prepared FHFA data with standardized columns
    """
    # Select and rename columns for matching
    lf = lf.select(
        [
            pl.col("record_number").alias("fhfa_record_id"),
            pl.col("enterprise_flag").alias("fhfa_enterprise"),
            pl.col("state_code").alias("state_fips"),
            pl.col("msa_code").alias("msa"),
            pl.col("application_channel").alias("channel"),  # 1=Retail, 2=Broker, 9=Correspondent
            pl.col("interest_rate_at_origination").alias("interest_rate"),
            pl.col("note_amount").alias("loan_amount"),
            pl.col("ltv_at_origination").alias("ltv"),
            pl.col("dti_ratio").alias("dti"),
            pl.col("loan_term").alias("term"),
            pl.col("number_of_units").alias("num_units"),
            pl.col("number_of_borrowers").alias("num_borrowers"),
            pl.col("first_time_home_buyer").alias("fthb"),  # 1=Yes, 2=No
            pl.col("loan_purpose"),  # 1=Purchase, 2=Refi, 7=Cash-out
            pl.col("occupancy_code"),  # 1=Primary, 2=Second, 3=Investment
            pl.col("property_type"),  # 1=Site-built, 2=Manufactured
            pl.col("year"),
            # Origination timing: 1=same year as acquisition, 2=prior year
            pl.col("date_of_mortgage_note").alias("orig_year_flag"),
        ]
    )

    # Convert MSA 99999 (non-metro) to 0 for matching
    lf = lf.with_columns(
        pl.when(pl.col("msa") == 99999).then(0).otherwise(pl.col("msa")).alias("msa")
    )

    # Cast to consistent types
    lf = lf.with_columns(
        [
            pl.col("state_fips").cast(pl.Int64),
            pl.col("msa").cast(pl.Int64),
            pl.col("channel").cast(pl.Int64),
            pl.col("interest_rate").cast(pl.Float64),
            pl.col("loan_amount").cast(pl.Float64),
            pl.col("ltv").cast(pl.Float64),
            pl.col("dti").cast(pl.Float64),
            pl.col("term").cast(pl.Int64),
            pl.col("num_units").cast(pl.Int64),
            pl.col("num_borrowers").cast(pl.Int64),
            pl.col("fthb").cast(pl.Int64),
            pl.col("loan_purpose").cast(pl.Int64),
            pl.col("occupancy_code").cast(pl.Int64),
            pl.col("property_type").cast(pl.Int64),
        ]
    )

    return lf


def prepare_fnma_for_matching(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Prepare FNMA data for matching by selecting, transforming, and standardizing columns.

    Args:
        lf: Raw FNMA issuances data

    Returns:
        Prepared FNMA data with standardized columns matching FHFA format
    """
    # Normalize the MSA header across FNMA loan-performance vintages: 2019+ files use
    # "Metropolitan Statistical Area (MSA)" while pre-2019 files use the longer
    # "...(MSA) or Metropolitan Statistical Division Area (MSDA)". Rename to the short form.
    msa_canon = "Metropolitan Statistical Area (MSA)"
    names = lf.collect_schema().names()
    if msa_canon not in names:
        alt = next((c for c in names if c.startswith(msa_canon)), None)
        if alt is not None:
            lf = lf.rename({alt: msa_canon})

    # Select columns
    # Note: We use CLTV instead of LTV because FHFA's "LTV at origination" field
    # actually reports "Combined LTV (CLTV) where available" per their data dictionary.
    # This means FHFA uses CLTV when there's a subordinate lien, otherwise LTV.
    # To match properly, we use FNMA's CLTV when available, falling back to LTV.
    lf = lf.select(
        [
            pl.col("Loan Identifier").alias("fnma_loan_id"),
            pl.col("Property State").alias("state_abbr"),
            pl.col("Metropolitan Statistical Area (MSA)").alias("msa_str"),
            pl.col("Channel").alias("channel_str"),  # R=Retail, B=Broker, C=Correspondent
            pl.col("Original Interest Rate").alias("interest_rate"),
            pl.col("Original UPB").alias("loan_amount"),
            pl.col("Original Loan to Value Ratio (LTV)").alias("ltv_raw"),
            pl.col("Original Combined Loan to Value Ratio (CLTV)").alias("cltv"),
            pl.col("Debt-To-Income (DTI)").alias("dti"),
            pl.col("Original Loan Term").alias("term"),
            pl.col("Number of Units").alias("num_units"),
            pl.col("Number of Borrowers").alias("num_borrowers"),
            pl.col("First Time Home Buyer Indicator").alias("fthb_str"),
            pl.col("Loan Purpose ").alias("purpose_str"),
            pl.col("Occupancy Status").alias("occupancy_str"),
            pl.col("Property Type").alias("property_str"),
            pl.col("Origination Date").alias("orig_date"),
        ]
    )

    # Use CLTV when available, otherwise fall back to LTV
    # This matches FHFA's behavior of reporting "CLTV where available"
    lf = lf.with_columns(pl.coalesce(pl.col("cltv"), pl.col("ltv_raw")).alias("ltv"))

    # Map state abbreviation to FIPS code
    lf = lf.with_columns(
        pl.col("state_abbr").replace_strict(STATE_FIPS, default=None).alias("state_fips")
    )

    # Map MSA: convert string to int, '00000' (non-metro) becomes 0 to match FHFA coding
    lf = lf.with_columns(
        pl.when(pl.col("msa_str") == "00000")
        .then(0)
        .otherwise(pl.col("msa_str").cast(pl.Int64, strict=False))
        .alias("msa")
    )

    # Map channel: R=1 (Retail), B=2 (Broker), C=9 (Correspondent) to match FHFA coding
    lf = lf.with_columns(
        pl.when(pl.col("channel_str") == "R")
        .then(1)
        .when(pl.col("channel_str") == "B")
        .then(2)
        .when(pl.col("channel_str") == "C")
        .then(9)
        .otherwise(None)
        .alias("channel")
    )

    # Map first-time home buyer: Y=1, N=2 (to match FHFA coding)
    lf = lf.with_columns(
        pl.when(pl.col("fthb_str") == "Y")
        .then(1)
        .when(pl.col("fthb_str") == "N")
        .then(2)
        .otherwise(None)
        .alias("fthb")
    )

    # Map loan purpose: P=1 (Purchase), R=2 (Refi), C=7 (Cash-out)
    lf = lf.with_columns(
        pl.when(pl.col("purpose_str") == "P")
        .then(1)
        .when(pl.col("purpose_str") == "R")
        .then(2)
        .when(pl.col("purpose_str") == "C")
        .then(7)
        .otherwise(None)
        .alias("loan_purpose")
    )

    # Map occupancy: P=1 (Primary), S=2 (Second), I=3 (Investment)
    lf = lf.with_columns(
        pl.when(pl.col("occupancy_str") == "P")
        .then(1)
        .when(pl.col("occupancy_str") == "S")
        .then(2)
        .when(pl.col("occupancy_str") == "I")
        .then(3)
        .otherwise(None)
        .alias("occupancy_code")
    )

    # Map property type: SF/PU=1 (Site-built), MH=2 (Manufactured), CO/CP=1 (treat as site-built)
    lf = lf.with_columns(
        pl.when(pl.col("property_str").is_in(["SF", "PU", "CO", "CP"]))
        .then(1)
        .when(pl.col("property_str") == "MH")
        .then(2)
        .otherwise(None)
        .alias("property_type")
    )

    # Extract year from origination date (MMYYYY format)
    lf = lf.with_columns(
        pl.col("orig_date").str.slice(2, 4).cast(pl.Int64, strict=False).alias("year")
    )

    # Cast numeric columns
    lf = lf.with_columns(
        [
            pl.col("interest_rate").cast(pl.Float64),
            pl.col("loan_amount").cast(pl.Float64),
            pl.col("ltv").cast(pl.Float64),
            pl.col("dti").cast(pl.Float64),
            pl.col("term").cast(pl.Int64),
            pl.col("num_units").cast(pl.Int64),
            pl.col("num_borrowers").cast(pl.Int64),
        ]
    )

    # Select final columns
    lf = lf.select(
        [
            "fnma_loan_id",
            "state_fips",
            "msa",
            "channel",
            "interest_rate",
            "loan_amount",
            "ltv",
            "dti",
            "term",
            "num_units",
            "num_borrowers",
            "fthb",
            "loan_purpose",
            "occupancy_code",
            "property_type",
            "year",
        ]
    )

    return lf


def round_to_fhfa_amount_bins(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Round FNMA loan amounts to FHFA $10k bin centers for exact matching.

    FHFA reports loan amounts in $10k bins centered at the midpoint:
    $5k, $15k, $25k, ..., $145k, $155k, etc. (i.e., floor(amount/10k)*10k + 5k).

    For FNMA amounts NOT on a $10k boundary, round to the bin center.
    For amounts ON an exact $10k boundary (ambiguous bin membership),
    duplicate the row into both adjacent bin centers.

    Examples:
        $141,000 -> $145,000 (single row)
        $149,000 -> $145,000 (single row)
        $140,000 -> $135,000 AND $145,000 (two rows)

    Args:
        lf: FNMA data with a ``loan_amount`` column

    Returns:
        Data with ``loan_amount`` replaced by bin-center values.
        Rows on $10k boundaries are duplicated. The original amount
        is preserved in ``loan_amount_orig``.
    """
    # Preserve original amount
    lf = lf.with_columns(pl.col("loan_amount").alias("loan_amount_orig"))

    # Non-boundary loans: round to bin center
    non_boundary = lf.filter(pl.col("loan_amount") % 10_000 != 0).with_columns(
        (pl.col("loan_amount") // 10_000 * 10_000 + 5_000).alias("loan_amount")
    )

    # Boundary loans: duplicate into lower and upper bin centers
    boundary = lf.filter(pl.col("loan_amount") % 10_000 == 0)
    boundary_lower = boundary.with_columns((pl.col("loan_amount") - 5_000).alias("loan_amount"))
    boundary_upper = boundary.with_columns((pl.col("loan_amount") + 5_000).alias("loan_amount"))

    return pl.concat([non_boundary, boundary_lower, boundary_upper], how="diagonal_relaxed")


def match_fnma_fhfa(
    fnma_lf: pl.LazyFrame,
    fhfa_lf: pl.LazyFrame,
    tolerances: MatchingTolerances | None = None,
) -> pl.LazyFrame:
    """Match FNMA loans to FHFA records using multi-field matching (fully lazy).

    Uses a single join approach with Polars optimization:
    1. Join on exact match fields (state, MSA, channel, units, borrowers, FTHB,
       purpose, occupancy, property type, term)
    2. Filter using numeric tolerances (rate, amount, LTV, DTI)
    3. Resolve duplicates via mutual best-match scoring
    4. Apply post-match tolerances for final quality filtering

    Args:
        fnma_lf: Prepared FNMA data
        fhfa_lf: Prepared FHFA data
        tolerances: Tolerance configuration. If None, uses DEFAULT_TOLERANCES.

    Returns:
        Matched loan pairs with all relevant columns
    """
    if tolerances is None:
        tolerances = DEFAULT_TOLERANCES

    # Optionally round FNMA amounts to FHFA bin centers before matching
    if tolerances.round_amount_to_fhfa_bins:
        fnma_lf = round_to_fhfa_amount_bins(fnma_lf)

    # Exact match fields
    exact_match_cols = [
        "state_fips",
        "msa",
        "channel",
        "num_units",
        "num_borrowers",
        "fthb",
        "loan_purpose",
        "occupancy_code",
        "property_type",
        "term",
    ]

    # When bin rounding is on, loan_amount is already aligned to FHFA bin centers
    # so we can use it as an exact join key (massive performance win)
    if tolerances.round_amount_to_fhfa_bins:
        exact_match_cols.append("loan_amount")

    # Join on exact fields
    merged = fnma_lf.join(
        fhfa_lf.drop("orig_year_flag"),  # Drop year flag, not needed for matching
        on=exact_match_cols,
        how="inner",
        suffix="_fhfa",
    )

    # Apply numeric tolerances

    # Interest rate tolerance
    merged = merged.filter(
        (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs() <= tolerances.rate_tolerance
    )

    # Loan amount tolerance (skip when bin rounding handles it via join key)
    if not tolerances.round_amount_to_fhfa_bins:
        merged = merged.filter(
            (pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs()
            <= tolerances.amount_tolerance
        )

    # LTV tolerance (allow null to pass)
    merged = merged.filter(
        pl.col("ltv").is_null()
        | pl.col("ltv_fhfa").is_null()
        | ((pl.col("ltv") - pl.col("ltv_fhfa")).abs() <= tolerances.ltv_tolerance)
    )

    # DTI matching using smart bin-aware logic
    merged = merged.filter(build_dti_match_expr("dti", "dti_fhfa", tolerances.dti_tolerance))

    # Mutual best-match scoring
    # Compute match score (lower = better match)
    rate_weight = 1.0 / tolerances.rate_tolerance if tolerances.rate_tolerance > 0 else 1000.0
    ltv_weight = 1.0 / tolerances.ltv_tolerance if tolerances.ltv_tolerance > 0 else 1000.0
    dti_weight = 1.0 / tolerances.dti_tolerance if tolerances.dti_tolerance > 0 else 1000.0

    score_terms = (
        ((pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs() * rate_weight)
        + ((pl.col("ltv") - pl.col("ltv_fhfa")).abs() * ltv_weight).fill_null(0)
        + ((pl.col("dti") - pl.col("dti_fhfa")).abs() * dti_weight).fill_null(0)
    )
    if not tolerances.round_amount_to_fhfa_bins:
        amount_weight = (
            1.0 / tolerances.amount_tolerance if tolerances.amount_tolerance > 0 else 1000.0
        )
        score_terms = score_terms + (
            (pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs() * amount_weight
        )

    merged = merged.with_columns(score_terms.alias("match_score"))

    # Rank each pair within FNMA and FHFA groups
    merged = merged.with_columns(
        [
            pl.col("match_score").rank(method="dense").over("fnma_loan_id").alias("rank_in_fnma"),
            pl.col("match_score").rank(method="dense").over("fhfa_record_id").alias("rank_in_fhfa"),
        ]
    )

    # Keep only mutual best matches (best for both sides)
    unique_matches = merged.filter((pl.col("rank_in_fnma") == 1) & (pl.col("rank_in_fhfa") == 1))

    # Ensure 1:1 by checking for remaining duplicates
    unique_matches = unique_matches.with_columns(
        [
            pl.len().over("fnma_loan_id").alias("fnma_count"),
            pl.len().over("fhfa_record_id").alias("fhfa_count"),
        ]
    )

    unique_matches = unique_matches.filter(
        (pl.col("fnma_count") == 1) & (pl.col("fhfa_count") == 1)
    )

    # Compute match quality tiers based on rate and LTV only.
    unique_matches = unique_matches.with_columns(
        [
            (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs().alias("_rate_diff"),
            (pl.col("ltv") - pl.col("ltv_fhfa")).abs().fill_null(0).alias("_ltv_diff"),
        ]
    )

    unique_matches = unique_matches.with_columns(
        pl.when((pl.col("_rate_diff") == 0) & (pl.col("_ltv_diff") == 0))
        .then(1)
        .when(pl.col("_rate_diff") == 0)
        .then(2)
        .otherwise(3)
        .alias("match_quality_tier")
    )

    # If bin rounding was applied, restore original loan amount
    if tolerances.round_amount_to_fhfa_bins:
        unique_matches = unique_matches.with_columns(
            pl.col("loan_amount_orig").alias("loan_amount")
        )

    # Drop temporary columns
    drop_cols = [
        "_rate_diff",
        "_ltv_diff",
        "match_score",
        "rank_in_fnma",
        "rank_in_fhfa",
        "fnma_count",
        "fhfa_count",
    ]
    if tolerances.round_amount_to_fhfa_bins:
        drop_cols.append("loan_amount_orig")
    unique_matches = unique_matches.drop(drop_cols)

    # Apply post-match filtering if enabled
    if tolerances.apply_post_match_filter:
        unique_matches = unique_matches.filter(
            (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs()
            < tolerances.post_rate_tolerance
        )
        if not tolerances.round_amount_to_fhfa_bins:
            unique_matches = unique_matches.filter(
                (pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs()
                <= tolerances.post_amount_tolerance
            )

    return unique_matches


def match_fnma_fhfa_by_year(
    fnma_lf: pl.LazyFrame,
    fhfa_lf: pl.LazyFrame,
    acquisition_year: int,
    tolerances: MatchingTolerances | None = None,
) -> pl.LazyFrame:
    """Match FNMA loans to FHFA records with year-based pre-filtering.

    Uses FHFA's orig_year_flag to split matching:
    - orig_year_flag=1: same year as acquisition -> match to FNMA from acquisition year
    - orig_year_flag=2: prior year -> match to FNMA from year before acquisition

    Args:
        fnma_lf: Prepared FNMA data
        fhfa_lf: Prepared FHFA data
        acquisition_year: The FHFA acquisition year
        tolerances: Tolerance configuration.

    Returns:
        Matched loan pairs
    """
    if tolerances is None:
        tolerances = DEFAULT_TOLERANCES

    # Same-year originations (flag=1) -> match to FNMA from acquisition year
    fhfa_same = fhfa_lf.filter(pl.col("orig_year_flag") == 1)
    fnma_same = fnma_lf.filter(pl.col("year") == acquisition_year)
    matches_same = match_fnma_fhfa(fnma_same, fhfa_same, tolerances)

    # Prior-year originations (flag=2) -> match to FNMA from year before acquisition
    fhfa_prior = fhfa_lf.filter(pl.col("orig_year_flag") == 2)
    fnma_prior = fnma_lf.filter(pl.col("year") == acquisition_year - 1)
    matches_prior = match_fnma_fhfa(fnma_prior, fhfa_prior, tolerances)

    # Combine matches from both year groups
    combined = pl.concat([matches_same, matches_prior], how="diagonal_relaxed")

    return combined


def run_fnma_fhfa_matching(
    year: int = 2023,
    tolerances: MatchingTolerances | None = None,
) -> pl.DataFrame:
    """Run the complete FNMA-FHFA matching pipeline for a single year.

    Uses LazyFrames throughout and only materializes when writing output.

    Args:
        year: Year to match (default: 2023)
        tolerances: Tolerance configuration. If None, uses DEFAULT_TOLERANCES.

    Returns:
        Matched loan pairs (collected for return value)
    """
    if tolerances is None:
        tolerances = DEFAULT_TOLERANCES

    # Ensure output directory exists
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("FNMA-FHFA matching for %d", year)
    amt_desc = f"±${tolerances.amount_tolerance:,.0f}"
    if tolerances.round_amount_to_fhfa_bins:
        amt_desc = f"bin-rounded{f' ±${tolerances.amount_tolerance:,.0f}' if tolerances.amount_tolerance > 0 else ''}"
    logger.info(
        "Tolerances: rate ±%s%%, amount %s, LTV ±%s%%, DTI ±%s%%",
        tolerances.rate_tolerance,
        amt_desc,
        tolerances.ltv_tolerance,
        tolerances.dti_tolerance,
    )
    if tolerances.apply_post_match_filter:
        logger.info(
            "Post-filter: rate <%s%%, amount <=$%s",
            tolerances.post_rate_tolerance,
            f"{tolerances.post_amount_tolerance:,.0f}",
        )

    # Load data as LazyFrames
    fhfa_raw = load_fhfa_data(year)
    fnma_raw = load_fnma_data(year)

    # Prepare for matching (still lazy)
    fhfa_prepared = prepare_fhfa_for_matching(fhfa_raw, year=year)
    fnma_prepared = prepare_fnma_for_matching(fnma_raw)

    logger.debug("Building match query (lazy)...")

    # Run matching with year-based filtering (still lazy)
    matches_lf = match_fnma_fhfa_by_year(fnma_prepared, fhfa_prepared, year, tolerances)

    logger.debug("Executing match query...")

    # Collect matches once for crosswalk, stats, and return value
    matches = matches_lf.collect()
    match_count = len(matches)

    # Write crosswalk (just ID pairs)
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk_file = CROSSWALK_OUTPUT_DIR / f"fnma_fhfa_crosswalk_{year}.parquet"
    matches.select(
        [
            "fnma_loan_id",
            "fhfa_record_id",
            pl.col("year_fhfa").alias("fhfa_year"),
            "fhfa_enterprise",
        ]
    ).write_parquet(crosswalk_file)

    logger.info("Crosswalk saved to: %s", crosswalk_file)

    # Collect input counts for stats
    fnma_count = fnma_prepared.select(pl.len()).collect().item()
    fhfa_count = fhfa_prepared.select(pl.len()).collect().item()

    fnma_match_rate = match_count / fnma_count * 100 if fnma_count > 0 else 0
    fhfa_match_rate = match_count / fhfa_count * 100 if fhfa_count > 0 else 0

    logger.info("Results: FNMA records: %s", f"{fnma_count:,}")
    logger.info("FHFA records: %s", f"{fhfa_count:,}")
    logger.info("Total matches: %s", f"{match_count:,}")
    logger.info("FNMA match rate: %.1f%%", fnma_match_rate)
    logger.info("FHFA match rate: %.1f%%", fhfa_match_rate)

    # Report quality tiers
    tier_counts = (
        matches.group_by("match_quality_tier")
        .agg(pl.len().alias("count"))
        .sort("match_quality_tier")
    )
    logger.info("Match quality tiers:")
    for row in tier_counts.iter_rows(named=True):
        tier = row["match_quality_tier"]
        count = row["count"]
        pct = count / match_count * 100 if match_count > 0 else 0
        tier_desc = {1: "Exact rate+LTV", 2: "Exact rate only", 3: "Tolerance match"}
        logger.info(
            "  Tier %s (%s): %s (%.1f%%)", tier, tier_desc.get(tier, "Unknown"), f"{count:,}", pct
        )

    return matches


# Legacy API for backward compatibility
def match_fhfa_fnma(file_fnma, file_fhfa, save_folder, file_suffix=""):
    """Legacy function signature - use run_fnma_fhfa_matching() instead."""
    raise NotImplementedError(
        "This legacy API is deprecated. Use run_fnma_fhfa_matching(year=YYYY) instead."
    )


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")
    df = run_fnma_fhfa_matching(year=2023)
    logger.info("Total matches: %s", f"{len(df):,}")
