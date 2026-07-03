"""Import functions for FHLMC bronze layer data.

This module handles importing raw FHLMC data files to bronze layer (Parquet),
including historical loan data (origination + performance) and RPL data.
"""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fhlmc.config import FHLMCConfig

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------


def apply_type_conversions(
    lf: pl.LazyFrame,
    schema: dict,
) -> pl.LazyFrame:
    """Apply type conversions to LazyFrame based on schema.

    Args:
        lf: LazyFrame with string columns
        schema: Schema dict with type information

    Returns:
        LazyFrame with proper datasets
    """
    conversions = []

    for col_name in lf.collect_schema().names():
        if col_name not in schema["column_types"]:
            continue  # Keep as string if not in schema

        target_type = schema["column_types"][col_name]

        # Handle date conversions
        if col_name in schema["date_columns"]:
            date_format = schema["date_formats"].get(col_name)

            if date_format:
                # strict=False so missing-value sentinels (e.g. "." in older non-standard
                # vintages) become null instead of crashing the whole file.
                conversions.append(
                    pl.col(col_name).str.strptime(pl.Date, format=date_format, strict=False)
                )
            else:
                logger.warning(
                    f"Date format not specified for {col_name}, "
                    f"keeping as string. Inspect data to determine format."
                )
                continue

        # Handle numeric conversions
        elif target_type == pl.Float64:
            conversions.append(pl.col(col_name).cast(pl.Float64, strict=False))

        elif target_type == pl.Int64:
            conversions.append(pl.col(col_name).cast(pl.Int64, strict=False))

    # Apply all conversions
    if conversions:
        lf = lf.with_columns(conversions)

    return lf


def generate_output_path(input_path: Path, output_dir: Path, extension: str = ".parquet") -> Path:
    """Generate output file path based on input filename.

    Args:
        input_path: Input file path
        output_dir: Output directory
        extension: File extension for output (default: .parquet)

    Returns:
        Path object for output file
    """
    base_name = input_path.stem
    return output_dir / f"{base_name}{extension}"


def ensure_directories(*dirs: Path) -> None:
    """Create directories if they don't exist.

    Args:
        *dirs: Variable number of Path objects to create
    """
    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {dir_path}")


# ---------------------------------------------------------------------------
# Historical Data Import (Origination + Performance)
# ---------------------------------------------------------------------------


def import_all_historical_data(
    schemas: dict[str, dict],
    raw_dir: Path | None = None,
    years: Iterable[int] | None = None,
    overwrite: bool = False,
    datasets: Iterable[str] | None = None,
    zip_names: Iterable[str] | None = None,
) -> None:
    """Recursively import every pipe-delimited .txt file found in raw zip archives.

    Walks each ``*.zip`` in ``raw_dir`` (excluding the RPL bundle, which has its
    own importer + schema) and descends into any nested zips, regardless of
    layering. At the leaves, every ``.txt`` is imported — origination vs
    performance is detected from the ``_time_`` substring in the filename. This
    handles standalone yearly zips, the bundled full-set zip, the non-standard
    (``historical_data_excl_*``) bundle, or any other layered ``.zip`` of the same
    shape with no source-specific code.

    Args:
        schemas: Schemas keyed by dataset ('origination', 'performance').
        raw_dir: Directory to scan. Defaults to ``FHLMCConfig.FHLMC_RAW_DIR``.
        years: If provided, only import .txt files whose name contains one of
            these years. Useful when scanning the full-set zip but only wanting
            a subset of vintages.
        overwrite: If False (default), skip .txt files whose bronze parquet
            already exists. If True, force a rebuild.
        datasets: If provided, only import these dataset types ('origination',
            'performance'). Lets the caller pull origination without the much
            larger performance files (e.g. for the non-standard bundle).
        zip_names: If provided, only walk top-level zips with these exact names.
            Lets the caller target one source (e.g. ``non_std_historical_data.zip``)
            instead of every zip in ``raw_dir`` (which includes the 37 GB full set).
    """
    raw_dir = raw_dir or FHLMCConfig.FHLMC_RAW_DIR
    year_filter = {str(y) for y in years} if years is not None else None
    dataset_filter = {str(d) for d in datasets} if datasets is not None else None
    name_filter = {str(n) for n in zip_names} if zip_names is not None else None

    zip_files = sorted(
        p
        for p in raw_dir.glob("*.zip")
        if p.name != "rpl_historical_data.zip" and (name_filter is None or p.name in name_filter)
    )

    for zip_path in zip_files:
        logger.info(f"Processing {zip_path.name}")
        _walk_zip_for_txt(zip_path, schemas, year_filter, overwrite, dataset_filter)


def _walk_zip_for_txt(
    zip_path: Path,
    schemas: dict[str, dict],
    year_filter: set[str] | None,
    overwrite: bool,
    dataset_filter: set[str] | None = None,
) -> None:
    """Recurse into a zip, extracting nested zips to temp dirs and importing .txt leaves.

    Args:
        zip_path: Zip file to walk.
        schemas: Schemas keyed by dataset.
        year_filter: If non-None, only .txt files containing one of these year
            strings are imported.
        overwrite: Forwarded to the leaf-level skip check.
        dataset_filter: If non-None, only import .txt files whose detected dataset
            ('origination'/'performance') is in this set.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        for entry in zf.namelist():
            if entry.endswith("/"):
                continue
            if entry.endswith(".zip"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    zf.extract(entry, temp_path)
                    _walk_zip_for_txt(
                        temp_path / entry, schemas, year_filter, overwrite, dataset_filter
                    )
            elif entry.endswith(".txt"):
                entry_name = Path(entry).name
                if year_filter is not None and not any(y in entry_name for y in year_filter):
                    continue

                dataset = "performance" if "_time_" in entry_name else "origination"

                if dataset_filter is not None and dataset not in dataset_filter:
                    continue

                output_dir = (
                    FHLMCConfig.FHLMC_BRONZE_ORIGINATION
                    if dataset == "origination"
                    else FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
                )
                output_file = output_dir / f"{Path(entry_name).stem}.parquet"
                if not should_process_file(output_file, overwrite=overwrite):
                    logger.info(f"  Skipping {entry_name} (bronze exists)")
                    continue

                logger.info(f"  Processing {entry_name} as {dataset}")
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    zf.extract(entry, temp_path)
                    _import_txt_file(temp_path / entry, dataset, schemas)
            else:
                logger.debug(f"  Skipping unrecognized entry: {entry}")


def _import_txt_file(txt_path: Path, dataset: str, schemas: dict[str, dict]) -> None:
    """Import a pipe-delimited text file using polars.

    Args:
        txt_path: Path to the text file
        dataset: 'origination' or 'performance'
        schemas: Dict of schemas
    """
    schema = schemas.get(dataset)

    if not schema:
        logger.error(f"No schema found for dataset: {dataset}")
        return

    logger.info(f"      Loading {txt_path.name} (infer_schema=False)")

    # Load as all strings with scan_csv
    lf = pl.scan_csv(
        txt_path,
        separator="|",
        has_header=False,
        infer_schema_length=0,  # Don't infer schema - load all as strings
        truncate_ragged_lines=True,  # Handle any inconsistent line lengths
    )

    # Rename columns based on schema
    lf = _rename_columns(lf, schema)

    # Apply type conversions
    lf = apply_type_conversions(lf, schema)

    # Save to parquet
    output_dir = (
        FHLMCConfig.FHLMC_BRONZE_ORIGINATION
        if dataset == "origination"
        else FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
    )
    output_file = generate_output_path(txt_path, output_dir)

    logger.info(f"      Saving to {output_file}")
    lf.sink_parquet(
        output_file,
        compression=FHLMCConfig.PARQUET_COMPRESSION,
        statistics=FHLMCConfig.PARQUET_STATISTICS,
        row_group_size=FHLMCConfig.PARQUET_ROW_GROUP_SIZE,
    )

    logger.info(f"      ✓ Saved {output_file.name}")


def _rename_columns(lf: pl.LazyFrame, schema: dict) -> pl.LazyFrame:
    """Rename columns based on schema.

    Args:
        lf: LazyFrame with default column names (column_1, column_2, etc.)
        schema: Schema dict with column_names list

    Returns:
        LazyFrame with renamed columns
    """
    column_names = schema["column_names"]
    n_cols = len(lf.collect_schema().names())

    if len(column_names) != n_cols:
        logger.warning(
            f"Column count mismatch: schema has {len(column_names)} columns, "
            f"file has {n_cols} columns. Using first {min(len(column_names), n_cols)} columns."
        )
        column_names = column_names[:n_cols]

    # Create column mapping (column_1 -> actual_name)
    rename_dict = {f"column_{i + 1}": name for i, name in enumerate(column_names)}
    return lf.rename(rename_dict)


# ---------------------------------------------------------------------------
# RPL (Reperforming) Data Import
# ---------------------------------------------------------------------------


def import_rpl_data(schemas: dict[str, dict]) -> None:
    """Import RPL (reperforming) loan matching data.

    Args:
        schemas: Dict of schemas including 'reperforming' schema
    """
    rpl_zip = FHLMCConfig.FHLMC_RAW_DIR / "rpl_historical_data.zip"

    if not rpl_zip.exists():
        logger.warning(f"RPL zip file not found: {rpl_zip}")
        return

    logger.info(f"Processing {rpl_zip.name}")
    schema = schemas.get("reperforming")

    with zipfile.ZipFile(rpl_zip, "r") as outer_zip:
        # Extract nested zips
        nested_zips = [f for f in outer_zip.namelist() if f.endswith(".zip")]

        for nested_zip_name in nested_zips:
            logger.info(f"  Processing {nested_zip_name}")

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Extract nested zip
                outer_zip.extract(nested_zip_name, temp_path)
                nested_zip_path = temp_path / nested_zip_name

                # Process CSV files in nested zip
                _process_rpl_nested_zip(nested_zip_path, schema, temp_path)


def _process_rpl_nested_zip(nested_zip_path: Path, schema: dict | None, temp_path: Path) -> None:
    """Process nested RPL zip file containing CSV files.

    Args:
        nested_zip_path: Path to nested zip file
        schema: Schema dict for RPL data (optional)
        temp_path: Temporary directory path
    """
    with zipfile.ZipFile(nested_zip_path, "r") as inner_zip:
        csv_files = [f for f in inner_zip.namelist() if f.endswith(".csv")]

        for csv_file in csv_files:
            logger.info(f"    Processing {csv_file}")

            inner_zip.extract(csv_file, temp_path)
            csv_path = temp_path / csv_file

            # Import CSV (these have headers)
            _import_rpl_csv(csv_path, schema)


def _import_rpl_csv(csv_path: Path, schema: dict | None) -> None:
    """Import RPL CSV file (has headers, comma-delimited).

    Args:
        csv_path: Path to CSV file
        schema: Schema dict (optional)
    """
    logger.info(f"      Loading {csv_path.name}")

    # Load as strings
    lf = pl.scan_csv(
        csv_path,
        separator=",",
        has_header=True,
        infer_schema_length=0,  # Load all as strings
    )

    # Apply type conversions if schema available
    if schema:
        lf = apply_type_conversions(lf, schema)

    # Save to parquet
    output_file = generate_output_path(csv_path, FHLMCConfig.FHLMC_BRONZE_REPERFORMING)

    logger.info(f"      Saving to {output_file}")
    lf.sink_parquet(
        output_file,
        compression=FHLMCConfig.PARQUET_COMPRESSION,
        statistics=FHLMCConfig.PARQUET_STATISTICS,
    )

    logger.info(f"      ✓ Saved {output_file.name}")
