"""Build the HUD silver layer: combined longitudinal crosswalk datasets.

Reads the per-quarter bronze parquet for each crosswalk type and concatenates
the quarters into a unified longitudinal dataset (deduplicated and sorted),
with optional CSV and Stata output. Creates a special "rounded" ZIP_TRACT
variant that truncates ZIP codes to 3-digit prefixes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud.config import STRING_COLUMNS, VALID_CROSSWALK_NAMES, HUDConfig

logger = get_logger(__name__)

_FILENAME_RE = re.compile(r"_(\d{4})_Q\d\.parquet$")


def _discover_parquet_files(type_dir: Path, min_year: int, max_year: int) -> list[Path]:
    """Return all per-quarter bronze parquet files for a type within the year range."""
    if not type_dir.exists():
        return []

    found_files = []
    for file in sorted(type_dir.glob("*.parquet")):
        match = _FILENAME_RE.search(file.name)
        if match:
            file_year = int(match.group(1))
            if min_year <= file_year <= max_year:
                found_files.append(file)

    return found_files


def _enforce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure consistent types for geographic columns."""
    for col in STRING_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


def _write_crosswalk_outputs(
    dataframe: pd.DataFrame,
    output_dir: Path,
    type_name: str,
    min_year: int,
    max_year: int,
    save_csv: bool = False,
    save_dta: bool = False,
) -> None:
    """Write the combined crosswalk data to Parquet, and optionally CSV/DTA."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_base = output_dir / f"{type_name}_{min_year}-{max_year}"

    # Parquet
    parquet_path = output_base.with_suffix(".parquet")
    dataframe.to_parquet(parquet_path, index=False)
    logger.info("Saved Combined Parquet: %s", parquet_path)

    # CSV (optional)
    if save_csv:
        csv_path = output_base.with_suffix(".csv")
        dataframe.to_csv(csv_path, index=False, sep="|")
        logger.info("Saved CSV: %s", csv_path)

    # Stata DTA (optional)
    if save_dta:
        dta_path = output_base.with_suffix(".dta")
        try:
            dataframe.to_stata(dta_path, write_index=False, version=118)
            logger.info("Saved Stata DTA: %s", dta_path)
        except Exception as e:
            logger.warning("Could not save Stata DTA: %s", e)

    # Special handling for ZIP_TRACT: rounded version
    if type_name == "ZIP_TRACT" and "zip" in dataframe.columns and "tract" in dataframe.columns:
        rounded_dataframe = dataframe.copy()
        rounded_dataframe["zipshort"] = rounded_dataframe["zip"].fillna("").str.slice(0, 3) + "00"
        rounded_dataframe = rounded_dataframe[["zipshort", "tract"]].drop_duplicates()

        rounded_path = output_dir / f"{type_name}_rounded_{min_year}-{max_year}.parquet"
        rounded_dataframe.to_parquet(rounded_path, index=False)
        logger.info("Saved Rounded Parquet: %s", rounded_path)


def process_crosswalk_type(
    type_name: str,
    bronze_dir: Path | None = None,
    output_dir: Path | None = None,
    min_year: int = 2010,
    max_year: int = 2025,
    save_csv: bool = False,
    save_dta: bool = False,
) -> None:
    """Combine one crosswalk type's per-quarter bronze parquet into silver.

    Args:
        type_name: Name of the crosswalk type (e.g., "ZIP_TRACT").
        bronze_dir: Source dir for per-quarter parquet. Defaults to HUDConfig.HUD_BRONZE_DIR.
        output_dir: Output dir for combined files. Defaults to HUDConfig.HUD_SILVER_DIR.
        min_year: Start year (inclusive).
        max_year: End year (inclusive).
        save_csv: Also save as pipe-delimited CSV.
        save_dta: Also save as Stata DTA.
    """
    if bronze_dir is None:
        bronze_dir = HUDConfig.HUD_BRONZE_DIR
    if output_dir is None:
        output_dir = HUDConfig.HUD_SILVER_DIR

    type_dir = bronze_dir / type_name
    if not type_dir.exists():
        logger.warning("Bronze directory not found for %s: %s", type_name, type_dir)
        return

    files = _discover_parquet_files(type_dir, min_year, max_year)
    if not files:
        logger.warning(
            "No bronze parquet found for %s in range %d-%d", type_name, min_year, max_year
        )
        return

    dataframes = []
    for f in files:
        logger.info("Reading file: %s", f)
        dataframes.append(pd.read_parquet(f))

    if not dataframes:
        return

    combined_df = pd.concat(dataframes, ignore_index=True)
    combined_df = _enforce_types(combined_df)
    combined_df = combined_df.drop_duplicates()

    sort_cols = [
        c for c in ["year", "quarter", "zip", "tract", "county", "cbsa"] if c in combined_df.columns
    ]
    if sort_cols:
        combined_df = combined_df.sort_values(by=sort_cols)

    _write_crosswalk_outputs(
        combined_df,
        output_dir,
        type_name,
        min_year,
        max_year,
        save_csv=save_csv,
        save_dta=save_dta,
    )


def process_all(
    min_year: int = 2010,
    max_year: int = 2025,
    save_csv: bool = False,
    save_dta: bool = False,
) -> None:
    """Combine all available crosswalk types from bronze into silver.

    Args:
        min_year: Start year (inclusive).
        max_year: End year (inclusive).
        save_csv: Also save as pipe-delimited CSV.
        save_dta: Also save as Stata DTA.
    """
    bronze_dir = HUDConfig.HUD_BRONZE_DIR
    silver_dir = HUDConfig.HUD_SILVER_DIR

    if not bronze_dir.exists():
        logger.error("Bronze data directory not found: %s", bronze_dir)
        return

    available_types = [
        d.name for d in bronze_dir.iterdir() if d.is_dir() and d.name in VALID_CROSSWALK_NAMES
    ]
    if not available_types:
        logger.warning("No crosswalk type subdirectories found in bronze data.")
        return

    logger.info("Found types: %s", available_types)

    for type_name in available_types:
        logger.info("Processing %s...", type_name)
        process_crosswalk_type(
            type_name,
            bronze_dir,
            silver_dir,
            min_year,
            max_year,
            save_csv=save_csv,
            save_dta=save_dta,
        )
