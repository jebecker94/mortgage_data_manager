"""FHA-specific HMDA-GNMA matching for Ginnie Mae securitized loans.

This module provides two methods for linking HMDA FHA loans to GNMA securitization:

1. Two-crosswalk method: HMDA → FHA-HMDA crosswalk → FHA → FHA-GNMA crosswalk → GNMA
   - Uses existing crosswalks from match_fha_hmda and match_fha_gnma workflows
   - Achieves ~34.5% coverage (2.54M of 7.36M HMDA FHA loans)

2. Direct matching method: HMDA → GNMA (probabilistic record linkage)
   - Bypasses FHA intermediate step
   - Uses LEI→Issuer mapping derived from two-crosswalk matches
   - Adds ~16% additional coverage (1.2M additional matches)

Combined coverage: ~50.7% of HMDA FHA loans linked to GNMA securitization data.
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

# HMDA file type priority by year (a > b > c)
HMDA_FILE_TYPE_PRIORITY: dict[int, str] = {
    2018: "a",
    2019: "a",
    2020: "a",
    2021: "a",
    2022: "b",
    2023: "b",
    2024: "c",
}

# US state codes
STATES: list[str] = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "PR",
    "VI",
    "GU",
    "AS",
    "MP",
]


def _log(msg: str) -> None:
    """Emit a progress message through the module logger."""
    logger.info(msg)


def build_two_crosswalk_chain(
    min_year: int = 2018,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Build HMDA → FHA → GNMA crosswalk using the two-crosswalk method.

    This method chains:
    1. HMDA → FHA (via FHA-HMDA crosswalk from match_fha_hmda)
    2. FHA → GNMA (via FHA-GNMA crosswalk from match_fha_gnma)

    Args:
        min_year: Start year (default 2018)
        max_year: End year (default 2024)

    Returns:
        DataFrame with HMDA FHA loans and their FHA/GNMA match information
    """
    _log("Building HMDA → FHA → GNMA two-crosswalk chain")

    # Load HMDA-FHA merged data
    hmda_fha_path = Config.HMDA_FHA_MERGED_PATH
    if not hmda_fha_path.exists():
        msg = f"HMDA-FHA merged data not found: {hmda_fha_path}"
        raise FileNotFoundError(msg)

    _log(f"Loading HMDA-FHA merged data from {hmda_fha_path}")

    hmda_fha = pl.read_parquet(hmda_fha_path)
    hmda_fha = hmda_fha.filter(
        (pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year)
    )

    _log(f"  HMDA FHA loans ({min_year}-{max_year}): {len(hmda_fha):,}")
    fha_matched = hmda_fha.filter(pl.col("FHA_Index").is_not_null()).height
    _log(f"  With FHA match: {fha_matched:,} ({fha_matched / len(hmda_fha) * 100:.1f}%)")

    # Load FHA-GNMA crosswalk
    fha_gnma_path = Config.FHA_GNMA_CROSSWALK_PATH
    if not fha_gnma_path.exists():
        msg = f"FHA-GNMA crosswalk not found: {fha_gnma_path}"
        raise FileNotFoundError(msg)

    _log(f"Loading FHA-GNMA crosswalk from {fha_gnma_path}")

    fha_gnma = pl.read_parquet(fha_gnma_path)

    # Filter to years of interest
    fha_gnma = fha_gnma.with_columns(
        pl.col("FHA_Index").str.slice(0, 4).cast(pl.Int32).alias("fha_year")
    )
    fha_gnma = fha_gnma.filter(
        (pl.col("fha_year") >= min_year) & (pl.col("fha_year") <= max_year)
    ).drop("fha_year")

    _log(f"  FHA-GNMA matches ({min_year}-{max_year}): {len(fha_gnma):,}")

    # Rename columns to avoid confusion
    fha_gnma = fha_gnma.rename(
        {"match_round": "fha_gnma_match_round", "match_score": "fha_gnma_match_score"}
    )

    # Join HMDA-FHA to FHA-GNMA
    hmda_fha_gnma = hmda_fha.join(fha_gnma, on="FHA_Index", how="left")

    # Rename HMDA-FHA match_round for clarity
    if "match_round" in hmda_fha_gnma.columns:
        hmda_fha_gnma = hmda_fha_gnma.rename({"match_round": "hmda_fha_match_round"})

    gnma_matched = hmda_fha_gnma.filter(pl.col("gnma_loan_id").is_not_null()).height
    _log(f"  Matched to GNMA: {gnma_matched:,} ({gnma_matched / len(hmda_fha_gnma) * 100:.1f}%)")

    return hmda_fha_gnma


def build_lei_issuer_crosswalk(
    validated: pl.DataFrame,
    gnma_dir: Path | None = None,
) -> pl.DataFrame:
    """Build LEI→Issuer mapping from validated two-crosswalk matches.

    This crosswalk enables direct matching by constraining which GNMA issuers
    an LEI (lender) is likely to sell loans to.

    Args:
        validated: DataFrame of validated GNMA matches with LEI and gnma_loan_id
        gnma_dir: Path to GNMA silver data (default: Config.GNMA_SILVER_DIR)

    Returns:
        DataFrame with columns: lei, primary_issuer, primary_concentration, top3_issuers
    """
    if gnma_dir is None:
        gnma_dir = Config.GNMA_SILVER_DIR

    _log("Building LEI→Issuer crosswalk from validated matches")

    gnma_ids = validated.select("gnma_loan_id").unique()

    _log(f"  Loading GNMA Issuer IDs for {len(gnma_ids):,} matched loans")

    # Load issuer IDs from GNMA files
    files = sorted(gnma_dir.glob("*.parquet"))
    frames = []

    for f in files:
        try:
            df = pl.read_parquet(f)
            seq_col = next(
                (c for c in df.columns if c.startswith("Disclosure Sequence Number")),
                None,
            )
            if seq_col is None:
                continue

            df = df.select(
                [
                    pl.col(seq_col).alias("gnma_loan_id"),
                    pl.col("Issuer ID (including for loan packages in MIP pool)").alias(
                        "issuer_id"
                    ),
                ]
            )
            df = df.join(gnma_ids, on="gnma_loan_id", how="inner")
            if len(df) > 0:
                frames.append(df)
        except Exception:
            continue

    gnma_issuers = pl.concat(frames, how="diagonal").unique(subset=["gnma_loan_id"], keep="first")

    _log(f"  Found issuer IDs for {len(gnma_issuers):,} loans")

    # Join with validated data
    validated_with_issuer = validated.join(gnma_issuers, on="gnma_loan_id", how="inner")

    # Focus on direct GNMA sales (purchaser_type=2)
    direct_sales = validated_with_issuer.filter(pl.col("purchaser_type") == 2)

    _log(f"  Direct GNMA sales (purchaser_type=2): {len(direct_sales):,}")

    # Count LEI-issuer pairs
    lei_issuer_counts = (
        direct_sales.group_by(["lei", "issuer_id"])
        .agg(pl.len().alias("n"))
        .sort(["lei", "n"], descending=[False, True])
    )

    # Get primary and top-3 issuers per LEI
    lei_primary = lei_issuer_counts.group_by("lei").agg(
        [
            pl.col("issuer_id").first().alias("primary_issuer"),
            pl.col("n").first().alias("primary_n"),
            pl.col("n").sum().alias("total_n"),
            pl.col("issuer_id").head(3).alias("top3_issuers"),
        ]
    )

    lei_primary = lei_primary.with_columns(
        (pl.col("primary_n") / pl.col("total_n")).alias("primary_concentration")
    )

    _log(f"  Unique LEIs with mappings: {len(lei_primary):,}")
    _log(f"  Median primary concentration: {lei_primary['primary_concentration'].median():.1%}")

    return lei_primary


def load_gnma_fha_loans(
    validated_gnma_ids: pl.Series,
    gnma_dir: Path | None = None,
    years: Sequence[int] | None = None,
) -> pl.DataFrame:
    """Load GNMA FHA loans, excluding validated matches.

    Args:
        validated_gnma_ids: Series of already-matched GNMA loan IDs to exclude
        gnma_dir: Path to GNMA silver data
        years: Years to include (default: 2018-2024)

    Returns:
        DataFrame of unmatched GNMA FHA loans with parsed fields
    """
    if gnma_dir is None:
        gnma_dir = Config.GNMA_SILVER_DIR

    if years is None:
        years = list(range(2018, 2025))

    _log("Loading all GNMA FHA loans...")

    # Build file patterns for years
    files = []
    for year in years:
        files.extend(sorted(gnma_dir.glob(f"dailyllmni_{year}*.parquet")))

    _log(f"  Found {len(files)} GNMA files for {min(years)}-{max(years)}")

    frames = []
    for f in sorted(files):
        try:
            df = pl.read_parquet(f)
            seq_col = next(
                (c for c in df.columns if c.startswith("Disclosure Sequence Number")),
                None,
            )
            if seq_col is None:
                continue

            # Filter to FHA only
            df = df.filter(pl.col("Agency (Agency Loan Type FHA, VA, RD, NA)") == "F")

            df = df.select(
                [
                    pl.col(seq_col).alias("gnma_loan_id"),
                    pl.col("Issuer ID (including for loan packages in MIP pool)").alias(
                        "issuer_id"
                    ),
                    pl.col("State (2 character State Code)").alias("gnma_state"),
                    pl.col("Original Principal Balance (OPB at pool issuance)").alias(
                        "gnma_loan_amount_raw"
                    ),
                    pl.col("Loan Interest Rate (current interest rate)").alias(
                        "gnma_interest_rate_raw"
                    ),
                    pl.col("Original Loan Term, in Months").alias("gnma_loan_term_raw"),
                    pl.col("Loan Purpose").alias("gnma_loan_purpose"),
                    pl.col("Loan Origination Date").alias("gnma_orig_date"),
                    pl.col("Combined LTV (CLTV)").alias("gnma_cltv_raw"),
                    pl.col("Total Debt Expense Ratio Percent").alias("gnma_dti_raw"),
                    pl.col("Property Type (Number of Living Units)").alias("gnma_units_raw"),
                    pl.col("Number of Borrowers").alias("gnma_num_borrowers_raw"),
                ]
            )

            frames.append(df)
        except Exception as e:
            _log(f"  Warning: {f.name}: {e}")
            continue

    gnma = pl.concat(frames, how="diagonal")

    _log(f"  Total GNMA FHA records: {len(gnma):,}")

    # Deduplicate
    gnma = gnma.unique(subset=["gnma_loan_id"], keep="first")

    _log(f"  Unique GNMA loans: {len(gnma):,}")

    # Exclude validated matches
    validated_df = pl.DataFrame({"gnma_loan_id": validated_gnma_ids})
    gnma = gnma.join(validated_df, on="gnma_loan_id", how="anti")

    _log(f"  After excluding validated: {len(gnma):,}")

    # Parse columns
    gnma = gnma.with_columns(
        [
            (pl.col("gnma_loan_amount_raw").cast(pl.Float64, strict=False) / 100).alias(
                "gnma_loan_amount"
            ),
            (pl.col("gnma_interest_rate_raw").cast(pl.Float64, strict=False) / 1000).alias(
                "gnma_interest_rate"
            ),
            pl.col("gnma_loan_term_raw").cast(pl.Int64, strict=False).alias("gnma_loan_term"),
            (pl.col("gnma_cltv_raw").cast(pl.Float64, strict=False) / 100).alias("gnma_cltv"),
            (pl.col("gnma_dti_raw").cast(pl.Float64, strict=False) / 100).alias("gnma_dti"),
            pl.col("gnma_units_raw").cast(pl.Int64, strict=False).alias("gnma_units"),
            pl.col("gnma_num_borrowers_raw")
            .cast(pl.Int64, strict=False)
            .alias("gnma_num_borrowers"),
            pl.col("gnma_orig_date")
            .str.slice(0, 4)
            .cast(pl.Int64, strict=False)
            .alias("gnma_orig_year"),
        ]
    )

    # Add blocking variables
    gnma = gnma.with_columns(
        [
            ((pl.col("gnma_loan_amount") / 10000).round() * 10000).alias("loan_amount_bin"),
            ((pl.col("gnma_interest_rate") * 8).round() / 8).alias("rate_block"),
            pl.when(pl.col("gnma_loan_purpose") == "1")
            .then(pl.lit("P"))
            .when(pl.col("gnma_loan_purpose").is_in(["2", "3"]))
            .then(pl.lit("R"))
            .otherwise(pl.lit("O"))
            .alias("purpose_cat"),
        ]
    )

    # Drop raw columns
    raw_cols = [c for c in gnma.columns if c.endswith("_raw")]
    gnma = gnma.drop(raw_cols)

    return gnma


def direct_match_fha(
    two_crosswalk_matches: pl.DataFrame,
    min_year: int = 2018,
    max_year: int = 2024,
    min_score: int = 5,
    exact_year: bool = True,
) -> pl.DataFrame:
    """Direct HMDA→GNMA matching for FHA loans using probabilistic record linkage.

    This method bypasses the two-crosswalk approach by directly matching HMDA FHA
    loans to GNMA securitization records using blocking and scoring.

    Args:
        two_crosswalk_matches: DataFrame from build_two_crosswalk_chain() with gnma_loan_id
        min_year: Start year
        max_year: End year
        min_score: Minimum match score threshold (5-10, default 5)
        exact_year: Require exact year match (True, recommended) or allow ±1 year (False)

    Returns:
        DataFrame of new direct matches (not in two_crosswalk_matches)
    """
    years = list(range(min_year, max_year + 1))

    _log("Direct HMDA-GNMA Matching for FHA Loans")
    _log(f"(Using {'EXACT' if exact_year else '±1'} year matching)")

    # Get validated GNMA matches
    validated = two_crosswalk_matches.filter(pl.col("gnma_loan_id").is_not_null())
    validated_hmda_ids = set(validated["HMDAIndex"].to_list())
    validated_gnma_ids = validated["gnma_loan_id"]

    _log(f"Two-crosswalk matches to exclude: {len(validated):,}")

    # Build LEI→Issuer crosswalk
    lei_issuer_map = build_lei_issuer_crosswalk(validated)

    # Load all unmatched GNMA data
    gnma_all = load_gnma_fha_loans(validated_gnma_ids, years=years)

    # Index GNMA by state
    gnma_by_state = {
        state: gnma_all.filter(pl.col("gnma_state") == state)
        for state in gnma_all["gnma_state"].unique().to_list()
    }
    del gnma_all
    gc.collect()

    # Process by year and state
    all_matches = []
    matched_hmda: set[str] = set()
    matched_gnma: set[str] = set()

    for year in years:
        _log(f"\nProcessing year {year}")

        year_matches = []
        states_with_matches = 0

        for state in STATES:
            if state not in gnma_by_state:
                continue

            gnma_state = gnma_by_state[state]
            if len(gnma_state) == 0:
                continue

            # Filter out already matched GNMA loans
            gnma_state_filtered = gnma_state.filter(~pl.col("gnma_loan_id").is_in(matched_gnma))
            if len(gnma_state_filtered) == 0:
                continue

            # Load HMDA for this year/state
            hmda_df = _load_hmda_year_state(
                year,
                state,
                validated_hmda_ids | matched_hmda,
                lei_issuer_map,
            )
            if hmda_df is None or len(hmda_df) == 0:
                continue

            # Match
            matches = _match_state_year(hmda_df, gnma_state_filtered, year, min_score, exact_year)

            if len(matches) > 0:
                # Deduplicate within state
                matches = _deduplicate_matches(matches)

                if len(matches) > 0:
                    year_matches.append(matches)
                    matched_hmda.update(matches["HMDAIndex"].to_list())
                    matched_gnma.update(matches["gnma_loan_id"].to_list())
                    states_with_matches += 1

            del hmda_df
            gc.collect()

        if year_matches:
            year_combined = pl.concat(year_matches, how="diagonal")
            year_combined = _deduplicate_matches(year_combined)
            year_combined = year_combined.with_columns(pl.lit(year).alias("match_year"))
            all_matches.append(year_combined)

            _log(f"  Year {year}: {len(year_combined):,} matches from {states_with_matches} states")

            matched_hmda.update(year_combined["HMDAIndex"].to_list())
            matched_gnma.update(year_combined["gnma_loan_id"].to_list())

    if not all_matches:
        _log("No new matches found!")
        return pl.DataFrame()

    # Combine and final deduplication
    combined = pl.concat(all_matches, how="diagonal")
    combined = _deduplicate_matches(combined)

    _log(f"\nTotal new direct matches: {len(combined):,}")
    _validate_matches(combined)

    return combined


def _load_hmda_year_state(
    year: int,
    state: str,
    exclude_hmda_ids: set[str],
    lei_issuer_map: pl.DataFrame,
) -> pl.DataFrame | None:
    """Load HMDA FHA loans for a specific year and state."""
    file_type = HMDA_FILE_TYPE_PRIORITY.get(year, "c")
    path = Config.HMDA_SILVER_DIR / f"activity_year={year}" / f"file_type={file_type}"

    if not path.exists():
        return None

    lf = pl.scan_parquet(f"{path}/*.parquet")

    # Filter to FHA loans in this state
    lf = lf.filter(
        (pl.col("loan_type") == 2) & (pl.col("action_taken") == 1) & (pl.col("state_code") == state)
    )

    # Select needed columns
    lf = lf.select(
        [
            "HMDAIndex",
            "activity_year",
            "lei",
            "state_code",
            "loan_amount",
            "interest_rate",
            "loan_term",
            "loan_purpose",
            "combined_loan_to_value_ratio",
            "debt_to_income_ratio",
            "total_units",
            "co_applicant_sex",
            "initially_payable_to_institution",
            "submission_of_application",
            "purchaser_type",
        ]
    )

    df = lf.collect()

    if len(df) == 0:
        return None

    # Filter out already matched
    df = df.filter(~pl.col("HMDAIndex").is_in(exclude_hmda_ids))

    if len(df) == 0:
        return None

    # Add blocking variables
    df = df.with_columns(
        [
            ((pl.col("loan_amount") / 10000).round() * 10000).alias("loan_amount_bin"),
            ((pl.col("interest_rate") * 8).round() / 8).alias("rate_block"),
            pl.when(pl.col("loan_purpose") == 1)
            .then(pl.lit("P"))
            .when(pl.col("loan_purpose").is_in([31, 32]))
            .then(pl.lit("R"))
            .otherwise(pl.lit("O"))
            .alias("purpose_cat"),
            pl.when(pl.col("co_applicant_sex").is_in([1, 2, 3]))
            .then(pl.lit(2))
            .otherwise(pl.lit(1))
            .alias("hmda_num_borrowers"),
            pl.col("total_units").cast(pl.Int64, strict=False).alias("hmda_units"),
            (
                (pl.col("initially_payable_to_institution") == 2)
                | (pl.col("submission_of_application") == 2)
            ).alias("is_broker_like"),
        ]
    )

    # Join LEI→Issuer mapping
    df = df.join(
        lei_issuer_map.select(["lei", "primary_issuer", "top3_issuers"]),
        on="lei",
        how="left",
    )

    return df


def _match_state_year(
    hmda_df: pl.DataFrame,
    gnma_state_df: pl.DataFrame,
    year: int,
    min_score: int = 5,
    exact_year: bool = True,
) -> pl.DataFrame:
    """Match HMDA and GNMA for a specific state and year."""
    if len(hmda_df) == 0 or len(gnma_state_df) == 0:
        return pl.DataFrame()

    # Filter GNMA to matching year
    if exact_year:
        gnma_filtered = gnma_state_df.filter(pl.col("gnma_orig_year") == year)
    else:
        gnma_filtered = gnma_state_df.filter(
            (pl.col("gnma_orig_year") >= year - 1) & (pl.col("gnma_orig_year") <= year + 1)
        )

    if len(gnma_filtered) == 0:
        return pl.DataFrame()

    # Blocking join
    matches = hmda_df.join(
        gnma_filtered,
        on=["loan_amount_bin", "rate_block", "purpose_cat"],
        how="inner",
        suffix="_gnma",
    )

    if len(matches) == 0:
        return pl.DataFrame()

    # Apply issuer constraint
    matches = matches.filter(
        pl.col("is_broker_like")
        | pl.col("primary_issuer").is_null()
        | (pl.col("issuer_id") == pl.col("primary_issuer"))
        | pl.col("top3_issuers").list.contains(pl.col("issuer_id"))
    )

    if len(matches) == 0:
        return pl.DataFrame()

    # Score candidates
    matches = matches.with_columns(
        [
            pl.when(pl.col("gnma_units") == pl.col("hmda_units"))
            .then(pl.lit(3))
            .otherwise(pl.lit(0))
            .alias("units_score"),
            pl.when(pl.col("gnma_num_borrowers") == pl.col("hmda_num_borrowers"))
            .then(pl.lit(2))
            .otherwise(pl.lit(0))
            .alias("borrowers_score"),
            pl.when(
                ((pl.col("debt_to_income_ratio") == 10) & (pl.col("gnma_dti") < 20))
                | (
                    (pl.col("debt_to_income_ratio") == 20)
                    & (pl.col("gnma_dti") >= 20)
                    & (pl.col("gnma_dti") < 30)
                )
                | (
                    (pl.col("debt_to_income_ratio") == 30)
                    & (pl.col("gnma_dti") >= 30)
                    & (pl.col("gnma_dti") < 36)
                )
                | (
                    (pl.col("debt_to_income_ratio") >= 36)
                    & (pl.col("debt_to_income_ratio") < 50)
                    & (pl.col("gnma_dti") >= pl.col("debt_to_income_ratio") - 0.5)
                    & (pl.col("gnma_dti") < pl.col("debt_to_income_ratio") + 0.5)
                )
                | (
                    (pl.col("debt_to_income_ratio") == 50)
                    & (pl.col("gnma_dti") >= 50)
                    & (pl.col("gnma_dti") < 60)
                )
                | ((pl.col("debt_to_income_ratio") == 60) & (pl.col("gnma_dti") >= 60))
            )
            .then(pl.lit(2))
            .otherwise(pl.lit(0))
            .alias("dti_score"),
            pl.when((pl.col("gnma_cltv") - pl.col("combined_loan_to_value_ratio")).abs() <= 5)
            .then(pl.lit(2))
            .otherwise(pl.lit(0))
            .alias("cltv_score"),
            pl.when(pl.col("gnma_loan_term") == pl.col("loan_term"))
            .then(pl.lit(1))
            .otherwise(pl.lit(0))
            .alias("term_score"),
        ]
    )

    matches = matches.with_columns(
        (
            pl.col("units_score")
            + pl.col("borrowers_score")
            + pl.col("dti_score")
            + pl.col("cltv_score")
            + pl.col("term_score")
        ).alias("total_score")
    )

    matches = matches.filter(pl.col("total_score") >= min_score)

    return matches


def _deduplicate_matches(matches: pl.DataFrame) -> pl.DataFrame:
    """Apply mutual-best-match deduplication."""
    if len(matches) == 0:
        return matches

    matches = matches.sort("total_score", descending=True)

    prev_count = -1
    while len(matches) != prev_count:
        prev_count = len(matches)
        matches = matches.unique(subset=["gnma_loan_id"], keep="first")
        matches = matches.unique(subset=["HMDAIndex"], keep="first")

    return matches


def _validate_matches(matches: pl.DataFrame) -> dict:
    """Validate match quality and print results."""
    if len(matches) == 0:
        return {"n": 0}

    _log("Validating match quality...")
    results = {"n": len(matches)}

    results["units_match"] = (matches["gnma_units"] == matches["hmda_units"]).mean()
    results["borrowers_match"] = (
        matches["gnma_num_borrowers"] == matches["hmda_num_borrowers"]
    ).mean()
    results["cltv_within_5"] = (
        (matches["gnma_cltv"] - matches["combined_loan_to_value_ratio"]).abs() <= 5
    ).mean()
    results["term_match"] = (matches["gnma_loan_term"] == matches["loan_term"]).mean()
    results["year_match"] = (matches["activity_year"] == matches["gnma_orig_year"]).mean()

    _log(f"  Units match:      {results['units_match']:.1%}")
    _log(f"  Borrowers match:  {results['borrowers_match']:.1%}")
    _log(f"  CLTV within 5%:   {results['cltv_within_5']:.1%}")
    _log(f"  Term match:       {results['term_match']:.1%}")
    _log(f"  Year exact match: {results['year_match']:.1%}")

    return results


def run_fha_matching_pipeline(
    min_year: int = 2018,
    max_year: int = 2024,
    include_direct: bool = True,
    exact_year: bool = True,
    output_dir: Path | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame | None]:
    """Run the complete FHA matching pipeline.

    This function:
    1. Builds the two-crosswalk chain (HMDA→FHA→GNMA)
    2. Optionally runs direct matching on unmatched loans
    3. Saves results to output files

    Args:
        min_year: Start year
        max_year: End year
        include_direct: Whether to run direct matching (adds ~16% coverage)
        exact_year: For direct matching, use exact year (recommended)
        output_dir: Output directory (default: Config.HMDA_MBS_OUTPUT_DIR)

    Returns:
        Tuple of (two_crosswalk_df, direct_matches_df)
    """
    if output_dir is None:
        output_dir = Config.HMDA_MBS_OUTPUT_DIR

    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Two-crosswalk chain
    two_crosswalk = build_two_crosswalk_chain(min_year, max_year)

    # Save two-crosswalk results
    two_crosswalk_path = output_dir / f"hmda_fha_gnma_two_crosswalk_{min_year}_{max_year}.parquet"
    two_crosswalk.write_parquet(two_crosswalk_path)

    _log(f"Saved two-crosswalk to {two_crosswalk_path}")

    # Step 2: Direct matching (optional)
    direct_matches = None
    if include_direct:
        direct_matches = direct_match_fha(
            two_crosswalk,
            min_year=min_year,
            max_year=max_year,
            exact_year=exact_year,
        )

        if len(direct_matches) > 0:
            suffix = "exact_year" if exact_year else "year_tolerance"
            direct_path = output_dir / f"hmda_gnma_direct_{suffix}_{min_year}_{max_year}.parquet"

            # Select output columns
            output_cols = [
                "HMDAIndex",
                "activity_year",
                "lei",
                "gnma_loan_id",
                "issuer_id",
                "state_code",
                "loan_amount",
                "interest_rate",
                "loan_term",
                "loan_purpose",
                "total_score",
                "units_score",
                "borrowers_score",
                "dti_score",
                "cltv_score",
                "term_score",
                "is_broker_like",
                "match_year",
            ]
            output_cols = [c for c in output_cols if c in direct_matches.columns]
            direct_output = direct_matches.select(output_cols)
            direct_output.write_parquet(direct_path)

            _log(f"Saved direct matches to {direct_path}")

    # Summary
    total = len(two_crosswalk)
    gnma_two = two_crosswalk.filter(pl.col("gnma_loan_id").is_not_null()).height
    gnma_direct = len(direct_matches) if direct_matches is not None else 0
    total_matched = gnma_two + gnma_direct

    _log("SUMMARY")
    _log(f"Total HMDA FHA loans ({min_year}-{max_year}): {total:,}")
    _log(f"Two-crosswalk matches: {gnma_two:,} ({gnma_two / total * 100:.1f}%)")
    if include_direct:
        _log(f"Direct matches: {gnma_direct:,} ({gnma_direct / total * 100:.1f}%)")
    _log(f"Total matched: {total_matched:,} ({total_matched / total * 100:.1f}%)")

    return two_crosswalk, direct_matches
