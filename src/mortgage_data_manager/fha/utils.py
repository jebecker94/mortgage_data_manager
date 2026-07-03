"""County FIPS enrichment and data standardization utilities for FHA datasets."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import addfips
import pandas as pd
import polars as pl
import yaml

from mortgage_data_manager.core.io import ensure_parent_dir
from mortgage_data_manager.core.logging import get_logger

if TYPE_CHECKING:
    from mortgage_data_manager.fha.import_bronze import PathLike

logger = get_logger(__name__)

_COUNTY_MAPPINGS_PATH = Path(__file__).parent / "county_name_mappings.yaml"


@lru_cache(maxsize=1)
def _load_county_name_mappings() -> dict[str, Any]:
    """Load the FHA county-name mapping YAML once and cache the result."""
    with open(_COUNTY_MAPPINGS_PATH) as f:
        return yaml.safe_load(f) or {}


COUNTY_FIPS_OVERRIDES: dict[tuple[str, str], str] = {
    ("DC", "WASHINGTON"): "11001",
    ("DC", "DISTRICT OF COLUMBIA"): "11001",
    # Independent cities that get confused with their adjacent counties after
    # the " COUNTY" suffix is stripped by standardize_county_names()
    # St. Louis City (29510) vs St. Louis County (29189)
    ("MO", "ST. LOUIS"): "29189",
    ("MO", "ST. LOUIS CITY"): "29510",
    ("MO", "SAINT LOUIS"): "29189",
    ("MO", "SAINT LOUIS CITY"): "29510",
    # Baltimore City (24510) vs Baltimore County (24005)
    ("MD", "BALTIMORE"): "24005",
    ("MD", "BALTIMORE CITY"): "24510",
    # Virginia independent cities (addfips defaults to city, not county)
    # Bedford: City (51515, dissolved 2013) vs County (51019)
    ("VA", "BEDFORD"): "51019",
    ("VA", "BEDFORD CITY"): "51515",
    # Fairfax: City (51600) vs County (51059)
    ("VA", "FAIRFAX"): "51059",
    ("VA", "FAIRFAX CITY"): "51600",
    # Franklin: City (51620) vs County (51067)
    ("VA", "FRANKLIN"): "51067",
    ("VA", "FRANKLIN CITY"): "51620",
    # Roanoke: City (51770) vs County (51161)
    ("VA", "ROANOKE"): "51161",
    ("VA", "ROANOKE CITY"): "51770",
    # Richmond: City (51760) vs County (51159)
    ("VA", "RICHMOND"): "51159",
    ("VA", "RICHMOND CITY"): "51760",
    # US Virgin Islands — addfips has no VI mappings at all.
    # St. Croix (78010), St. John (78020), St. Thomas (78030).
    # The ST → ST. prefix transform in standardize_county_names() handles
    # the "ST CROIX" → "ST. CROIX" rewrite, so we only need the dot-form
    # plus the SAINT-prefixed forms (which the transform does not touch).
    ("VI", "ST. CROIX"): "78010",
    ("VI", "SAINT CROIX"): "78010",
    ("VI", "ST. JOHN"): "78020",
    ("VI", "SAINT JOHN"): "78020",
    ("VI", "ST. THOMAS"): "78030",
    ("VI", "SAINT THOMAS"): "78030",
    # Alaska — Chugach Census Area (02063) was carved out of Valdez-Cordova
    # in 2019 alongside Copper River (02066). addfips's bundled data
    # predates the split and knows Copper River but not Chugach; map the
    # name explicitly here. If a future addfips release adds Chugach this
    # override stays correct.
    ("AK", "CHUGACH"): "02063",
}


def _read_tabular_file(path: Path) -> pl.DataFrame:
    """Read a CSV or parquet file into a Polars DataFrame."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pl.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pl.read_parquet(path)
    raise ValueError(f"Unsupported file extension for {path}")


def _write_tabular_file(df: pl.DataFrame, path: Path) -> None:
    """Persist a Polars DataFrame to CSV or parquet."""
    ensure_parent_dir(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.write_csv(path)
        return
    if suffix in {".parquet", ".pq"}:
        df.write_parquet(path)
        return
    raise ValueError(f"Unsupported file extension for {path}")


def standardize_county_names(
    df: pl.LazyFrame,
    county_col: str = "Property County",
    state_col: str = "Property State",
) -> pl.LazyFrame:
    """Standardise county names so they align with the FIPS dataset.

    Mapping tables for state corrections and county renames live in
    ``county_name_mappings.yaml`` (sibling to this module) and reflect
    FHA-specific quirks; they are not suitable for deployment in other
    projects. The trailing ``" COUNTY"`` suffix strip and ``ST ``/``STE ``
    prefix rewrites are generic transforms applied here in code.

    Args:
        df: The snapshot data containing county information.
        county_col: Column containing county names to standardise.
        state_col: Column containing the two-letter state abbreviation.

    Returns:
        A ``LazyFrame`` with harmonised county naming.
    """
    logger.info("Standardizing county names...")

    mappings = _load_county_name_mappings()

    # First convert empty values and "NAN"/"None" to empty strings
    df = df.with_columns(
        pl.when(pl.col(county_col).is_null())
        .then(pl.lit(""))
        .when(pl.col(county_col).str.to_lowercase().is_in(["nan", "none"]))
        .then(pl.lit(""))
        .otherwise(pl.col(county_col))
        .str.to_uppercase()
        .alias(county_col)
    )

    # State corrections: when (county, state) matches a known mismatch, fix the state.
    for fix in mappings.get("state_corrections", []):
        df = df.with_columns(
            pl.when(
                (pl.col(county_col) == fix["county"]) & (pl.col(state_col) == fix["from_state"])
            )
            .then(pl.lit(fix["to_state"]))
            .otherwise(pl.col(state_col))
            .alias(state_col)
        )

    # County renames: build a single chained when/then expression that mirrors
    # the previous hand-written chain, then append the trailing-" COUNTY" strip
    # as the final fall-through branch (matches the prior ordering exactly).
    rename_branches = mappings.get("county_renames", []) or []
    expr: pl.Expr | None = None
    for rule in rename_branches:
        cond = (pl.col(county_col) == rule["from"]) & (
            pl.col(state_col).is_in(list(rule["states"]))
        )
        expr = (
            pl.when(cond).then(pl.lit(rule["to"]))
            if expr is None
            else expr.when(cond).then(pl.lit(rule["to"]))
        )

    suffix_cond = pl.col(county_col).str.contains(" COUNTY$")
    suffix_value = pl.col(county_col).str.replace(" COUNTY$", "")
    expr = (
        pl.when(suffix_cond).then(suffix_value)
        if expr is None
        else expr.when(suffix_cond).then(suffix_value)
    )

    df = df.with_columns(expr.otherwise(pl.col(county_col)).alias(county_col))

    # Handle common prefixes after specific cases
    df = df.with_columns(
        pl.when(pl.col(county_col).str.starts_with("ST "))
        .then(pl.concat_str([pl.lit("ST. "), pl.col(county_col).str.slice(3)]))
        .when(pl.col(county_col).str.starts_with("STE "))
        .then(pl.concat_str([pl.lit("SAINTE "), pl.col(county_col).str.slice(4)]))
        .otherwise(pl.col(county_col))
        .alias(county_col)
    )

    return df


def add_county_fips(
    df: pl.LazyFrame,
    state_col: str = "Property State",
    county_col: str = "Property County",
    fips_col: str = "FIPS",
) -> pl.LazyFrame:
    """Add FIPS codes to a dataset with state and county columns.

    Args:
        df: Dataset containing the state and county columns to enrich.
        state_col: Name of the state column.
        county_col: Name of the county column.
        fips_col: Name of the output column that will receive the concatenated FIPS code.

    Returns:
        A ``LazyFrame`` containing a ``fips_col`` column with county-level FIPS codes.
    """
    logger.info("Starting FIPS code addition process...")

    # Standardize the main dataframe's county names. The unique pairs derived
    # below inherit this standardisation, so no second pass is needed.
    logger.info("Standardizing main dataframe county names...")
    df = standardize_county_names(df, state_col=state_col, county_col=county_col)

    logger.info("Getting unique county/state pairs...")
    unique_pairs = df.select([state_col, county_col]).unique().collect()

    if unique_pairs.is_empty():
        logger.warning("No valid county/state pairs found for FIPS lookup")
        return df.with_columns(pl.lit(None).cast(pl.Utf8).alias(fips_col))

    logger.info("Generating FIPS codes for %d unique counties...", len(unique_pairs))
    af = addfips.AddFIPS()
    fips_values: list[str | None] = []
    for state, county in unique_pairs.iter_rows():
        # Check overrides first (handles independent cities like St. Louis, Baltimore)
        override_key = (state.upper() if state else "", county.upper() if county else "")
        if override_key in COUNTY_FIPS_OVERRIDES:
            fips_values.append(COUNTY_FIPS_OVERRIDES[override_key])
        else:
            fips_values.append(af.get_county_fips(county, state))

    county_map = unique_pairs.with_columns(pl.Series(fips_col, fips_values, dtype=pl.Utf8))

    logger.info("Joining FIPS codes back to main dataframe...")
    return df.join(county_map.lazy(), on=[state_col, county_col], how="left")


def build_county_fips_crosswalk(
    bronze_folder: PathLike,
    crosswalk_path: PathLike,
    problematic_path: PathLike,
    state_col: str = "Property State",
    county_col: str = "Property County",
    fips_col: str = "FIPS",
    manual_overrides: Mapping[tuple[str, str], str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Create or extend a county FIPS crosswalk from bronze snapshot files.

    The function scans the bronze-level parquet datasets for both the single
    family and HECM programs, standardises county names using the same logic as
    the main import pipeline, and applies the :mod:`addfips` lookup augmented by
    manual overrides. New state/county combinations are appended to the
    ``crosswalk_path`` file, and any unresolved observations are written to
    ``problematic_path``.

    Args:
        bronze_folder: Directory containing ``single_family`` and ``hecm``
            bronze parquet files.
        crosswalk_path: Destination for the successful crosswalk mappings. The
            extension determines whether a CSV or parquet file is written.
        problematic_path: Destination for observations that could not be
            matched to a FIPS code.
        state_col: Name of the state column in the source data.
        county_col: Name of the county column in the source data.
        fips_col: Name of the output FIPS column in the crosswalk.
        manual_overrides: Additional manual mappings from ``(state, county)`` to
            FIPS codes. Overrides are combined with the built-in corrections.

    Returns:
        Tuple containing the updated crosswalk dataframe and the dataframe of
        problematic observations.
    """
    bronze_root = Path(bronze_folder)
    crosswalk_file = Path(crosswalk_path)
    problematic_file = Path(problematic_path)

    manual_map: dict[tuple[str, str], str] = dict(COUNTY_FIPS_OVERRIDES)
    if manual_overrides:
        manual_map.update(
            {
                (
                    state.upper(),
                    county.upper(),
                ): code
                for (state, county), code in manual_overrides.items()
            }
        )

    logger.info("Scanning bronze datasets for unique state/county pairs...")
    lazy_frames: list[pl.LazyFrame] = []
    for dataset in ("single_family", "hecm"):
        dataset_dir = bronze_root / dataset
        if not dataset_dir.exists():
            logger.warning("Bronze dataset folder missing: %s", dataset_dir)
            continue

        dataset_pattern = dataset_dir / "*.parquet"
        try:
            lf = pl.scan_parquet(str(dataset_pattern)).select(
                [
                    pl.col(state_col).cast(pl.Utf8, strict=False).alias(state_col),
                    pl.col(county_col).cast(pl.Utf8, strict=False).alias(county_col),
                ]
            )
        except FileNotFoundError:
            logger.warning("No parquet files found for dataset: %s", dataset_dir)
            continue
        lazy_frames.append(lf)

    if not lazy_frames:
        msg = "No bronze parquet files were found for single_family or hecm."
        raise FileNotFoundError(msg)

    combined = pl.concat(lazy_frames, how="diagonal_relaxed")
    combined = standardize_county_names(combined, state_col=state_col, county_col=county_col)
    combined = combined.with_columns(pl.col(state_col).str.to_uppercase())

    unique_pairs = (
        combined.filter(
            pl.col(state_col).is_not_null()
            & pl.col(county_col).is_not_null()
            & (pl.col(state_col) != "")
            & (pl.col(county_col) != "")
        )
        .select([state_col, county_col])
        .unique()
        .collect()
    )

    logger.info("Loaded %d unique state/county pairs", len(unique_pairs))

    existing_crosswalk: pl.DataFrame | None = None
    if crosswalk_file.exists():
        logger.info("Reading existing crosswalk from %s", crosswalk_file)
        existing_crosswalk = _read_tabular_file(crosswalk_file)
        existing_crosswalk = (
            standardize_county_names(
                existing_crosswalk.lazy(),
                state_col=state_col,
                county_col=county_col,
            )
            .with_columns(
                pl.col(state_col).str.to_uppercase(),
                pl.col(fips_col)
                .cast(pl.Utf8, strict=False)
                .str.strip_chars()
                .str.zfill(5)
                .alias(fips_col),
            )
            .collect()
        )

    known_pairs = (
        existing_crosswalk.select([state_col, county_col]).unique()
        if existing_crosswalk is not None
        else pl.DataFrame(
            {
                state_col: pl.Series([], dtype=pl.Utf8),
                county_col: pl.Series([], dtype=pl.Utf8),
            }
        )
    )

    new_pairs = unique_pairs.join(known_pairs, on=[state_col, county_col], how="anti")

    logger.info("Identified %d new state/county pairs", len(new_pairs))

    af = addfips.AddFIPS()
    crosswalk_records: list[tuple[str, str, str]] = []
    problematic_records: list[tuple[str, str]] = []

    for state, county in new_pairs.iter_rows():
        key = (state, county)
        fips = manual_map.get(key)
        if not fips:
            fips = af.get_county_fips(county, state)
        if fips:
            crosswalk_records.append((state, county, str(fips).zfill(5)))
        else:
            problematic_records.append((state, county))

    new_crosswalk = (
        pl.DataFrame(
            {
                state_col: [record[0] for record in crosswalk_records],
                county_col: [record[1] for record in crosswalk_records],
                fips_col: [record[2] for record in crosswalk_records],
            }
        )
        if crosswalk_records
        else pl.DataFrame(
            {
                state_col: pl.Series([], dtype=pl.Utf8),
                county_col: pl.Series([], dtype=pl.Utf8),
                fips_col: pl.Series([], dtype=pl.Utf8),
            }
        )
    )

    new_problematic = (
        pl.DataFrame(
            {
                state_col: [record[0] for record in problematic_records],
                county_col: [record[1] for record in problematic_records],
            }
        )
        if problematic_records
        else pl.DataFrame(
            {
                state_col: pl.Series([], dtype=pl.Utf8),
                county_col: pl.Series([], dtype=pl.Utf8),
            }
        )
    )

    crosswalk_frames: list[pl.DataFrame] = []
    if existing_crosswalk is not None:
        crosswalk_frames.append(existing_crosswalk)
    if not new_crosswalk.is_empty():
        crosswalk_frames.append(new_crosswalk)

    if crosswalk_frames:
        updated_crosswalk = (
            pl.concat(crosswalk_frames, how="diagonal_relaxed")
            .unique(subset=[state_col, county_col], keep="first")
            .sort([state_col, county_col])
        )
    else:
        updated_crosswalk = pl.DataFrame(
            {
                state_col: pl.Series([], dtype=pl.Utf8),
                county_col: pl.Series([], dtype=pl.Utf8),
                fips_col: pl.Series([], dtype=pl.Utf8),
            }
        )

    existing_problematic: pl.DataFrame | None = None
    if problematic_file.exists():
        logger.info("Reading existing problematic observations from %s", problematic_file)
        existing_problematic = (
            standardize_county_names(
                _read_tabular_file(problematic_file).lazy(),
                state_col=state_col,
                county_col=county_col,
            )
            .with_columns(pl.col(state_col).str.to_uppercase())
            .collect()
        )

    problematic_frames: list[pl.DataFrame] = []
    if existing_problematic is not None and not existing_problematic.is_empty():
        resolved_pairs = updated_crosswalk.select([state_col, county_col])
        existing_problematic = existing_problematic.join(
            resolved_pairs, on=[state_col, county_col], how="anti"
        )
        if not existing_problematic.is_empty():
            problematic_frames.append(existing_problematic)
    if not new_problematic.is_empty():
        problematic_frames.append(new_problematic)

    if problematic_frames:
        updated_problematic = (
            pl.concat(problematic_frames, how="diagonal_relaxed")
            .unique()
            .sort([state_col, county_col])
        )
    else:
        updated_problematic = pl.DataFrame(
            {
                state_col: pl.Series([], dtype=pl.Utf8),
                county_col: pl.Series([], dtype=pl.Utf8),
            }
        )

    logger.info(
        "Crosswalk now contains %d rows; %d problematic observations recorded",
        len(updated_crosswalk),
        len(updated_problematic),
    )

    _write_tabular_file(updated_crosswalk, crosswalk_file)
    _write_tabular_file(updated_problematic, problematic_file)

    return updated_crosswalk, updated_problematic


def create_lender_id_to_name_crosswalk(clean_data_folder: PathLike) -> pl.DataFrame:
    """Create a lender ID/name crosswalk from cleaned snapshot parquet files."""
    logger.info("Creating lender ID to name crosswalk...")

    lazy_frames: list[pl.LazyFrame] = []

    clean_path = Path(clean_data_folder)
    sf_files = sorted((clean_path / "single_family").glob("fha_sf_snapshot*.parquet"))
    sf_files = [file for file in sf_files if "201408" not in file.name]
    for file in sf_files:
        logger.info("Get institution data from: %s", file)
        file_date = pd.to_datetime(file.stem.split("_")[-1], format="%Y%m%d")

        sf_originators = (
            pl.scan_parquet(str(file))
            .select(["Originating Mortgagee Number", "Originating Mortgagee"])
            .rename(
                {
                    "Originating Mortgagee Number": "Institution_Number",
                    "Originating Mortgagee": "Institution_Name",
                }
            )
            .with_columns(pl.lit(file_date).alias("File_Date"))
        )
        lazy_frames.append(sf_originators)

        sf_sponsors = (
            pl.scan_parquet(str(file))
            .select(["Sponsor Number", "Sponsor Name"])
            .rename(
                {
                    "Sponsor Number": "Institution_Number",
                    "Sponsor Name": "Institution_Name",
                }
            )
            .with_columns(pl.lit(file_date).alias("File_Date"))
        )
        lazy_frames.append(sf_sponsors)

    hecm_files = sorted((clean_path / "hecm").glob("fha_hecm_snapshot*.parquet"))
    for file in hecm_files:
        logger.info("Get institution data from: %s", file)
        file_date = pd.to_datetime(file.stem.split("_")[-1], format="%Y%m%d")

        hecm_originators = (
            pl.scan_parquet(str(file))
            .select(["Originating Mortgagee Number", "Originating Mortgagee"])
            .rename(
                {
                    "Originating Mortgagee Number": "Institution_Number",
                    "Originating Mortgagee": "Institution_Name",
                }
            )
            .with_columns(pl.lit(file_date).alias("File_Date"))
        )
        lazy_frames.append(hecm_originators)

        hecm_sponsors = (
            pl.scan_parquet(str(file))
            .select(["Sponsor Number", "Sponsor Name"])
            .rename(
                {
                    "Sponsor Number": "Institution_Number",
                    "Sponsor Name": "Institution_Name",
                }
            )
            .with_columns(pl.lit(file_date).alias("File_Date"))
        )
        lazy_frames.append(hecm_sponsors)

    combined = (
        pl.concat(lazy_frames, how="diagonal_relaxed")
        .unique()
        .drop_nulls()
        .sort(["Institution_Number", "File_Date", "Institution_Name"])
        .collect()
    )

    enriched = (
        combined.with_columns(
            pl.col("File_Date")
            .min()
            .over(["Institution_Number", "Institution_Name"])
            .alias("First_Observed"),
            pl.col("File_Date")
            .max()
            .over(["Institution_Number", "Institution_Name"])
            .alias("Last_Observed"),
        )
        .select(
            [
                "Institution_Number",
                "Institution_Name",
                "First_Observed",
                "Last_Observed",
            ]
        )
        .unique()
        .with_columns(
            pl.col("First_Observed").dt.strftime("%Y-%m").alias("First_Observed_Period"),
            pl.col("Last_Observed").dt.strftime("%Y-%m").alias("Last_Observed_Period"),
            pl.col("First_Observed").dt.date().alias("First_Observed"),
            pl.col("Last_Observed").dt.date().alias("Last_Observed"),
        )
    )

    conflict_flags = (
        enriched.group_by("Institution_Number")
        .agg(
            pl.col("Institution_Name").n_unique().alias("Distinct_Name_Count"),
        )
        .with_columns(
            (pl.col("Distinct_Name_Count") > 1).alias("Has_Name_Conflict"),
        )
    )

    crosswalk = (
        enriched.join(conflict_flags, on="Institution_Number", how="left")
        .with_columns(
            pl.col("Distinct_Name_Count").fill_null(1),
            pl.col("Has_Name_Conflict").fill_null(False),
        )
        .sort(["Institution_Number", "First_Observed", "Institution_Name"])
    )

    return crosswalk
