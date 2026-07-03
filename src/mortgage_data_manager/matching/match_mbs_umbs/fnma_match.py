#!/usr/bin/env python3
"""Fannie Mae (FNMA) specific MBS matching logic using Polars."""

# Standard library imports

from __future__ import annotations

import datetime
from pathlib import Path

# Third-party imports
import polars as pl

# Local application imports
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_umbs.config import (
    CROSSWALK_OUTPUT_DIR,
    MBSUMBSConfig,
    get_umbs_bronze_dir,
)

logger = get_logger(__name__)


def match_fnma_mbs_umbs(
    mbs_dir: Path,
    umbs_dir: Path,
    crosswalk_file: Path,
    variable_file: Path,
    snapshot_file: Path | None = None,
    max_year: int = 2099,
    max_month: int = 12,
) -> pl.DataFrame:
    """Match Fannie Mae MBS loans to UMBS loans using standardized loan characteristics.

    Performs a three-round matching process: round 1 with mortgage insurance percentage
    (MIP) included, round 2 without MIP for remaining unmatched loans, and round 3 without
    the credit-score filter (MIP strict) to catch the VantageScore-4 / Classic-FICO
    score-type transition affecting FPD-2025+ vintages. Matches are based on exact
    loan purpose, state, amount, interest rate, and first payment date, with tolerance
    for small differences in LTV, CLTV, DTI, term, and credit scores.

    The UMBS side is the ILLD issuance disclosure, which begins ``UMBS_ISSUANCE_START``
    (2019-06). When ``snapshot_file`` is supplied, loans originated before then are recovered
    from the first monthly snapshot (FNM_MLLD_201906) — a snapshot of every loan still
    outstanding in a UMBS-eligible pool at that date — and the match window opens to pre-2019.
    The two UMBS sources are split by First Payment Date (ILLD >= start, snapshot < start) so
    they never overlap.

    Args:
        mbs_dir: Directory containing MBS parquet files (typically silver/issuances).
        umbs_dir: Directory containing UMBS ILLD parquet files.
        crosswalk_file: Output path for the final crosswalk parquet file.
        variable_file: Path to CSV file mapping MBS/UMBS columns to standardized names.
        snapshot_file: First monthly UMBS snapshot (FNM_MLLD) for pre-2019 recovery. If
            ``None``, only the post-2019 ILLD population is matched (legacy behavior).
        max_year: Last year to include in matching (default 2099).
        max_month: Last month to include in matching (default 12).

    Returns:
        DataFrame with columns 'Loan Sequence Number (MBS)' and 'Loan Sequence Number (UMBS)'
        containing matched loan pairs.
    """
    # Match Columns
    match_columns = [
        "Loan Sequence Number",
        "Amortization Type",
        "Credit Score 1",
        "Credit Score 2",
        "Channel",
        "Combined Loan-To-Value",
        "Debt-To-Income",
        "First Payment Date",
        "First Time Home Buyer Indicator",
        "Loan Purpose",
        "Loan Term",
        "Loan-To-Value",
        "Mortgage Insurance Percentage",
        "Mortgage Loan Amount",
        "Loan Size",
        "Number of Borrowers",
        "Number of Units",
        "Occupancy Status",
        "Original Interest Rate",
        "Property State",
        "Property Type",
        "Seller Name",
        "Servicer Name",
    ]

    # Read Variable Crosswalks
    variable_crosswalk = pl.read_csv(variable_file)

    umbs_rename_map = {
        row["umbs_column"]: row["standardized_name"]
        for row in variable_crosswalk.select(["umbs_column", "standardized_name"])
        .drop_nulls()
        .filter(pl.col("standardized_name").is_in(match_columns))
        .to_dicts()
    }

    mbs_rename_map = {
        row["mbs_column"]: row["standardized_name"]
        for row in variable_crosswalk.select(["mbs_column", "standardized_name"])
        .drop_nulls()
        .filter(pl.col("standardized_name").is_in(match_columns))
        .to_dicts()
    }

    logger.debug("UMBS rename map: %s", umbs_rename_map)
    logger.debug("MBS rename map: %s", mbs_rename_map)

    # Date window. ILLD issuance disclosure begins at issuance_start; with a snapshot we open
    # the lower bound to recover loans that predate ILLD. Upper bound is exclusive-of-next-month.
    issuance_start = datetime.datetime.fromisoformat(MBSUMBSConfig.UMBS_ISSUANCE_START)
    if max_month == 12:
        last_date = datetime.datetime(max_year + 1, 1, 1)
    else:
        last_date = datetime.datetime(max_year, max_month + 1, 1)
    mbs_first_date = datetime.datetime(1900, 1, 1) if snapshot_file is not None else issuance_start

    # Import MBS data (glob on directory). missing/extra_columns handle schema differences.
    mbs_pattern = str(mbs_dir / "**/*.parquet")
    lf1 = pl.scan_parquet(mbs_pattern, missing_columns="insert", extra_columns="ignore")
    lf1 = lf1.select(list(mbs_rename_map.keys())).rename(mbs_rename_map)
    # MBS First Payment Date may be a string "MMYYYY" (legacy bronze) or already a Date
    # (current silver, which parses packed date encodings to pl.Date). Parse only when it
    # is still a string, then window it.
    if lf1.collect_schema()["First Payment Date"] != pl.Date:
        lf1 = lf1.with_columns(
            pl.col("First Payment Date")
            .cast(pl.String)
            .str.zfill(6)
            .str.to_date(format="%m%Y")
            .cast(pl.Date)
        )
    lf1 = lf1.filter(
        (pl.col("First Payment Date") >= mbs_first_date)
        & (pl.col("First Payment Date") < last_date)
    )

    def _load_umbs(
        pattern: str, fpd_lo: datetime.datetime, fpd_hi: datetime.datetime
    ) -> pl.LazyFrame:
        """Scan a UMBS source, standardize columns, parse First Payment Date, window [lo, hi)."""
        lf = pl.scan_parquet(pattern, missing_columns="insert", extra_columns="ignore")
        lf = lf.select(list(umbs_rename_map.keys())).rename(umbs_rename_map)
        return lf.with_columns(
            pl.col("First Payment Date")
            .cast(pl.String)
            .str.zfill(6)
            .str.to_date(format="%m%Y")
            .cast(pl.Date)
        ).filter((pl.col("First Payment Date") >= fpd_lo) & (pl.col("First Payment Date") < fpd_hi))

    # UMBS issuance side: ILLD covers [issuance_start, last_date); the first monthly snapshot
    # recovers the pre-issuance-start population still outstanding at the snapshot date. The two
    # sources are split on First Payment Date so they never overlap.
    lf2 = _load_umbs(str(umbs_dir / "**/*.parquet"), issuance_start, last_date)
    if snapshot_file is not None:
        lf2_snapshot = _load_umbs(str(snapshot_file), mbs_first_date, issuance_start)
        lf2 = pl.concat([lf2, lf2_snapshot], how="diagonal_relaxed")
        logger.info(
            "Combined UMBS source: ILLD (>= %s) + snapshot %s (< %s)",
            issuance_start.date(),
            snapshot_file.name,
            issuance_start.date(),
        )

    # Drop Loan Modifications
    lf2 = lf2.filter(pl.col("Loan Purpose") != "M")

    # Drop UMBS Loan Duplicates
    lf2 = lf2.unique(subset=["Loan Sequence Number"])

    # Harmonize Loan Purpose Notation for Match
    lf2 = lf2.with_columns(
        pl.when(pl.col("Loan Purpose") == "N")
        .then(pl.lit("R"))
        .otherwise(pl.col("Loan Purpose"))
        .alias("Loan Purpose")
    )

    # Convert Strings to Numerics
    numeric_cols = [
        "Combined Loan-To-Value",
        "Credit Score 1",
        "Credit Score 2",
        "Debt-To-Income",
        "Loan Term",
        "Loan-To-Value",
        "Mortgage Insurance Percentage",
        "Number of Borrowers",
        "Number of Units",
        # Exact-match join keys: cast so dtypes line up across ILLD/snapshot/MBS — the
        # MLLD snapshot stores Mortgage Loan Amount as f64 while issuances use i64.
        "Mortgage Loan Amount",
        "Original Interest Rate",
    ]
    # Cast both sides: the difference filters below subtract (MBS) from (UMBS),
    # so both frames must be numeric. UMBS bronze can store these as strings
    # (schema drift), which otherwise raises "arithmetic on string and numeric".
    lf1 = lf1.with_columns(
        [
            pl.col(c).cast(pl.Float64, strict=False)
            for c in numeric_cols
            if c in lf1.collect_schema().names()
        ]
    )
    lf2 = lf2.with_columns(
        [
            pl.col(c).cast(pl.Float64, strict=False)
            for c in numeric_cols
            if c in lf2.collect_schema().names()
        ]
    )

    logger.info("Number of MBS observations: %s", lf1.select(pl.len()).collect().item())
    logger.info("Number of UMBS observations: %s", lf2.select(pl.len()).collect().item())

    # Join logic for matches
    def apply_match_logic(l_frame, r_frame, include_mip=True, include_credit_score=True):
        """Apply matching logic to join MBS and UMBS loan data.

        Joins on exact loan purpose, state, amount, interest rate, and first payment date,
        then filters to matches with similar loan characteristics (LTV, CLTV, DTI, term,
        credit scores, channel, occupancy, property type, borrower count, units). Deduplicates
        to ensure one-to-one matches only.

        Args:
            l_frame: LazyFrame containing MBS loan data with standardized column names.
            r_frame: LazyFrame containing UMBS loan data with standardized column names.
            include_mip: If True, require exact match on mortgage insurance percentage.
            include_credit_score: If True, require credit-score agreement (Credit Score 1
                within 1, or Credit Score 2 exact). Set False in round 3 to handle the
                VantageScore/FICO score-type transition.

        Returns:
            LazyFrame with matched loan pairs containing 'Loan Sequence Number (MBS)' and
            'Loan Sequence Number (UMBS)' columns.
        """
        join_keys = [
            "Loan Purpose",
            "Property State",
            "Mortgage Loan Amount",
            "Original Interest Rate",
            "First Payment Date",
        ]

        # Merge
        merged = l_frame.join(r_frame, on=join_keys, how="inner", suffix=" (UMBS)")

        # Rename LHS cols to (MBS)
        shared = set(l_frame.collect_schema().names()).intersection(
            r_frame.collect_schema().names()
        )
        for k in join_keys:
            shared.discard(k)
        merged = merged.rename({name: f"{name} (MBS)" for name in shared})

        # Apply differences filters
        conditions = [
            (pl.col("Loan-To-Value (MBS)") - pl.col("Loan-To-Value (UMBS)")).abs() <= 1,
            (pl.col("Combined Loan-To-Value (MBS)") - pl.col("Combined Loan-To-Value (UMBS)")).abs()
            <= 1,
            (pl.col("Debt-To-Income (MBS)") - pl.col("Debt-To-Income (UMBS)")).abs() <= 1,
            (pl.col("Number of Borrowers (MBS)") - pl.col("Number of Borrowers (UMBS)")).abs() == 0,
            (pl.col("Number of Units (MBS)") - pl.col("Number of Units (UMBS)")).abs() == 0,
            (pl.col("Loan Term (MBS)") - pl.col("Loan Term (UMBS)")).abs() <= 6,
            # Exact matches
            pl.col("Channel (MBS)") == pl.col("Channel (UMBS)"),
            pl.col("Occupancy Status (MBS)") == pl.col("Occupancy Status (UMBS)"),
            pl.col("First Time Home Buyer Indicator (MBS)")
            == pl.col("First Time Home Buyer Indicator (UMBS)"),
            pl.col("Property Type (MBS)") == pl.col("Property Type (UMBS)"),
        ]

        if include_credit_score:
            # Credit Score checks (Credit Score 2 is MBS-only, so no suffix)
            conditions.append(
                ((pl.col("Credit Score 1 (MBS)") - pl.col("Credit Score 1 (UMBS)")).abs() <= 1)
                | (pl.col("Credit Score 2") == pl.col("Credit Score 1 (UMBS)"))
            )

        if include_mip:
            conditions.append(
                (
                    pl.col("Mortgage Insurance Percentage (MBS)")
                    - pl.col("Mortgage Insurance Percentage (UMBS)")
                ).abs()
                == 0
            )

        res = merged.filter(pl.all_horizontal(conditions))

        # Deduplicate matches
        res = res.with_columns(
            [
                pl.len().over("Loan Sequence Number (MBS)").alias("Count (MBS)"),
                pl.len().over("Loan Sequence Number (UMBS)").alias("Count (UMBS)"),
            ]
        ).filter((pl.col("Count (MBS)") == 1) & (pl.col("Count (UMBS)") == 1))

        return res.select(["Loan Sequence Number (MBS)", "Loan Sequence Number (UMBS)"])

    # Round 1: exact match with MIP and credit score
    crosswalk_r1 = apply_match_logic(lf1, lf2, include_mip=True)
    df_cw_r1 = crosswalk_r1.collect()
    logger.info("Round 1 matches (MIP + CS): %s", f"{len(df_cw_r1):,}")

    # Round 2: Match without MIP (still require credit score)
    lf1_unmatched = lf1.join(
        df_cw_r1.lazy(),
        left_on="Loan Sequence Number",
        right_on="Loan Sequence Number (MBS)",
        how="anti",
    )
    lf2_unmatched = lf2.join(
        df_cw_r1.lazy(),
        left_on="Loan Sequence Number",
        right_on="Loan Sequence Number (UMBS)",
        how="anti",
    )

    crosswalk_r2 = apply_match_logic(lf1_unmatched, lf2_unmatched, include_mip=False)
    df_cw_r2 = crosswalk_r2.collect()
    logger.info("Round 2 matches (no MIP, with CS): %s", f"{len(df_cw_r2):,}")

    # Round 3: Match WITHOUT credit score (handles the VantageScore-4 / Classic-FICO
    # score-type transition affecting FPD-2025+ vintages, where MBS and UMBS disclose
    # different score types for the same loan). MIP is kept strict to limit false positives.
    df_cw_r1_r2 = pl.concat([df_cw_r1, df_cw_r2], how="diagonal")
    lf1_unmatched_r3 = lf1.join(
        df_cw_r1_r2.lazy(),
        left_on="Loan Sequence Number",
        right_on="Loan Sequence Number (MBS)",
        how="anti",
    )
    lf2_unmatched_r3 = lf2.join(
        df_cw_r1_r2.lazy(),
        left_on="Loan Sequence Number",
        right_on="Loan Sequence Number (UMBS)",
        how="anti",
    )
    crosswalk_r3 = apply_match_logic(
        lf1_unmatched_r3, lf2_unmatched_r3, include_mip=True, include_credit_score=False
    )
    df_cw_r3 = crosswalk_r3.collect()
    logger.info("Round 3 matches (no CS, with MIP): %s", f"{len(df_cw_r3):,}")

    # Combine Matches
    df_crosswalk = pl.concat([df_cw_r1, df_cw_r2, df_cw_r3], how="diagonal")

    # Final conversion to string for ID columns before saving
    df_crosswalk = df_crosswalk.with_columns(
        [
            pl.col("Loan Sequence Number (MBS)").cast(pl.String),
            pl.col("Loan Sequence Number (UMBS)").cast(pl.String),
        ]
    )

    logger.info("Number of matched observations: %s", df_crosswalk.select(pl.len()).item())

    df_crosswalk.write_parquet(crosswalk_file)
    return df_crosswalk


def explore_fnma_credit_scores(cw_fnma):
    """Experiment with using expanded VantageScore 4 credit score data in the matching process.

    Loads Fannie Mae's VantageScore 4 historical credit score data for both MBS and UMBS
    loans, joins it with the crosswalk, and examines discrepancies in credit scores between
    matched loan pairs. This is an exploratory function for validating matches.

    Args:
        cw_fnma: DataFrame or LazyFrame containing the FNMA crosswalk with columns
            'Loan Sequence Number (MBS)' and 'Loan Sequence Number (UMBS)'.

    Returns:
        None. Logs diagnostic information about credit score mismatches.
    """
    import zipfile

    # Set this path to your FNMA credit scores data directory
    data_folder = "/path/to/data/securities_data/fnma/raw_files/credit_scores"
    zip_file_mbs = f"{data_folder}/FNM_VantageScore4_HistoricalScores_LoanPerformance.zip"
    zip_file_umbs = f"{data_folder}/FNM_VantageScore4_HistoricalScores_MBS.zip"

    def read_zip_csv(zip_path):
        with zipfile.ZipFile(zip_path, "r") as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                # Polars can read from a file object
                return pl.read_csv(f, separator="|", dtypes={"loan_identifier": pl.String})

    df_umbs = read_zip_csv(zip_file_umbs)
    df_mbs = read_zip_csv(zip_file_mbs)

    cw_fnma_df = cw_fnma if isinstance(cw_fnma, pl.DataFrame) else cw_fnma.collect()

    # Bring in Credit Score Data
    temp = cw_fnma_df.join(df_mbs, left_on="Loan Sequence Number (MBS)", right_on="loan_identifier")
    temp = temp.join(
        df_umbs, left_on="Loan Sequence Number (UMBS)", right_on="loan_identifier", suffix="_UMBS"
    )
    # Note: Column names will be like 'vs4_...' and 'vs4_..._UMBS'

    # Identify mirrored columns and rename original to _MBS
    mismatch_cols = [c for c in temp.columns if c.endswith("_UMBS")]
    base_cols = [c[:-5] for c in mismatch_cols]

    temp = temp.rename({c: f"{c}_MBS" for c in base_cols if c in temp.columns})

    # Examine Mismatched Observations (example column from original script)
    if "vs4_bimerge_highest_MBS" in temp.columns and "vs4_bimerge_highest_UMBS" in temp.columns:
        mismatched = temp.filter(
            pl.col("vs4_bimerge_highest_MBS") != pl.col("vs4_bimerge_highest_UMBS")
        )
        logger.info("%s mismatched observations out of %s total.", len(mismatched), len(temp))


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")

    # Get paths from config
    # Use silver/issuances (one row per loan) instead of bronze (multiple monthly snapshots per loan)
    mbs_dir = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
    umbs_dir = get_umbs_bronze_dir() / "FNMA" / "FNM_ILLD"
    snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file("fnma")

    # Ensure output directory exists
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk_file = CROSSWALK_OUTPUT_DIR / "fnma_crosswalk.parquet"

    # Variable mapping file is in the same directory as this script
    variable_file = Path(__file__).parent / "fnma_column_mapping.csv"

    logger.info("MBS data directory: %s", mbs_dir)
    logger.info("UMBS data directory: %s", umbs_dir)
    logger.info("UMBS snapshot file: %s", snapshot_file)
    logger.info("Output crosswalk: %s", crosswalk_file)
    logger.info("Variable mapping: %s", variable_file)

    cw_fnma = match_fnma_mbs_umbs(
        mbs_dir=mbs_dir,
        umbs_dir=umbs_dir,
        crosswalk_file=crosswalk_file,
        variable_file=variable_file,
        snapshot_file=snapshot_file,
    )

    logger.info("Matching complete. Crosswalk saved to: %s", crosswalk_file)
    logger.info("Total matches: %s", len(cw_fnma))
