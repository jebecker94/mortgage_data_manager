#!/usr/bin/env python3
"""Shared utilities for matching MBS data using Polars."""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def combine_issuances(mbs_file, umbs_file, crosswalk_file, variable_file, combined_file) -> None:
    """Combine MBS and UMBS data using a crosswalk using Polars LazyFrames."""
    # Read Variable Names
    variable_crosswalk = pl.read_csv(variable_file)

    # Prepare crosswalk dictionaries as lists of tuples for renaming
    umbs_rename = variable_crosswalk.select(["UMBS Columns", "Standardized Names"]).drop_nulls()
    umbs_rename_dict = {
        row["UMBS Columns"]: row["Standardized Names"] for row in umbs_rename.to_dicts()
    }

    mbs_rename = variable_crosswalk.select(["MBS Columns", "Standardized Names"]).drop_nulls()
    mbs_rename_dict = {
        row["MBS Columns"]: row["Standardized Names"] for row in mbs_rename.to_dicts()
    }

    # Import Data
    # Polars scan_parquet doesn't need to read metadata separately to filter columns;
    # it lazy-loads schema and handles projection pushdown.
    lf1 = pl.scan_parquet(mbs_file)
    lf2 = pl.scan_parquet(umbs_file)
    cw = pl.scan_parquet(crosswalk_file)

    # Select only relevant columns that exist in the files
    lf1_cols = [c for c in mbs_rename_dict.keys() if c in lf1.collect_schema().names()]
    lf2_cols = [c for c in umbs_rename_dict.keys() if c in lf2.collect_schema().names()]

    lf1 = lf1.select(lf1_cols).rename({c: mbs_rename_dict[c] for c in lf1_cols})
    lf2 = lf2.select(lf2_cols).rename({c: umbs_rename_dict[c] for c in lf2_cols})

    # Keep Only Matched Observations
    # String manipulation for Loan Sequence Number
    lf1 = lf1.with_columns(
        pl.col("Loan Sequence Number")
        .str.replace(r"\.0$", "", literal=False)
        .str.strip_chars_start("0")
    )
    lf1 = lf1.join(
        cw, left_on="Loan Sequence Number", right_on="Loan Sequence Number (MBS)", how="inner"
    )

    lf2 = lf2.with_columns(pl.col("Loan Sequence Number").cast(pl.String))
    lf2 = lf2.join(
        cw, left_on="Loan Sequence Number", right_on="Loan Sequence Number (UMBS)", how="inner"
    )

    # Merge
    lf1 = lf1.drop("Loan Sequence Number")
    lf2 = lf2.drop("Loan Sequence Number")

    df_combined = lf1.join(
        lf2,
        on=["Loan Sequence Number (MBS)", "Loan Sequence Number (UMBS)"],
        how="inner",
        suffix=" (UMBS)",
    )
    # Note: Suffix in Polars is applied to RHS. The LHS will retain original names.
    # We want (MBS) and (UMBS).
    # Rename LHS columns to include (MBS) where shared
    shared_names = set(lf1.collect_schema().names()).intersection(lf2.collect_schema().names())
    shared_names.discard("Loan Sequence Number (MBS)")
    shared_names.discard("Loan Sequence Number (UMBS)")

    df_combined = df_combined.rename({name: f"{name} (MBS)" for name in shared_names})

    # Replace Missings
    shared_standard_columns = (
        variable_crosswalk.select(["UMBS Columns", "MBS Columns", "Standardized Names"])
        .drop_nulls()
        .select("Standardized Names")
        .to_series()
        .to_list()
    )
    shared_standard_columns = [
        x for x in shared_standard_columns if "Loan Sequence Number" not in x
    ]

    for column in shared_standard_columns:
        mbs_column = f"{column} (MBS)"
        umbs_column = f"{column} (UMBS)"

        # Check if both columns exist in the resulting LazyFrame
        current_schema = df_combined.collect_schema().names()
        if mbs_column in current_schema and umbs_column in current_schema:
            logger.info("Replacing Missing Values for Column: %s", column)
            df_combined = df_combined.with_columns(
                pl.coalesce([pl.col(umbs_column), pl.col(mbs_column)]).alias(column)
            ).drop([mbs_column, umbs_column])

    # Fix Data Types (Polars is stricter, so we just ensure they are strings if they were "mixed")
    # In Polars, we can't easily "infer_dtype" in a lazy way across all values without a collect.
    # However, we can check the col dtypes and cast if they are Object/Categorical to String.
    # But usually, they'll be correct from the Parquet read.
    # We'll just cast all to string if they are of a type that might cause issues.
    for name, dtype in df_combined.collect_schema().items():
        if dtype == pl.Object:
            df_combined = df_combined.with_columns(pl.col(name).cast(pl.String))

    # Save Combined File
    df_combined.collect().write_parquet(combined_file)


def combine_crosswalks(cw_fnma_file, cw_fhlmc_file, save_file_csv, save_file_parquet) -> None:
    """Combine Fannie and Freddie crosswalks using Polars."""
    # Load Crosswalks
    cw_fnma = pl.scan_parquet(cw_fnma_file).with_columns(pl.lit(1).alias("Enterprise"))
    cw_fhlmc = pl.scan_parquet(cw_fhlmc_file).with_columns(pl.lit(3).alias("Enterprise"))

    # Combine
    cw = pl.concat([cw_fnma, cw_fhlmc], how="diagonal")

    # Save Combined Crosswalk
    cw.sink_csv(save_file_csv, separator="|")
    cw.sink_parquet(save_file_parquet)
