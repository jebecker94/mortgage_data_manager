"""Core matching logic for FHLB data.

Uses lazy evaluation with Polars LazyFrames for memory efficiency.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.reference import to_planning_region

from . import config

logger = get_logger(__name__)


def apply_post2018_filters(lf: pl.LazyFrame, round_number: int = 1) -> pl.LazyFrame:
    """Apply standard filters for post-2018 FHLB matching.

    Args:
        lf: Joined HMDA-FHLB data
        round_number: Matching round (1=strict, 2=relaxed tolerances)

    Returns:
        Filtered matches
    """
    settings = config.MATCH_SETTINGS
    income_tol = settings["income_tol"][f"round{round_number}"]
    ltv_tol = settings["ltv_tol"][f"round{round_number}"]
    dti_tol = settings["dti_tol"][f"round{round_number}"]
    rate_tol = settings["rate_tol"]
    term_tol = settings["term_tol"]

    # Clean sentinel values (-99999 -> null)
    lf = lf.with_columns([
        pl.when(pl.col("income") == -99999)
            .then(None)
            .otherwise(pl.col("income"))
            .cast(pl.Float64)
            .alias("income"),
        pl.when(pl.col("loan_term") == -99999)
            .then(None)
            .otherwise(pl.col("loan_term"))
            .cast(pl.Float64)
            .alias("loan_term"),
        pl.when(pl.col("interest_rate") == -99999)
            .then(None)
            .otherwise(pl.col("interest_rate"))
            .cast(pl.Float64)
            .alias("interest_rate"),
        pl.when(pl.col("combined_loan_to_value_ratio") == -99999)
            .then(None)
            .otherwise(pl.col("combined_loan_to_value_ratio"))
            .cast(pl.Float64)
            .alias("combined_loan_to_value_ratio"),
        pl.when(pl.col("debt_to_income_ratio") == -99999)
            .then(None)
            .otherwise(pl.col("debt_to_income_ratio"))
            .cast(pl.Float64)
            .alias("debt_to_income_ratio"),
    ])

    # Purpose filters - exclude mismatched purposes
    # Original: ~((purpose==1) & LoanPurposeType.is_in([6,2])) AND ~(purpose.is_in([31,32]) & (LoanPurposeType==1))
    lf = lf.filter(
        ~((pl.col("loan_purpose") == 1) & pl.col("LoanPurposeType").is_in([6, 2]))
    ).filter(
        ~(pl.col("loan_purpose").is_in([31, 32]) & (pl.col("LoanPurposeType") == 1))
    )

    # Income filter - original used negation which excludes nulls
    lf = lf.filter(
        ~((pl.col("TotalMonthlyIncomeAmount") - pl.col("income")).abs() > income_tol)
    )

    # Term filter
    lf = lf.filter(
        ~((pl.col("LoanAmortizationMaxTermMonths") - pl.col("loan_term")).abs() > term_tol)
    )

    # Rate filter
    lf = lf.filter(
        ~((pl.col("NoteRatePercent") - pl.col("interest_rate")).abs() > rate_tol)
    )

    # LTV filter
    lf = lf.filter(
        ~((pl.col("LTVRatioPercent") - pl.col("combined_loan_to_value_ratio")).abs() > ltv_tol)
    )

    # DTI filter with special handling for 36-50 range
    lf = lf.filter(
        ~(
            ((pl.col("TotalDebtExpenseRatioPercent") - pl.col("debt_to_income_ratio")).abs() > dti_tol) &
            (pl.col("debt_to_income_ratio") >= 36) &
            (pl.col("debt_to_income_ratio") < 50)
        )
    )

    # Gender filters - exclude contradictory gender
    lf = lf.filter(
        ~((pl.col("applicant_sex") == 1) & (pl.col("Borrower1SexType") == 2))
    ).filter(
        ~((pl.col("applicant_sex") == 2) & (pl.col("Borrower1SexType") == 1))
    ).filter(
        ~((pl.col("co_applicant_sex") == 1) & (pl.col("Borrower2SexType") == 2))
    ).filter(
        ~((pl.col("co_applicant_sex") == 2) & (pl.col("Borrower2SexType") == 1))
    )

    # Round 2: additional amount tolerance
    if round_number == 2:
        amount_tol = settings["amount_tol"]
        lf = lf.filter(
            ~((pl.col("loan_amount") - pl.col("NoteAmount")).abs() > amount_tol)
        )

    # Keep only unique 1:1 matches
    lf = (
        lf
        .with_columns([
            pl.col("LoanCharacteristicsID").count().over("LoanCharacteristicsID").alias("count_fhlb"),
            pl.col("HMDAIndex").count().over("HMDAIndex").alias("count_hmda"),
        ])
        .filter((pl.col("count_hmda") == 1) & (pl.col("count_fhlb") == 1))
        .drop(["count_fhlb", "count_hmda"])
    )

    return lf


def _load_all_hmda_originations(max_year: int) -> pl.LazyFrame:
    """Load all HMDA originations (not restricted to FHLB members).

    Used by Round 3 open matching to find correspondent originators
    who sell through FHLB member intermediaries.

    Args:
        max_year: Maximum year to load.

    Returns:
        LazyFrame of all HMDA originations with action_taken=1 and
        purchaser_type != 3 (excludes GNMA).
    """
    hmda_conf = config.get_hmda_manager_config()
    hmda_dir = hmda_conf.HMDAConfig.get_dataset_dir("silver", "loans", "post2018")

    hmda_cols = [
        "HMDAIndex", "activity_year", "lei", "census_tract", "loan_amount",
        "loan_type", "total_units", "occupancy_type", "loan_purpose", "income",
        "loan_term", "interest_rate", "applicant_sex", "co_applicant_sex",
        "combined_loan_to_value_ratio", "debt_to_income_ratio", "purchaser_type",
    ]

    frames = []
    for year in range(2018, max_year + 1):
        year_path = hmda_dir / f"activity_year={year}"
        if not year_path.exists():
            continue

        file_type_dirs = sorted([
            d for d in year_path.iterdir()
            if d.is_dir() and "file_type" in d.name
        ])
        target_path = file_type_dirs[0] if file_type_dirs else year_path

        lf = (
            pl.scan_parquet(target_path / "*.parquet")
            .filter(
                (pl.col("action_taken") == 1)
                & (pl.col("purchaser_type") != 3)
            )
            .select(hmda_cols)
        )
        frames.append(lf)

    if not frames:
        raise FileNotFoundError(f"No HMDA silver data found in {hmda_dir}")

    # Normalize CT tracts to planning-region convention (HMDA switched in 2024)
    # so they line up with the FHLB side after its own normalization.
    return to_planning_region(pl.concat(frames, how="diagonal"), tract_col="census_tract")


def match_pre2018(match_folder: Path, crosswalk_folder: Path | None = None) -> pl.DataFrame:
    """Execute FHLB matching for pre-2018 data.

    Args:
        match_folder: Directory containing cleaned data files
        crosswalk_folder: Directory for crosswalk outputs. Defaults to config.CROSSWALK_OUTPUT_DIR.

    Returns:
        Matched records
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    logger.info("Matching HMDA to FHLB (pre-2018)")

    settings = config.MATCH_SETTINGS
    income_tol = settings["income_tol"]["pre2018"]

    # Load data lazily
    hmda = pl.scan_parquet(match_folder / "hmda_fhlb_members_pre2018.parquet")
    fhlb = pl.scan_parquet(match_folder / "fhlb_acquisitions_pre2018.parquet")

    # Prepare FHLB columns for join
    fhlb = fhlb.with_columns([
        (pl.col("Round Loan Amount") * 1000).cast(pl.Int64).alias("Round Loan Amount"),
        pl.col("data_year").cast(pl.Int64),
    ])

    # Join on key fields
    lf = hmda.join(
        fhlb,
        left_on=["activity_year", "census_tract", "loan_amount"],
        right_on=["data_year", "census_tract", "Round Loan Amount"],
        how="inner",
    )

    # Purpose filter - flexible mapping for pre-2018 coding mismatch
    # FHLB type 2 (refinance) encompasses HMDA types 2 (refi) and 3 (home improvement)
    # Many home improvement loans are structured as cash-out refinances, so FHLB classifies them as type 2
    purpose_filter = (
        (pl.col("Loan Purpose Type") == pl.col("loan_purpose")) |  # Exact match (1→1, 2→2)
        ((pl.col("Loan Purpose Type") == 2) & (pl.col("loan_purpose") == 3))  # FHLB refi → HMDA home improvement
    )

    # Income filter
    income_filter = (
        (pl.col("Total Yearly Income Amount") - pl.col("income").cast(pl.Float64)).abs() <= income_tol
    )

    # Gender filters
    gender_filter = ~(
        ((pl.col("applicant_sex") == 1) & (pl.col("Borrower 1 Gender Type") == 2)) |
        ((pl.col("applicant_sex") == 2) & (pl.col("Borrower 1 Gender Type") == 1)) |
        ((pl.col("co_applicant_sex") == 1) & (pl.col("Borrower 2 Gender Type") == 2)) |
        ((pl.col("co_applicant_sex") == 2) & (pl.col("Borrower 2 Gender Type") == 1))
    )

    lf = lf.filter(purpose_filter & income_filter & gender_filter)

    # FHLB district match (if column exists)
    # Polars adds _right suffix for duplicate column names
    lf = lf.filter(
        (pl.col("FHFBID") == pl.col("FHFBID_right")) |
        pl.col("FHFBID_right").is_null() |
        (pl.col("FHFBID_right") == 0)
    )

    # Keep unique 1:1 matches
    # Pre-2018: LoanCharacteristicsID is unique within year (need year + ID)
    # After join, FHLB's data_year becomes activity_year (same column, joined on it)
    lf = (
        lf
        .with_columns([
            pl.col("LoanCharacteristicsID").count().over(["LoanCharacteristicsID", "activity_year"]).alias("count_fhlb"),
            pl.col("HMDAIndex").count().over("HMDAIndex").alias("count_hmda"),
        ])
        .filter((pl.col("count_hmda") == 1) & (pl.col("count_fhlb") == 1))
        .drop(["count_fhlb", "count_hmda"])
    )

    # Final income validation (stricter)
    lf = lf.filter(
        (pl.col("Total Yearly Income Amount") - pl.col("income")).abs() <= 1000
    )

    # Exclude known GSE purchasers
    lf = lf.filter(~pl.col("purchaser_type").is_in([1, 2, 3]))

    # Collect results
    df = lf.collect()

    # Save outputs
    df.write_parquet(match_folder / "fhlb_hmda_matches_pre2018.parquet")

    # Crosswalk with unique IDs
    # Pre-2018 needs year + ID (ID reused across years)
    # Post-2018 only needs ID (globally unique)
    crosswalk = df.select([
        "HMDAIndex",
        "LoanCharacteristicsID",
        "activity_year",  # Same as data_year after join
    ])
    crosswalk.write_parquet(crosswalk_folder / "fhlb_hmda_crosswalk_pre2018.parquet")

    logger.info(f"Pre-2018 matching complete: {len(df):,} matches")
    return df


def match_post2018(match_folder: Path, crosswalk_folder: Path | None = None) -> pl.DataFrame:
    """Execute FHLB matching for post-2018 data (2 rounds).

    Args:
        match_folder: Directory containing cleaned data files
        crosswalk_folder: Directory for crosswalk outputs. Defaults to config.CROSSWALK_OUTPUT_DIR.

    Returns:
        Matched records from both rounds
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)

    logger.info("Matching HMDA to FHLB (post-2018) - Round 1")

    # Load data lazily. Normalize CT tracts to planning-region convention on both
    # sides: HMDA switched to planning regions in 2024, while FHLB AMA reports a
    # mix of both conventions for CT — without this, the exact census_tract join
    # silently drops CT records whose two sides disagree. Idempotent (PR/non-CT
    # tracts pass through). See investigation_ct_planning_region_*_2026-06-10.md.
    hmda = to_planning_region(
        pl.scan_parquet(match_folder / "hmda_fhlb_members_post2018.parquet"),
        tract_col="census_tract",
    )
    fhlb = to_planning_region(
        pl.scan_parquet(match_folder / "fhlb_acquisitions_post2018.parquet"),
        tract_col="Census Tract String",
    )

    # Get pre-match counts for logging
    fhlb_counts = fhlb.group_by("data_year").len().sort("data_year").collect()
    logger.debug(f"FHLB observations by year: {fhlb_counts.to_dict()}")

    # Round 1: Strict matching with loan amount
    lf = hmda.join(
        fhlb,
        left_on=["activity_year", "census_tract", "loan_amount", "loan_type", "total_units", "occupancy_type"],
        right_on=["NoteDate", "Census Tract String", "Round Loan Amount", "Loan Type", "PropertyUnitCount", "PropertyUsageType"],
        how="inner",
    )

    lf = apply_post2018_filters(lf, round_number=1)

    # Ensure Round Loan Amount exists (may be dropped by join)
    lf = lf.with_columns(pl.col("loan_amount").alias("Round Loan Amount"))

    # Collect round 1 results
    df_round1 = lf.collect()

    # Save round 1 crosswalk
    crosswalk1 = df_round1.select(["HMDAIndex", "LoanCharacteristicsID"]).with_columns(
        pl.lit(1).cast(pl.UInt8).alias("MatchRound")
    )
    crosswalk1.write_parquet(crosswalk_folder / "fhlb_hmda_crosswalk_post2018.parquet")

    # Log match rates
    match_counts = df_round1.group_by("data_year").len().sort("data_year").rename({"len": "matches"})
    stats = fhlb_counts.join(match_counts, on="data_year", how="left").fill_null(0)
    stats = stats.with_columns((pl.col("matches") / pl.col("len")).alias("match_rate"))

    for row in stats.iter_rows(named=True):
        logger.info(
            f"Round 1 - Year {row['data_year']}: {row['matches']:,} matches "
            f"({row['match_rate']:.1%} of {row['len']:,} FHLB loans)"
        )

    logger.info(f"Round 1 complete: {len(df_round1):,} matches")

    # Round 2: Relaxed matching without loan amount
    logger.info("Matching HMDA to FHLB (post-2018) - Round 2")

    # Remove already matched records
    hmda_unmatched = hmda.join(
        crosswalk1.lazy().select("HMDAIndex"),
        on="HMDAIndex",
        how="anti",
    )
    fhlb_unmatched = fhlb.join(
        crosswalk1.lazy().select("LoanCharacteristicsID"),
        on="LoanCharacteristicsID",
        how="anti",
    )

    # Join without loan amount
    lf2 = hmda_unmatched.join(
        fhlb_unmatched,
        left_on=["activity_year", "census_tract", "loan_type", "total_units", "occupancy_type"],
        right_on=["NoteDate", "Census Tract String", "Loan Type", "PropertyUnitCount", "PropertyUsageType"],
        how="inner",
    )

    lf2 = apply_post2018_filters(lf2, round_number=2)
    df_round2 = lf2.collect()

    logger.info(f"Round 2 complete: {len(df_round2):,} matches")

    # Combine R1+R2 crosswalks
    crosswalk2 = df_round2.select(["HMDAIndex", "LoanCharacteristicsID"]).with_columns(
        pl.lit(2).cast(pl.UInt8).alias("MatchRound")
    )
    crosswalk_r1r2 = pl.concat([crosswalk1, crosswalk2], how="diagonal")
    crosswalk_r1r2.write_parquet(crosswalk_folder / "fhlb_hmda_crosswalk_post2018_round2.parquet")

    # Round 3: Open matching against all HMDA originations (not just FHLB members)
    # Catches correspondent originators who sell through FHLB member intermediaries.
    # See investigations/reports/investigation_fhlb_ama_open_matching_2026-03-07.md
    logger.info("Matching HMDA to FHLB (post-2018) - Round 3 (open)")

    hmda_all = _load_all_hmda_originations(
        max_year=fhlb_counts["data_year"].max(),
    )

    # Exclude HMDA and FHLB records already matched in R1+R2
    hmda_open = hmda_all.join(
        crosswalk_r1r2.lazy().select("HMDAIndex"),
        on="HMDAIndex",
        how="anti",
    )
    fhlb_open = fhlb.join(
        crosswalk_r1r2.lazy().select("LoanCharacteristicsID"),
        on="LoanCharacteristicsID",
        how="anti",
    )

    # R1-style join (strict, with loan amount)
    lf3 = hmda_open.join(
        fhlb_open,
        left_on=["activity_year", "census_tract", "loan_amount", "loan_type", "total_units", "occupancy_type"],
        right_on=["NoteDate", "Census Tract String", "Round Loan Amount", "Loan Type", "PropertyUnitCount", "PropertyUsageType"],
        how="inner",
    )

    lf3 = apply_post2018_filters(lf3, round_number=1)
    df_round3 = lf3.collect()

    logger.info(f"Round 3 (open) complete: {len(df_round3):,} matches")

    # Determine FHLB membership for R3 matches
    fhlb_member_leis = set(
        pl.read_parquet(crosswalk_folder / "lender_fhlb_crosswalk_post2018.parquet")["LEI"]
        .unique()
        .to_list()
    )
    crosswalk3 = df_round3.select(["HMDAIndex", "LoanCharacteristicsID"]).with_columns([
        pl.lit(3).cast(pl.UInt8).alias("MatchRound"),
    ])
    crosswalk3.write_parquet(crosswalk_folder / "fhlb_hmda_crosswalk_post2018_round3.parquet")

    n_member_r3 = df_round3.filter(pl.col("lei").is_in(fhlb_member_leis)).height
    n_nonmember_r3 = len(df_round3) - n_member_r3
    logger.info(
        f"Round 3 breakdown: {n_member_r3:,} from FHLB members, "
        f"{n_nonmember_r3:,} from non-members (correspondent originators)"
    )

    # Combine all crosswalks
    crosswalk_all = pl.concat([crosswalk_r1r2, crosswalk3], how="diagonal")
    crosswalk_all.write_parquet(crosswalk_folder / "fhlb_hmda_crosswalk_post2018_all.parquet")

    # Combine all matches (align columns)
    all_dfs = [df_round1, df_round2, df_round3]
    common_cols = sorted(set.intersection(*(set(df.columns) for df in all_dfs)))
    result = pl.concat([df.select(common_cols) for df in all_dfs], how="diagonal")

    result.write_parquet(match_folder / "fhlb_hmda_matches_post2018.parquet")

    logger.info(f"Total post-2018 matches: {len(result):,}")

    # Log purchaser type distribution
    if "purchaser_type" in result.columns:
        ptype_counts = result.group_by("purchaser_type").len().sort("purchaser_type")
        total = ptype_counts["len"].sum()
        ptype_counts = ptype_counts.with_columns((pl.col("len") / total).alias("pct"))
        logger.debug(f"Purchaser type distribution: {ptype_counts.to_dict()}")

    return result
