#!/usr/bin/env python3
"""Freddie Mac (FHLMC) specific MBS matching logic using Polars.

Matching Rounds:
    Round 1: Exact match on all criteria including Mortgage Insurance Percentage (MIP)
             and exact credit score match.

    Round 2: Same as Round 1, but relaxes the MIP requirement for remaining unmatched
             loans. Still requires exact credit score match.

    Round 3: Matches WITHOUT credit score validation. This round is intended to catch
             loans affected by the VantageScore 4.0 / Classic FICO transition that
             began in late 2024/early 2025. During this transition period, MBS and UMBS
             disclosures may report different credit score types (VantageScore vs FICO),
             resulting in score differences of 20-100+ points for the same loan.
             This round requires all other criteria to match exactly (MIP included)
             to minimize false positives when credit score is not used as a filter.

             See: docs/matching/mbs_umbs_matching.md for details.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_mbs_umbs.config import (
    CROSSWALK_OUTPUT_DIR,
    MBSUMBSConfig,
    get_fhlmc_config,
    get_umbs_bronze_dir,
)

logger = get_logger(__name__)


def match_fhlmc_mbs_umbs(
    mbs_dir: Path,
    umbs_dir: Path,
    crosswalk_file: Path,
    variable_file: Path,
    snapshot_file: Path | None = None,
    max_year: int = 2099,
    max_month: int = 12,
) -> pl.DataFrame:
    """Match Freddie Mac MBS loans to UMBS loans using standardized loan characteristics.

    Three-round process (see module docstring). The UMBS side is the FRE_ILLD issuance
    disclosure, which begins ``UMBS_ISSUANCE_START`` (2019-06). When ``snapshot_file`` is
    supplied, loans originated before then are recovered from the first monthly snapshot
    (fu190606) and the match window opens to pre-2019; the two UMBS sources are split by
    First Payment Date (ILLD >= start, snapshot < start) so they never overlap.

    Args:
        mbs_dir: Directory containing MBS parquet files (bronze/origination).
        umbs_dir: Directory containing UMBS FRE_ILLD parquet files.
        crosswalk_file: Output path for the final crosswalk parquet file.
        variable_file: Path to CSV file mapping MBS/UMBS columns to standardized names.
        snapshot_file: First monthly UMBS snapshot (FU) for pre-2019 recovery. If ``None``,
            only the post-2019 ILLD population is matched (legacy behavior).
        max_year: Last year to include in matching (default 2099).
        max_month: Last month to include in matching (default 12).

    Returns:
        DataFrame with columns 'Loan Sequence Number (MBS)' and 'Loan Sequence Number (UMBS)'.
    """
    # Match Columns
    match_columns = [
        "Loan Sequence Number",
        "Amortization Type",
        "Credit Score 1",
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
        "Number of Borrowers",
        "Number of Units",
        "Occupancy Status",
        "Original Interest Rate",
        "Property State",
        "Property Type",
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

    # Import MBS data (glob on directory). FHLMC origination First Payment Date is already a Date.
    mbs_pattern = str(mbs_dir / "**/*.parquet")
    lf1 = pl.scan_parquet(mbs_pattern, missing_columns="insert", extra_columns="ignore")
    lf1 = lf1.select(list(mbs_rename_map.keys())).rename(mbs_rename_map)
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

    # UMBS issuance side: FRE_ILLD covers [issuance_start, last_date); the first monthly snapshot
    # (FU) recovers the pre-issuance-start population. The two sources are split on First Payment
    # Date so they never overlap.
    lf2 = _load_umbs(str(umbs_dir / "**/*.parquet"), issuance_start, last_date)
    if snapshot_file is not None:
        lf2_snapshot = _load_umbs(str(snapshot_file), mbs_first_date, issuance_start)
        lf2 = pl.concat([lf2, lf2_snapshot], how="diagonal_relaxed")
        logger.info(
            "Combined UMBS source: FRE_ILLD (>= %s) + snapshot %s (< %s)",
            issuance_start.date(),
            snapshot_file.name,
            issuance_start.date(),
        )

    # Drop Loan Modifications
    lf2 = lf2.filter(pl.col("Loan Purpose") != "M")

    # Ensure numeric dtype on both sides for the difference filters in apply_match_logic
    # (UMBS bronze can store these as strings under schema drift, raising "arithmetic on string
    # and numeric"). The two exact-match join keys are cast too so dtypes line up across
    # ILLD/snapshot/MBS — the FU snapshot stores Mortgage Loan Amount as f64 while origination
    # uses i64, and FRE_ILLD too.
    numeric_cols = [
        "Loan-To-Value",
        "Combined Loan-To-Value",
        "Debt-To-Income",
        "Number of Borrowers",
        "Number of Units",
        "Loan Term",
        "Credit Score 1",
        "Mortgage Insurance Percentage",
        "Mortgage Loan Amount",
        "Original Interest Rate",
    ]
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
        """Apply matching logic with configurable criteria.

        Args:
            l_frame: MBS LazyFrame
            r_frame: UMBS LazyFrame
            include_mip: Whether to require MIP match (default True)
            include_credit_score: Whether to require credit score match (default True).
                Set to False for Round 3 to handle VantageScore/FICO transition issues.
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

        # Credit Score check (FHLMC uses simple exact match)
        if include_credit_score:
            conditions.append(
                (pl.col("Credit Score 1 (MBS)") - pl.col("Credit Score 1 (UMBS)")).abs() == 0
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

    # Round 1: Exact match with MIP and credit score
    crosswalk_r1 = apply_match_logic(lf1, lf2, include_mip=True, include_credit_score=True)
    df_cw_r1 = crosswalk_r1.collect()

    logger.info("Round 1 matches (MIP + CS): %s", f"{len(df_cw_r1):,}")

    # Round 2: Match without MIP, still require credit score
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

    crosswalk_r2 = apply_match_logic(
        lf1_unmatched, lf2_unmatched, include_mip=False, include_credit_score=True
    )
    df_cw_r2 = crosswalk_r2.collect()

    logger.info("Round 2 matches (no MIP, with CS): %s", f"{len(df_cw_r2):,}")

    # Round 3: Match WITHOUT credit score (to handle VantageScore/FICO transition)
    # Requires MIP to be strict since we're relaxing credit score
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

    logger.info("Total matched observations: %s", f"{len(df_crosswalk):,}")

    df_crosswalk.write_parquet(crosswalk_file)
    return df_crosswalk


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")

    # Get paths from config classes
    fhlmc_config = get_fhlmc_config()
    mbs_dir = fhlmc_config.FHLMC_BRONZE_ORIGINATION
    umbs_dir = get_umbs_bronze_dir() / "FHLMC" / "FRE_ILLD"
    snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file("fhlmc")

    # Ensure output directory exists
    CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk_file = CROSSWALK_OUTPUT_DIR / "fhlmc_crosswalk.parquet"

    # Variable mapping file is in the same directory as this script
    variable_file = Path(__file__).parent / "fhlmc_column_mapping.csv"

    logger.info("MBS data directory: %s", mbs_dir)
    logger.info("UMBS data directory: %s", umbs_dir)
    logger.info("UMBS snapshot file: %s", snapshot_file)
    logger.info("Output crosswalk: %s", crosswalk_file)
    logger.info("Variable mapping: %s", variable_file)

    cw_fhlmc = match_fhlmc_mbs_umbs(
        mbs_dir=mbs_dir,
        umbs_dir=umbs_dir,
        crosswalk_file=crosswalk_file,
        variable_file=variable_file,
        snapshot_file=snapshot_file,
    )

    logger.info("Matching complete. Crosswalk saved to: %s", crosswalk_file)
    logger.info("Total matches: %s", len(cw_fhlmc))
