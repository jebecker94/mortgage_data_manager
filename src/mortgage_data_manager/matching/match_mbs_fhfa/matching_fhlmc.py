#!/usr/bin/env python3
"""FHLMC (Freddie Mac) - FHFA Matching Logic using Polars LazyFrames.

Matches FHLMC loan-level origination data with FHFA sf_c (Federal Housing
Finance Agency single-family census tract file) to create a crosswalk
between the two datasets.

This module uses lazy evaluation throughout for memory efficiency,
enabling processing of high-volume years (2019-2021) that would otherwise
run out of memory with eager evaluation.

Key features:
1. Uses scan_parquet() for lazy loading instead of read_parquet()
2. All operations stay as LazyFrame until output
3. Uses year-based pre-filtering via FHFA's date_of_mortgage_note field
4. Includes state_fips in exact join (lets Polars optimize)
5. Uses sink_parquet() to stream results to disk

The matching uses multi-field approach:
1. Exact match on categorical fields: state, MSA, channel, units, borrowers,
   FTHB, purpose, occupancy, property type, term (binned), loan amount (pre-rounded)
   Note: MSA requires mapping FHLMC Metropolitan Division codes to parent CBSA codes
   using the Census Bureau's delineation crosswalk (schemas/census/md_to_cbsa_crosswalk.csv)
2. Near-exact match on interest rate (1 bp tolerance for FHFA rounding)
3. Tolerance-based matching:
   - Amount: Exact (pre-rounded to FHFA $10k bin midpoints)
   - LTV/CLTV: 1% tolerance - FHFA reports "CLTV where available" so we use
     FHLMC's CLTV when available, falling back to LTV
   - DTI: Smart bin-aware matching via build_dti_match_expr()
4. Term binning: FHLMC non-standard terms are binned to nearest FHFA standard
   term (360, 180, 240, 120, 480 months) - FHFA does this but doesn't document it
5. Edge case handling: Amounts on exact $10k boundaries are duplicated
   to try both adjacent bins
6. Mutual best-match scoring to resolve duplicates
7. Match quality tier assignment for confidence assessment

Expected match rates: ~83% FHLMC / ~85% FHFA (2019-2024)
"""

from __future__ import annotations

from functools import cache

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_fhfa.config import (
    CROSSWALK_OUTPUT_DIR,
    get_fhfa_config,
    get_fhlmc_config,
)
from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
    STATE_FIPS,
    MatchingTolerances,
    build_dti_match_expr,
)

logger = get_logger(__name__)


@cache
def get_md_to_cbsa() -> dict[int, int]:
    """Load (lazily, cached) the Metropolitan Division → CBSA mapping.

    The crosswalk covers both 2020 and 2023 OMB delineation vintages, providing 40
    MD-to-CBSA mappings for the 13 metros with divisions. Loaded on first use rather
    than at import time, so a missing crosswalk only affects the FHLMC-FHFA matcher
    rather than breaking import of the package / unified CLI.

    Returns:
        Mapping from MD code to parent CBSA code

    Raises:
        FileNotFoundError: if the Census crosswalk file is not present.
    """
    crosswalk_path = MortgageDataConfig.SCHEMAS_DIR / "census" / "md_to_cbsa_crosswalk.csv"
    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Census MD-to-CBSA crosswalk not found at {crosswalk_path}. "
            "Download from Census Bureau delineation files."
        )
    df = pl.read_csv(crosswalk_path)
    return dict(df.select("md_code", "cbsa_code").iter_rows())


# FHLMC-specific tolerances (different from FNMA)
# FHFA rounds Freddie rates to hundredths, but FHLMC uses eighth-percent precision
# e.g., FHLMC 7.125% -> FHFA 7.12% or 7.13%
# A rate tolerance of 0.01 (1 basis point) captures this rounding
FHLMC_DEFAULT_TOLERANCES = MatchingTolerances(
    rate_tolerance=0.01,  # 1 bp for FHFA rounding (FHFA rounds Freddie rates to hundredths)
    amount_tolerance=0.0,  # Exact match - amounts pre-rounded to FHFA bins
    post_amount_tolerance=0.0,  # Match amount_tolerance for validation
    ltv_tolerance=1.0,  # 1% tolerance
    dti_tolerance=1.0,  # 1% for exact range (36-49); also controls bin 30 boundary
)


# FHFA standard loan terms (months)
# FHFA bins all FHLMC non-standard terms to these values (undocumented behavior)
# See docs/matching/mbs_fhfa_matching.md for details
FHFA_STANDARD_TERMS = [360, 180, 240, 120, 480]

# Pre-computed mapping of any term to nearest FHFA standard term
# Used to bin FHLMC non-standard terms before matching
TERM_TO_FHFA_BIN = {t: min(FHFA_STANDARD_TERMS, key=lambda x: abs(x - t)) for t in range(1, 600)}


def round_to_fhfa_bin(amount: pl.Expr) -> pl.Expr:
    """Round loan amount to FHFA's $10k bin midpoint.

    FHFA reports loan amounts as $10k bin midpoints:
    - $175,000 represents loans from $170,000-$179,999
    - $165,000 represents loans from $160,000-$169,999

    Formula: floor(amount / 10000) * 10000 + 5000
    Example: $171,000 -> $175,000; $168,000 -> $165,000

    Args:
        amount: Polars expression for loan amount

    Returns:
        Rounded loan amount at FHFA bin midpoint
    """
    return ((amount / 10000).floor() * 10000 + 5000).cast(pl.Float64)


def is_bin_edge(amount: pl.Expr) -> pl.Expr:
    """Check if loan amount is exactly on a $10k boundary (ambiguous bin).

    Amounts exactly at $10k multiples (e.g., $170,000) are ambiguous because
    they could reasonably round to either adjacent bin ($165k or $175k).

    Args:
        amount: Polars expression for loan amount

    Returns:
        Boolean expression indicating if amount is on a bin boundary
    """
    return (amount % 10000) == 0


def load_fhfa_data(
    year: int,
) -> pl.LazyFrame:
    """Load FHFA sf_c data for Freddie Mac loans as a LazyFrame.

    Args:
        year: Year to load

    Returns:
        FHFA data filtered to Freddie Mac (enterprise_flag=2)
    """
    fhfa_config = get_fhfa_config()
    fhfa_file = fhfa_config.FHFA_SILVER_DIR / "sf_c" / f"sf_c_{year}.parquet"

    if not fhfa_file.exists():
        raise FileNotFoundError(f"FHFA file not found: {fhfa_file}")

    logger.debug("Scanning FHFA data from %s...", fhfa_file)

    # Use scan_parquet for lazy loading
    lf = pl.scan_parquet(fhfa_file)

    # Filter to Freddie Mac (enterprise_flag = 2)
    lf = lf.filter(pl.col("enterprise_flag") == 2)

    return lf


def load_fhlmc_data(
    year: int,
    include_prior_year: bool = True,
) -> pl.LazyFrame:
    """Load FHLMC origination data for a given year as a LazyFrame.

    Args:
        year: Year to load
        include_prior_year: If True, also load prior year's files to support matching FHFA's
            prior-year originations (date_of_mortgage_note=2). Default True.

    Returns:
        FHLMC origination data for the specified year(s)
    """
    fhlmc_config = get_fhlmc_config()
    origination_dir = fhlmc_config.FHLMC_BRONZE_DIR / "origination"

    # Find quarterly files for the year (and optionally prior year)
    years_to_load = [year]
    if include_prior_year:
        years_to_load.append(year - 1)

    all_files = []
    for y in years_to_load:
        files = sorted(origination_dir.glob(f"historical_data_{y}Q*.parquet"))
        # Also include the non-standard (ARM / relief-refi) loans: historical_data_excl_{y}Q*.
        # The standard pattern cannot match these (the "excl_" infix breaks it), so this is
        # purely additive. They share the origination layout (sans the trailing field, handled
        # by diagonal_relaxed) and the F##Q#/A##Q# loan-id year encoding used below.
        files += sorted(origination_dir.glob(f"historical_data_excl_{y}Q*.parquet"))
        all_files.extend(files)

    if not all_files:
        raise FileNotFoundError(
            f"No FHLMC files found for year(s) {years_to_load} in {origination_dir}"
        )

    logger.debug("Scanning %d FHLMC quarterly files for %s...", len(all_files), years_to_load)

    # Scan and concat all quarterly files lazily
    lf = pl.concat([pl.scan_parquet(f) for f in all_files], how="diagonal_relaxed")

    return lf


def prepare_fhfa_for_matching(df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Prepare FHFA data for matching by selecting and standardizing columns.

    Works with both DataFrame and LazyFrame inputs.

    Args:
        df: Raw FHFA sf_c data

    Returns:
        Prepared FHFA data with standardized columns
    """
    # Select and rename columns for matching
    result = df.select(
        [
            pl.col("record_number").alias("fhfa_record_id"),
            pl.col("enterprise_flag").alias("fhfa_enterprise"),
            pl.col("state_code").alias("state_fips"),
            pl.col("msa_code").alias("msa"),
            pl.col("application_channel").alias("channel"),  # 1=Retail, 2=Broker, 3=Correspondent
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
    result = result.with_columns(
        pl.when(pl.col("msa") == 99999).then(0).otherwise(pl.col("msa")).alias("msa")
    )

    # Cast to consistent types
    result = result.with_columns(
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

    return result


def prepare_fhlmc_for_matching(df: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame | pl.LazyFrame:
    """Prepare FHLMC data for matching by selecting, transforming, and standardizing columns.

    Works with both DataFrame and LazyFrame inputs.

    Args:
        df: Raw FHLMC origination data

    Returns:
        Prepared FHLMC data with standardized columns matching FHFA format
    """
    # Select columns
    # Note: We use CLTV instead of LTV because FHFA's "LTV at origination" field
    # actually reports "Combined LTV (CLTV) where available" per their data dictionary.
    # This means FHFA uses CLTV when there's a subordinate lien, otherwise LTV.
    # To match properly, we use FHLMC's CLTV when available, falling back to LTV.
    result = df.select(
        [
            pl.col("Loan Sequence Number").alias("fhlmc_loan_id"),
            pl.col("Property State").alias("state_abbr"),
            pl.col("Metropolitan Statistical Area (MSA) Or Metropolitan Division").alias("msa"),
            pl.col("Channel").alias("channel_str"),  # R=Retail, B=Broker, C=Correspondent
            pl.col("Original Interest Rate").alias("interest_rate"),
            pl.col("Original UPB").alias("loan_amount"),
            pl.col("Original Loan-to-Value (LTV)").alias("ltv_raw"),
            pl.col("Original Combined Loan-to-Value (CLTV)").alias("cltv"),
            pl.col("Original Debt-to-Income (DTI) Ratio").alias("dti"),
            pl.col("Original Loan Term").alias("term"),
            pl.col("Number of Units").alias("num_units"),
            pl.col("Number of Borrowers").alias("num_borrowers"),
            pl.col("First Time Homebuyer Flag").alias("fthb_str"),
            pl.col("Loan Purpose").alias("purpose_str"),
            pl.col("Occupancy Status").alias("occupancy_str"),
            pl.col("Property Type").alias("property_str"),
            pl.col("First Payment Date").alias("first_payment_date"),
        ]
    )

    # Use CLTV when available, otherwise fall back to LTV
    # This matches FHFA's behavior of reporting "CLTV where available"
    result = result.with_columns(pl.coalesce(pl.col("cltv"), pl.col("ltv_raw")).alias("ltv"))

    # Map state abbreviation to FIPS code
    result = result.with_columns(
        pl.col("state_abbr").replace_strict(STATE_FIPS, default=None).alias("state_fips")
    )

    # Map MSA: 0 or null for non-metro
    result = result.with_columns(
        pl.when(pl.col("msa").is_null() | (pl.col("msa") == 0))
        .then(0)
        .otherwise(pl.col("msa"))
        .alias("msa")
    )

    # Map Metropolitan Division codes to parent MSA codes
    # FHLMC uses MD codes but FHFA uses MSA codes
    result = result.with_columns(
        pl.col("msa").replace_strict(get_md_to_cbsa(), default=pl.col("msa")).alias("msa")
    )

    # Map channel: R=1 (Retail), B=2 (Broker), C=3 (Correspondent) to match FHFA Freddie coding
    # Note: FHFA uses channel=9 for Correspondent in Fannie Mae, but channel=3 for Freddie Mac
    result = result.with_columns(
        pl.when(pl.col("channel_str") == "R")
        .then(1)
        .when(pl.col("channel_str") == "B")
        .then(2)
        .when(pl.col("channel_str") == "C")
        .then(3)
        .otherwise(None)
        .alias("channel")
    )

    # Map first-time home buyer: Y=1, N=2 (to match FHFA coding)
    result = result.with_columns(
        pl.when(pl.col("fthb_str") == "Y")
        .then(1)
        .when(pl.col("fthb_str") == "N")
        .then(2)
        .otherwise(None)
        .alias("fthb")
    )

    # Map loan purpose: P=1 (Purchase), R=2 (Refi), C=7 (Cash-out), N=2 (No cash-out refi)
    result = result.with_columns(
        pl.when(pl.col("purpose_str") == "P")
        .then(1)
        .when(pl.col("purpose_str") == "R")
        .then(2)
        .when(pl.col("purpose_str") == "N")
        .then(2)  # No cash-out refi = refi
        .when(pl.col("purpose_str") == "C")
        .then(7)
        .otherwise(None)
        .alias("loan_purpose")
    )

    # Map occupancy: P=1 (Primary), S=2 (Second), I=3 (Investment)
    result = result.with_columns(
        pl.when(pl.col("occupancy_str") == "P")
        .then(1)
        .when(pl.col("occupancy_str") == "S")
        .then(2)
        .when(pl.col("occupancy_str") == "I")
        .then(3)
        .otherwise(None)
        .alias("occupancy_code")
    )

    # Map property type: SF/PU=1 (Site-built), MH=2 (Manufactured), CO/CP=1
    result = result.with_columns(
        pl.when(pl.col("property_str").is_in(["SF", "PU", "CO", "CP"]))
        .then(1)
        .when(pl.col("property_str") == "MH")
        .then(2)
        .otherwise(None)
        .alias("property_type")
    )

    # Extract origination year from loan ID
    # Format: F{YY}Q{Q}{sequence}, e.g., F23Q10031582 = 2023 Q1
    result = result.with_columns(
        (pl.col("fhlmc_loan_id").str.slice(1, 2).cast(pl.Int64) + 2000).alias("origination_year")
    )

    # Cap num_borrowers at 2 to match FHFA (FHFA only reports 1 or 2)
    result = result.with_columns(
        pl.when(pl.col("num_borrowers") > 2)
        .then(2)
        .otherwise(pl.col("num_borrowers"))
        .alias("num_borrowers")
    )

    # Cast numeric columns
    result = result.with_columns(
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

    # Bin loan term to nearest FHFA standard term
    # FHFA bins all FHLMC non-standard terms (e.g., 300mo -> 360mo) but this
    # behavior is undocumented. Pre-binning improves match rates by ~1.3pp.
    result = result.with_columns(
        pl.col("term").replace_strict(TERM_TO_FHFA_BIN, default=pl.col("term")).alias("term")
    )

    # Add rounded loan amount for FHFA bin matching
    # FHFA reports amounts at $10k bin midpoints (e.g., $175k = $170k-$179.9k)
    result = result.with_columns(
        round_to_fhfa_bin(pl.col("loan_amount")).alias("loan_amount_rounded")
    )

    # Flag edge cases (amounts exactly on $10k boundaries) for later duplication
    result = result.with_columns(is_bin_edge(pl.col("loan_amount")).alias("is_edge_case"))

    # Select final columns
    # Note: ltv is actually CLTV where available (matching FHFA's field definition)
    result = result.select(
        [
            "fhlmc_loan_id",
            "state_fips",
            "msa",
            "channel",
            "interest_rate",
            "loan_amount",
            "loan_amount_rounded",
            "is_edge_case",
            "ltv",  # CLTV where available, otherwise LTV (matches FHFA definition)
            "dti",
            "term",
            "num_units",
            "num_borrowers",
            "fthb",
            "loan_purpose",
            "occupancy_code",
            "property_type",
            "origination_year",
        ]
    )

    return result


def expand_edge_cases(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Expand edge-case records by duplicating loans on $10k boundaries.

    Amounts exactly at $10k multiples (e.g., $170,000) are ambiguous because
    they could round to either adjacent bin. This function creates two copies:
    - One rounded down to lower bin midpoint ($165,000)
    - One rounded up to upper bin midpoint ($175,000)

    Non-edge-case loans pass through unchanged.

    Args:
        lf: FHLMC data with loan_amount_rounded and is_edge_case columns

    Returns:
        Expanded data with edge cases duplicated
    """
    # Non-edge cases: keep as-is
    non_edge = lf.filter(~pl.col("is_edge_case"))

    # Edge cases: create two versions
    edge_cases = lf.filter(pl.col("is_edge_case"))

    # Version 1: Round down to lower bin (e.g., $170k -> $165k)
    edge_lower = edge_cases.with_columns(
        (pl.col("loan_amount_rounded") - 10000).alias("loan_amount_rounded")
    )

    # Version 2: Round up to upper bin (e.g., $170k -> $175k)
    # Already at midpoint from round_to_fhfa_bin, which gives $175k for $170k
    # So we need to use the original rounded value (no change needed)
    edge_upper = edge_cases

    # Combine all
    return pl.concat([non_edge, edge_lower, edge_upper], how="diagonal_relaxed")


def match_fhlmc_fhfa(
    fhlmc_lf: pl.LazyFrame,
    fhfa_lf: pl.LazyFrame,
    tolerances: MatchingTolerances | None = None,
) -> pl.LazyFrame:
    """Match FHLMC loans to FHFA records using multi-field matching (fully lazy).

    Uses a single join approach with Polars optimization:
    1. Expand edge-case amounts (exact $10k multiples) to try both adjacent bins
    2. Join on exact match fields (state, MSA, channel, units, borrowers, FTHB,
       purpose, occupancy, property type, term, loan_amount_rounded)
    3. Filter using numeric tolerances (rate, LTV, DTI)
    4. Resolve duplicates via mutual best-match scoring

    Args:
        fhlmc_lf: Prepared FHLMC data
        fhfa_lf: Prepared FHFA data
        tolerances: Tolerance configuration. If None, uses FHLMC_DEFAULT_TOLERANCES.

    Returns:
        Matched loan pairs with all relevant columns
    """
    if tolerances is None:
        tolerances = FHLMC_DEFAULT_TOLERANCES

    # Expand edge cases (amounts on $10k boundaries get duplicated to try both bins)
    fhlmc_expanded = expand_edge_cases(fhlmc_lf)

    # Prepare FHFA for joining on rounded loan amount
    # Rename FHFA loan_amount to loan_amount_rounded for the join
    fhfa_lf = fhfa_lf.with_columns(pl.col("loan_amount").alias("loan_amount_rounded"))

    # Exact match fields
    # MSA included after mapping FHLMC Metropolitan Division codes to parent CBSAs
    # using the Census Bureau crosswalk (schemas/census/md_to_cbsa_crosswalk.csv)
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
        "loan_amount_rounded",  # Pre-rounded to FHFA $10k bins
    ]

    # Drop orig_year_flag if present (not needed for join)
    fhfa_cols = fhfa_lf.collect_schema().names()
    if "orig_year_flag" in fhfa_cols:
        fhfa_lf = fhfa_lf.drop("orig_year_flag")

    # Join on exact fields
    merged = fhlmc_expanded.join(
        fhfa_lf,
        on=exact_match_cols,
        how="inner",
        suffix="_fhfa",
    )

    # Apply numeric tolerances

    # Interest rate tolerance
    merged = merged.filter(
        (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs() <= tolerances.rate_tolerance
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
    amount_weight = 1.0 / tolerances.amount_tolerance if tolerances.amount_tolerance > 0 else 1000.0
    ltv_weight = 1.0 / tolerances.ltv_tolerance if tolerances.ltv_tolerance > 0 else 1000.0
    dti_weight = 1.0 / tolerances.dti_tolerance if tolerances.dti_tolerance > 0 else 1000.0

    merged = merged.with_columns(
        [
            (
                ((pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs() * rate_weight)
                + ((pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs() * amount_weight)
                + ((pl.col("ltv") - pl.col("ltv_fhfa")).abs() * ltv_weight).fill_null(0)
                + ((pl.col("dti") - pl.col("dti_fhfa")).abs() * dti_weight).fill_null(0)
            ).alias("match_score")
        ]
    )

    # Rank each pair within FHLMC and FHFA groups
    merged = merged.with_columns(
        [
            pl.col("match_score").rank(method="dense").over("fhlmc_loan_id").alias("rank_in_fhlmc"),
            pl.col("match_score").rank(method="dense").over("fhfa_record_id").alias("rank_in_fhfa"),
        ]
    )

    # Keep only mutual best matches (best for both sides)
    unique_matches = merged.filter((pl.col("rank_in_fhlmc") == 1) & (pl.col("rank_in_fhfa") == 1))

    # Ensure 1:1 by checking for remaining duplicates
    unique_matches = unique_matches.with_columns(
        [
            pl.len().over("fhlmc_loan_id").alias("fhlmc_count"),
            pl.len().over("fhfa_record_id").alias("fhfa_count"),
        ]
    )

    unique_matches = unique_matches.filter(
        (pl.col("fhlmc_count") == 1) & (pl.col("fhfa_count") == 1)
    )

    # Compute match quality tiers
    unique_matches = unique_matches.with_columns(
        [
            (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs().alias("_rate_diff"),
            (pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs().alias("_amount_diff"),
            (pl.col("ltv") - pl.col("ltv_fhfa")).abs().fill_null(0).alias("_ltv_diff"),
        ]
    )

    # Match quality tiers for FHLMC (adjusted for FHFA rate rounding)
    # Note: FHFA rounds Freddie rates to hundredths, so rate_diff <= 0.01 is considered "near-exact"
    unique_matches = unique_matches.with_columns(
        pl.when(
            (pl.col("_rate_diff") <= 0.01)
            & (pl.col("_amount_diff") == 0)
            & (pl.col("_ltv_diff") == 0)
        )
        .then(1)
        .when((pl.col("_rate_diff") <= 0.01) & (pl.col("_ltv_diff") == 0))
        .then(2)
        .when(pl.col("_rate_diff") <= 0.01)
        .then(3)
        .otherwise(4)
        .alias("match_quality_tier")
    )

    # Drop temporary columns and edge case flag
    unique_matches = unique_matches.drop(
        [
            "_rate_diff",
            "_amount_diff",
            "_ltv_diff",
            "match_score",
            "rank_in_fhlmc",
            "rank_in_fhfa",
            "fhlmc_count",
            "fhfa_count",
            "is_edge_case",
            "loan_amount_rounded",
        ]
    )

    return unique_matches


def match_fhlmc_fhfa_by_year(
    fhlmc_lf: pl.LazyFrame,
    fhfa_lf: pl.LazyFrame,
    acquisition_year: int,
    tolerances: MatchingTolerances | None = None,
) -> pl.LazyFrame:
    """Match FHLMC loans to FHFA records with year-based pre-filtering.

    Uses FHFA's orig_year_flag (date_of_mortgage_note) to split matching:
    - orig_year_flag=1: same year as acquisition -> match to FHLMC from acquisition year
    - orig_year_flag=2: prior year -> match to FHLMC from year before acquisition

    This significantly reduces the candidate pool and eliminates cross-year false matches.

    Args:
        fhlmc_lf: Prepared FHLMC data
        fhfa_lf: Prepared FHFA data (must include orig_year_flag column)
        acquisition_year: The FHFA acquisition year
        tolerances: Tolerance configuration.

    Returns:
        Matched loan pairs
    """
    if tolerances is None:
        tolerances = FHLMC_DEFAULT_TOLERANCES

    # Same-year originations (flag=1) -> match to FHLMC from acquisition year
    fhfa_same = fhfa_lf.filter(pl.col("orig_year_flag") == 1)
    fhlmc_same = fhlmc_lf.filter(pl.col("origination_year") == acquisition_year)
    matches_same = match_fhlmc_fhfa(fhlmc_same, fhfa_same, tolerances)

    # Prior-year originations (flag=2) -> match to FHLMC from year before acquisition
    fhfa_prior = fhfa_lf.filter(pl.col("orig_year_flag") == 2)
    fhlmc_prior = fhlmc_lf.filter(pl.col("origination_year") == acquisition_year - 1)
    matches_prior = match_fhlmc_fhfa(fhlmc_prior, fhfa_prior, tolerances)

    # Combine matches from both year groups
    combined = pl.concat([matches_same, matches_prior], how="diagonal_relaxed")

    return combined


def run_fhlmc_fhfa_matching(
    year: int = 2023,
    tolerances: MatchingTolerances | None = None,
) -> pl.DataFrame:
    """Run the complete FHLMC-FHFA matching pipeline for a single year.

    Uses LazyFrames throughout and only materializes when writing output.

    Args:
        year: Year to match (default: 2023)
        tolerances: Tolerance configuration. If None, uses FHLMC_DEFAULT_TOLERANCES.
            Note: FHLMC uses different tolerances than FNMA because FHFA rounds
            Freddie Mac rates to hundredths (e.g., 7.125% -> 7.12% or 7.13%).

    Returns:
        Matched loan pairs (collected for return value)
    """
    if tolerances is None:
        tolerances = FHLMC_DEFAULT_TOLERANCES

    # Ensure output directory exists
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("FHLMC-FHFA matching for %d", year)
    logger.info(
        "Tolerances: rate +/-%s%%, amount +/-$%s, LTV +/-%s%%, DTI +/-%s%%",
        tolerances.rate_tolerance,
        f"{tolerances.amount_tolerance:,.0f}",
        tolerances.ltv_tolerance,
        tolerances.dti_tolerance,
    )

    # Load data as LazyFrames
    fhfa_raw = load_fhfa_data(year)
    fhlmc_raw = load_fhlmc_data(year, include_prior_year=True)

    # Prepare for matching (still lazy)
    fhfa_prepared = prepare_fhfa_for_matching(fhfa_raw)
    fhlmc_prepared = prepare_fhlmc_for_matching(fhlmc_raw)

    logger.debug("Building match query (lazy)...")

    # Run matching with year-based filtering (still lazy)
    matches_lf = match_fhlmc_fhfa_by_year(fhlmc_prepared, fhfa_prepared, year, tolerances)

    logger.debug("Executing match query...")

    # Collect matches once for crosswalk, stats, and return value
    matches = matches_lf.collect()
    match_count = len(matches)

    # Write crosswalk with acq_year for deduplication later
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk_file = CROSSWALK_OUTPUT_DIR / f"fhlmc_fhfa_crosswalk_{year}.parquet"
    matches.select(
        [
            "fhlmc_loan_id",
            "fhfa_record_id",
            pl.col("year").alias("fhfa_year"),
            "fhfa_enterprise",
        ]
    ).write_parquet(crosswalk_file)

    logger.info("Crosswalk saved to: %s", crosswalk_file)

    # Collect input counts for stats
    fhlmc_count = (
        fhlmc_prepared.filter(
            (pl.col("origination_year") == year) | (pl.col("origination_year") == year - 1)
        )
        .select(pl.len())
        .collect()
        .item()
    )
    fhfa_count = fhfa_prepared.select(pl.len()).collect().item()

    fhlmc_match_rate = match_count / fhlmc_count * 100 if fhlmc_count > 0 else 0
    fhfa_match_rate = match_count / fhfa_count * 100 if fhfa_count > 0 else 0

    logger.info("Results: FHLMC records (year %d + %d): %s", year, year - 1, f"{fhlmc_count:,}")
    logger.info("FHFA records: %s", f"{fhfa_count:,}")
    logger.info("Total matches: %s", f"{match_count:,}")
    logger.info("FHLMC match rate: %.1f%%", fhlmc_match_rate)
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
        tier_desc = {
            1: "Near-exact rate+amount+LTV",
            2: "Near-exact rate+LTV",
            3: "Near-exact rate only",
            4: "Wider tolerance",
        }
        logger.info(
            "  Tier %s (%s): %s (%.1f%%)", tier, tier_desc.get(tier, "Unknown"), f"{count:,}", pct
        )

    return matches


def run_fhlmc_fhfa_matching_multi_year(
    min_year: int = 2019,
    max_year: int = 2024,
    tolerances: MatchingTolerances | None = None,
) -> None:
    """Run FHLMC-FHFA matching for multiple years.

    Args:
        min_year: First year to process
        max_year: Last year to process (inclusive)
        tolerances: Tolerance configuration.
    """
    results = []

    for year in range(min_year, max_year + 1):
        logger.info("Processing year %d", year)

        try:
            df = run_fhlmc_fhfa_matching(year, tolerances)
            results.append({"year": year, "matches": len(df), "status": "success"})
        except FileNotFoundError as e:
            logger.warning("Skipping year %d: %s", year, e)
            results.append({"year": year, "matches": 0, "status": f"skipped: {e}"})
        except Exception as e:
            logger.error("Error processing year %d: %s", year, e)
            results.append({"year": year, "matches": 0, "status": f"error: {e}"})

    logger.info("Summary")
    for r in results:
        logger.info("  %s: %s matches (%s)", r["year"], f"{r['matches']:,}", r["status"])


# Legacy API for backward compatibility
def match_fhfa_fhlmc(file_fhlmc, file_fhfa, save_folder, file_suffix=""):
    """Legacy function signature - use run_fhlmc_fhfa_matching() instead."""
    raise NotImplementedError(
        "This legacy API is deprecated. Use run_fhlmc_fhfa_matching(year=YYYY) instead."
    )
