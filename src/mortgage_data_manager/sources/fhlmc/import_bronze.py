"""Import functions for FHLMC bronze layer data.

This module handles importing raw FHLMC data files to bronze layer (Parquet),
including historical loan data (origination + performance) and RPL data.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig

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
                    "Date format not specified for %s, "
                    "keeping as string. Inspect data to determine format.",
                    col_name,
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
        logger.debug("Ensured directory exists: %s", dir_path)


# ---------------------------------------------------------------------------
# Historical Data Import (Origination + Performance)
# ---------------------------------------------------------------------------

# Raw bundles that must never be walked by the generic historical importer: the RPL
# bundle has its own importer and schema, and the CRT reference-pool bundle is not
# ingested by this package. Their members are ``.txt`` files that the walk would
# otherwise misparse as origination records. A year filter is no defence here — CRT
# members like ``13DN01_20130701_lld.txt`` carry a 4-digit year, so a wide-year run
# would import ~250 of them into bronze/origination.
_DEDICATED_IMPORTER_ZIPS: tuple[str, ...] = ("rpl_historical_data.zip",)
_DEDICATED_IMPORTER_PREFIXES: tuple[str, ...] = ("fre-crt-",)

_YEAR_IN_NAME = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _has_dedicated_importer(zip_name: str) -> bool:
    """Return True if a raw zip is handled by an importer other than the historical walk.

    Args:
        zip_name: Bare filename of a top-level zip in the raw directory.

    Returns:
        True when a dedicated importer owns the zip.
    """
    return zip_name in _DEDICATED_IMPORTER_ZIPS or zip_name.startswith(_DEDICATED_IMPORTER_PREFIXES)


def _classify_leaf(leaf_name: str) -> tuple[str, str, str] | None:
    """Resolve a ``.txt`` leaf to (dataset, schema key, bronze output stem).

    Two layout eras share the raw directory (see the Release 47 notes in
    ``schemas.py``). The R47 standard dataset ships ``orig_YYYYQn.txt`` /
    ``perf_YYYYQn.txt``; the non-standard bundle still ships the pre-R47
    ``historical_data_excl_YYYYQn.txt`` shape. Dataset detection therefore keys
    on the ``orig_``/``perf_`` prefixes first and only then falls back to the
    old ``_time_`` substring rule.

    Bronze *filenames* deliberately stay on the pre-R47 convention
    (``historical_data_YYYYQn`` / ``historical_data_time_YYYYQn``) even for R47
    input: a dozen modules and downstream scripts glob those names, and the
    layout change is carried by the column names, not the paths.

    Args:
        leaf_name: Bare filename of the ``.txt`` leaf inside the raw zip.

    Returns:
        ``(dataset, schema_key, output_stem)``, or None if unrecognized.
    """
    stem = Path(leaf_name).stem
    if stem.startswith("orig_"):
        return "origination", "origination_r47", f"historical_data_{stem[len('orig_') :]}"
    if stem.startswith("perf_"):
        return "performance", "performance_r47", f"historical_data_time_{stem[len('perf_') :]}"
    if "_time_" in stem:
        return "performance", "performance", stem
    if stem.startswith("historical_data_"):
        return "origination", "origination", stem
    return None


def _zip_excluded_by_year(zip_name: str, year_filter: set[str] | None) -> bool:
    """Return True if a nested zip's name carries years and none of them are wanted.

    The nested standard-dataset zips are named ``historical_data_YYYY.zip`` and
    ``historical_data_YYYYQn.zip``, so a targeted run can prune whole branches
    instead of extracting all 28 year bundles (~43 GB) to temp just to filter the
    ``.txt`` leaves inside them. Names carrying no year are always descended
    into, since nothing can be concluded from them.

    Args:
        zip_name: Bare filename of the nested zip member.
        year_filter: Wanted years as strings, or None for no filtering.

    Returns:
        True when the name carries years and none of them are wanted.
    """
    if year_filter is None:
        return False
    years_in_name = {m.group(0) for m in _YEAR_IN_NAME.finditer(zip_name)}
    return bool(years_in_name) and years_in_name.isdisjoint(year_filter)


def import_all_historical_data(
    schemas: dict[str, dict],
    raw_dir: Path | None = None,
    years: Iterable[int] | None = None,
    overwrite: bool = False,
    datasets: Iterable[str] | None = None,
    zip_names: Iterable[str] | None = None,
) -> None:
    """Recursively import every pipe-delimited .txt file found in raw zip archives.

    Walks each ``*.zip`` in ``raw_dir`` (excluding the RPL and CRT bundles; see
    ``_DEDICATED_IMPORTER_ZIPS``) and descends into any nested zips,
    regardless of layering. At the leaves, every ``.txt`` is imported — dataset,
    layout era and bronze filename are resolved by `_classify_leaf`, so the
    R47 ``orig_``/``perf_`` standard dataset and the pre-R47 non-standard
    ``historical_data_excl_*`` bundle can be ingested in the same pass. This
    handles standalone yearly zips, the bundled full-set zip, the non-standard
    bundle, or any other layered ``.zip`` of the same shape with no
    source-specific code.

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
        if not _has_dedicated_importer(p.name) and (name_filter is None or p.name in name_filter)
    )

    # Two raw bundles can map to the SAME bronze output: the R47 full set holds
    # orig_2019Q1.txt while a superseded per-year zip still holds the pre-R47
    # historical_data_2019Q1.txt, and both land on historical_data_2019Q1.parquet.
    # Without arbitration the later writer wins by alphabetical accident, which
    # silently leaves stale pre-R47 data in bronze. Track what this pass wrote and
    # let the newer layout win regardless of walk order.
    written: dict[Path, str] = {}
    for zip_path in zip_files:
        logger.info("Processing %s", zip_path.name)
        _walk_zip_for_txt(zip_path, schemas, year_filter, overwrite, dataset_filter, written)


def _walk_zip_for_txt(
    zip_path: Path,
    schemas: dict[str, dict],
    year_filter: set[str] | None,
    overwrite: bool,
    dataset_filter: set[str] | None = None,
    written: dict[Path, str] | None = None,
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
        written: Output path -> schema key already written by this pass, used to
            arbitrate when two raw sources target the same bronze file.
    """
    if written is None:
        written = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for entry in zf.namelist():
            if entry.endswith("/"):
                continue
            if entry.endswith(".zip"):
                if _zip_excluded_by_year(Path(entry).name, year_filter):
                    logger.debug("  Skipping %s (outside requested years)", entry)
                    continue
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    zf.extract(entry, temp_path)
                    _walk_zip_for_txt(
                        temp_path / entry, schemas, year_filter, overwrite, dataset_filter, written
                    )
            elif entry.endswith(".txt"):
                entry_name = Path(entry).name
                if year_filter is not None and not any(y in entry_name for y in year_filter):
                    continue

                classified = _classify_leaf(entry_name)
                if classified is None:
                    logger.debug("  Skipping unrecognized leaf: %s", entry_name)
                    continue
                dataset, schema_key, output_stem = classified

                if dataset_filter is not None and dataset not in dataset_filter:
                    continue

                output_dir = (
                    FHLMCConfig.FHLMC_BRONZE_ORIGINATION
                    if dataset == "origination"
                    else FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
                )
                output_file = output_dir / f"{output_stem}.parquet"

                prior = written.get(output_file)
                if prior is not None:
                    if prior.endswith("_r47") or not schema_key.endswith("_r47"):
                        logger.info(
                            "  Skipping %s (%s already written from the %s layout this pass)",
                            entry_name,
                            output_file.name,
                            prior,
                        )
                        continue
                    logger.warning(
                        "  Rebuilding %s from %s: the R47 layout supersedes the %s copy "
                        "written earlier this pass",
                        output_file.name,
                        entry_name,
                        prior,
                    )
                elif not should_process_file(output_file, overwrite=overwrite):
                    logger.info("  Skipping %s (bronze exists)", entry_name)
                    continue

                logger.info("  Processing %s as %s -> %s", entry_name, schema_key, output_file.name)
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    zf.extract(entry, temp_path)
                    _import_txt_file(temp_path / entry, schema_key, schemas, output_file)
                written[output_file] = schema_key
            else:
                logger.debug("  Skipping unrecognized entry: %s", entry)


def _import_txt_file(
    txt_path: Path,
    schema_key: str,
    schemas: dict[str, dict],
    output_file: Path,
) -> None:
    """Import a pipe-delimited text file using polars.

    Args:
        txt_path: Path to the text file
        schema_key: Schema to name columns with ('origination', 'performance',
            'origination_r47', 'performance_r47')
        schemas: Dict of schemas
        output_file: Bronze parquet destination (its name may differ from the
            source stem — R47 input is written under the pre-R47 filename)
    """
    schema = schemas.get(schema_key)

    if not schema:
        logger.error("No schema found for: %s", schema_key)
        return

    logger.info("      Loading %s (infer_schema=False)", txt_path.name)

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

    output_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("      Saving to %s", output_file)
    with atomic_output(output_file) as tmp:
        lf.sink_parquet(
            tmp,
            compression=FHLMCConfig.PARQUET_COMPRESSION,
            statistics=FHLMCConfig.PARQUET_STATISTICS,
            row_group_size=FHLMCConfig.PARQUET_ROW_GROUP_SIZE,
        )

    logger.info("      ✓ Saved %s", output_file.name)


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
            "Column count mismatch: schema has %s columns, "
            "file has %s columns. Using first %s columns.",
            len(column_names),
            n_cols,
            min(len(column_names), n_cols),
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
        logger.warning("RPL zip file not found: %s", rpl_zip)
        return

    logger.info("Processing %s", rpl_zip.name)
    schema = schemas.get("reperforming")

    with zipfile.ZipFile(rpl_zip, "r") as outer_zip:
        # Extract nested zips
        nested_zips = [f for f in outer_zip.namelist() if f.endswith(".zip")]

        for nested_zip_name in nested_zips:
            logger.info("  Processing %s", nested_zip_name)

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
            logger.info("    Processing %s", csv_file)

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
    logger.info("      Loading %s", csv_path.name)

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

    logger.info("      Saving to %s", output_file)
    with atomic_output(output_file) as tmp:
        lf.sink_parquet(
            tmp,
            compression=FHLMCConfig.PARQUET_COMPRESSION,
            statistics=FHLMCConfig.PARQUET_STATISTICS,
        )

    logger.info("      ✓ Saved %s", output_file.name)
