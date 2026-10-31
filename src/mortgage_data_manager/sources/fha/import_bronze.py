"""Bronze layer import: Convert raw FHA Excel snapshots to Parquet format."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from multiprocessing import get_context
from os import cpu_count
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file

from .config import SnapshotType

if TYPE_CHECKING:
    import fastexcel

logger = get_logger(__name__)

# Column dtype mappings for FHA snapshots

SF_DATA_TYPES: dict[str, type[pl.DataType]] = {
    "Property State": pl.Utf8,
    "Property City": pl.Utf8,
    "Property County": pl.Utf8,
    "Property Zip": pl.Int32,
    "Originating Mortgagee": pl.Utf8,
    "Originating Mortgagee Number": pl.Int32,
    "Sponsor Name": pl.Utf8,
    "Sponsor Number": pl.Int32,
    "Down Payment Source": pl.Utf8,
    "Non Profit Number": pl.Int64,
    "Product Type": pl.Utf8,
    "Loan Purpose": pl.Utf8,
    "Property Type": pl.Utf8,
    "Interest Rate": pl.Float64,
    "Mortgage Amount": pl.Int64,
    "Year": pl.Int16,
    "Month": pl.Int16,
}

HECM_DATA_TYPES: dict[str, type[pl.DataType]] = {
    "Property State": pl.Utf8,
    "Property City": pl.Utf8,
    "Property County": pl.Utf8,
    "Property Zip": pl.Int32,
    "Originating Mortgagee": pl.Utf8,
    "Originating Mortgagee Number": pl.Int32,
    "Sponsor Name": pl.Utf8,
    "Sponsor Number": pl.Int32,
    "Sponsor Originator": pl.Utf8,
    "NMLS": pl.Int64,
    "Standard/Saver": pl.Utf8,
    "Purchase/Refinance": pl.Utf8,
    "Rate Type": pl.Utf8,
    "Interest Rate": pl.Float64,
    "Initial Principal Limit": pl.Float64,
    "Maximum Claim Amount": pl.Float64,
    "Year": pl.Int16,
    "Month": pl.Int16,
    "HECM Type": pl.Utf8,
    "Current Servicer ID": pl.Int64,
    "Previous Servicer ID": pl.Int64,
}


#: Columns whose numeric cast is expected to null real text: "Not Available" /
#: "Not Avail" are long-standing legitimate content in the agency's mortgagee
#: and non-profit identifier columns, so their losses log at DEBUG. Loss in any
#: other column logs at ERROR — it is the signature of a vintage delivering a
#: numeric field as text (e.g. an interest rate with a "%" suffix), which the
#: strict=False cast would otherwise destroy silently.
_EXPECTED_CAST_LOSS_COLUMNS: frozenset[str] = frozenset(
    {"Originating Mortgagee Number", "Sponsor Number", "Non Profit Number"}
)


#: HECM identifier columns screened for fabricated values before the Int64 cast.
_HECM_IDENTIFIER_COLUMNS: tuple[str, ...] = (
    "Current Servicer ID",
    "Previous Servicer ID",
    "NMLS",
)


def _null_fabricated_hecm_identifiers(df: pl.DataFrame) -> pl.DataFrame:
    """Null HECM servicer/NMLS identifiers that are negative or fractional.

    One published HECM workbook carries these three columns as an Excel
    linear-trend fill dragged down from its first few genuine rows, so most of
    their values are a function of row position rather than of the loan: they
    run negative and carry a fractional part, neither of which an identifier can
    have. The fraction is the discriminating evidence and the ``Int64`` cast that
    follows truncates it away, leaving the invented IDs indistinguishable from
    real ones -- so they are dropped here, while the genuine seed values (plain
    non-negative integers) survive. The rule is a property of an identifier, not
    a date-pinned patch, so a recurrence in a future vintage is caught too.

    Expects the identifier columns to have been cast to Float64 already.

    Returns:
        The frame with the fabricated identifier values replaced by null.
    """
    for column in _HECM_IDENTIFIER_COLUMNS:
        if column not in df.columns:
            continue
        values = df.get_column(column)
        fabricated = values.is_not_null() & ((values < 0) | (values % 1 != 0))
        nulled = int(fabricated.sum())
        if not nulled:
            continue
        logger.warning(
            "HECM column %r: nulled %d of %d non-null values that are negative or "
            "fractional and so cannot be identifiers.",
            column,
            nulled,
            int(values.is_not_null().sum()),
        )
        df = df.with_columns(pl.when(fabricated).then(pl.lit(None)).otherwise(values).alias(column))
    return df


def _cast_numeric_reporting_loss(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Cast ``column`` to Float64 (non-strict), reporting values the cast destroyed.

    A ``strict=False`` cast converts unparseable text to null without a trace.
    This wrapper counts the values that were non-null before and null after,
    and names up to five of them, so schema drift in a new vintage surfaces in
    the log instead of as silently missing data.

    Returns:
        The frame with ``column`` cast to Float64.
    """
    before = df.get_column(column)
    casted = before.cast(pl.Float64, strict=False)
    lost_mask = before.is_not_null() & casted.is_null()
    lost = int(lost_mask.sum())
    if lost:
        examples = before.filter(lost_mask).unique().head(5).to_list()
        log = logger.debug if column in _EXPECTED_CAST_LOSS_COLUMNS else logger.error
        log(
            "Numeric cast of %r nulled %d of %d non-null values; examples: %s",
            column,
            lost,
            int(before.is_not_null().sum()),
            examples,
        )
    return df.with_columns(casted.alias(column))


@dataclass(frozen=True)
class _SnapshotConversionTask:
    """Encapsulate the information needed to convert a monthly snapshot."""

    input_file: Path
    output_file: Path
    year: int
    month: int
    snapshot_type: SnapshotType


def _run_parallel_conversions(tasks: list[_SnapshotConversionTask]) -> None:
    """Execute snapshot conversion tasks, leveraging multiprocessing when useful."""
    if not tasks:
        return

    # ``spawn`` works across platforms and avoids issues when the project is embedded in
    # other applications. Fallback to a sequential loop if only one task needs work.
    process_count = min(len(tasks), max(1, cpu_count() or 1))

    if process_count <= 1:
        for task in tasks:
            _convert_snapshot(task)
        return

    ctx = get_context("spawn")
    with ctx.Pool(processes=process_count) as pool:
        pool.map(_convert_snapshot, tasks)


def clean_sf_sheets(df: pl.DataFrame) -> pl.DataFrame:
    """Clean Excel sheets for FHA single-family data using Polars.

    Args:
        df: Raw single-family data.

    Returns:
        Cleaned single-family data.
    """
    # Initial column name cleaning:
    # 1. Strip whitespace from column names
    # 2. Replace underscores with spaces in column names
    df = df.rename(lambda col: col.strip() if isinstance(col, str) else col)
    df = df.rename(lambda col: col.replace("_", " ") if isinstance(col, str) else col)

    # Rename Columns to Standardize - only rename columns that exist
    rename_dict: dict[str, str] = {
        "Endorsement Month": "Month",
        "Original Mortgage Amount": "Mortgage Amount",
        "Origination Mortgagee/Sponsor Originator": "Originating Mortgagee",
        "Origination Mortgagee Sponsor Or": "Originating Mortgagee",
        "Orig Num": "Originating Mortgagee Number",
        "Property/Product Type": "Property Type",
        "Property Type Final": "Property Type",
        "Sponosr Number": "Sponsor Number",
        "Sponsor Num": "Sponsor Number",
        "Endorsement  Year": "Year",
        "Endorsment Year": "Year",
        "Endorsement Year": "Year",
    }
    rename_dict_filtered = {old: new for old, new in rename_dict.items() if old in df.columns}
    df = df.rename(rename_dict_filtered)

    # Drop unnamed columns
    unnamed_cols = [col for col in df.columns if "unnamed" in col.lower()]
    if unnamed_cols:
        df = df.drop(unnamed_cols)

    # Convert numeric columns
    numeric_columns = [
        "Property Zip",
        "Originating Mortgagee Number",
        "Sponsor Number",
        "Non Profit Number",
        "Interest Rate",
        "Mortgage Amount",
        "Year",
        "Month",
    ]

    for column in numeric_columns:
        if column in df.columns:
            df = _cast_numeric_reporting_loss(df, column)

    # Drop header rows that a few vintages repeat inside the data block. The
    # column-name cleaning above rewrites underscores to spaces in *names*, not
    # values, so the leaked cell can carry either spelling.
    if "Loan Purpose" in df.columns:
        df = df.filter(~pl.col("Loan Purpose").is_in(["Loan Purpose", "Loan_Purpose"]))

    # Replace bad loan purposes for 2016
    if "Loan Purpose" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("Loan Purpose").is_in(["Fixed Rate", "Adjustable Rate"]))
            .then(pl.lit("Purchase"))
            .when(pl.col("Loan Purpose").is_in(["Rehabilitation", "Single Family"]))
            .then(pl.lit("Purchase"))
            .otherwise(pl.col("Loan Purpose"))
            .alias("Loan Purpose")
        )

        # Hyphens to underscores: "Refi-Conv-Curr" -> "Refi_Conv_Curr". Must be
        # replace_all -- str.replace defaults to n=1, so a single pass leaves
        # "Refi_Conv-Curr", which the silver Loan Purpose enum nulls. This ran
        # twice, in two places, to work around that.
        df = df.with_columns(pl.col("Loan Purpose").str.replace_all("-", "_").alias("Loan Purpose"))

    # Standardize down payment types
    if "Down Payment Source" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("Down Payment Source") == "NonProfit")
            .then(pl.lit("Non Profit"))
            .when(
                (pl.col("Down Payment Source").cast(pl.Utf8).str.strip_chars() == "")
                | (pl.col("Down Payment Source") == "nan")
            )
            .then(None)
            .otherwise(pl.col("Down Payment Source"))
            .alias("Down Payment Source")
        )

    # Fix county names and sponsor names
    if "Property County" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("Property County") == "#NULL!")
            .then(None)
            .otherwise(pl.col("Property County"))
            .alias("Property County")
        )

    if "Sponsor Name" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("Sponsor Name") == "Not Available")
            .then(None)
            .otherwise(pl.col("Sponsor Name"))
            .alias("Sponsor Name")
        )

    # Convert to appropriate data types based on schema
    df = df.with_columns([pl.col(c).cast(dt) for c, dt in SF_DATA_TYPES.items() if c in df.columns])

    return df


# Sheet-name tokens that mark the loan-level single-family records. Unlike the
# HECM tokens this set is case-sensitive on "Data"; no vintage uses the lower
# case spelling and widening it here would change which sheets are read.
_SF_SHEET_TOKENS: tuple[str, ...] = ("Data", "Purchase", "Refinance")


def _read_sf_frames(reader: fastexcel.ExcelReader) -> list[pl.DataFrame]:
    """Load and clean every single-family sheet carrying loan-level records."""
    frames: list[pl.DataFrame] = []
    for sheet in [x for x in reader.sheet_names if any(t in x for t in _SF_SHEET_TOKENS)]:
        try:
            frames.append(clean_sf_sheets(reader.load_sheet(sheet).to_polars()))
        except Exception as exc:
            logger.warning("Error reading sheet %s: %s", sheet, exc)
    return frames


# Sheet-name tokens that mark the loan-level HECM records.
_HECM_SHEET_TOKENS: tuple[str, ...] = ("Data", "data", "Purchase", "Refinance")

# Columns present on every HECM records sheet. Used to tell a records sheet from
# a cover page, and to find the real header under the banner rows described below.
_HECM_SIGNATURE_COLUMNS: frozenset[str] = frozenset(
    {"Property State", "Property County", "Property Zip"}
)

# How many leading rows to probe for that header. The 2011-04 through 2011-07
# vintages carry a two-row banner, so the header can sit as low as row 2.
_HECM_MAX_HEADER_ROW = 3


def _load_hecm_sheet(reader: fastexcel.ExcelReader, sheet: str) -> pl.DataFrame | None:
    """Load one HECM sheet, looking past an advisory banner row when present.

    The 2009-05 through 2011-07 vintages open the records sheet with one or two
    advisory rows ("Column I is a new field and Column J has been modified:",
    "*Nationwide Mortgage Licensing..."), which park the real header further down.

    Returns:
        The loaded sheet, or None when it carries no loan-level records.
    """
    for header_row in range(_HECM_MAX_HEADER_ROW + 1):
        try:
            df = reader.load_sheet(sheet, header_row=header_row).to_polars()
        except Exception as exc:
            logger.warning("Error reading sheet %s: %s", sheet, exc)
            return None
        if _HECM_SIGNATURE_COLUMNS <= {str(col).strip() for col in df.columns}:
            return df
    return None


def _read_hecm_frames(reader: fastexcel.ExcelReader, sheets: Sequence[str]) -> list[pl.DataFrame]:
    """Load and clean every sheet in ``sheets`` that carries HECM records."""
    frames: list[pl.DataFrame] = []
    for sheet in sheets:
        df = _load_hecm_sheet(reader, sheet)
        if df is not None:
            frames.append(clean_hecm_sheets(df))
    return frames


def clean_hecm_sheets(df: pl.DataFrame) -> pl.DataFrame:
    """Clean HECM sheets using Polars.

    Args:
        df: Raw HECM data.

    Returns:
        Cleaned HECM data.
    """
    # Initial column name cleaning:
    # 1. Strip whitespace from column names
    # 2. Replace underscores with spaces in column names
    df = df.rename(lambda col: col.strip() if isinstance(col, str) else col)
    df = df.rename(lambda col: col.replace("_", " ") if isinstance(col, str) else col)

    # Rename columns
    rename_dict: dict[str, str] = {
        "NMLS*": "NMLS",
        "Sponosr Number": "Sponsor Number",
        "Standard Saver": "Standard/Saver",
        "Purchase /Refinance": "Purchase/Refinance",
        "Purchase Refinance": "Purchase/Refinance",
        "RefinanceType*": "RefinanceType",
        "Refinance Type*": "RefinanceType",
        "Previous Servicer": "Previous Servicer ID",
        "Endorsement Year": "Year",
        "Endorsement Month": "Month",
        "Hecm Type": "HECM Type",
        "Originating Mortgagee/Sponsor Originator": "Originating Mortgagee",
        "Originating Mortgagee Sponsor Originator": "Originating Mortgagee",
        "Originating Mortgagee Sponsor Or": "Originating Mortgagee",
        "Sponsored Originator": "Sponsor Originator",
    }
    rename_dict_filtered = {old: new for old, new in rename_dict.items() if old in df.columns}
    df = df.rename(rename_dict_filtered)

    # Drop unnamed columns and junk header columns
    cols_to_drop = [
        col
        for col in df.columns
        if "unnamed" in col.lower()
        or col.startswith("*Colum")
        or col.startswith("Column I")
        or "Modified to reflect" in col
        or col in ["New", "Modified", "New 1", "New 2"]
    ]
    if cols_to_drop:
        df = df.drop(cols_to_drop)

    # Replace "Not Available" and null values with None
    for col in df.columns:
        # if string column, replace 'Not Available' and null values with None
        if df.schema[col] in [pl.Utf8, pl.Categorical, pl.String]:
            df = df.with_columns(
                pl.when(pl.col(col).is_in(["Not Available", "nan", "None"]))
                .then(pl.lit(None))
                .otherwise(pl.col(col))
                .alias(col)
            )

    # Convert numeric columns
    numeric_cols = [
        "Property Zip",
        "Originating Mortgagee Number",
        "Sponsor Number",
        "NMLS",
        "Interest Rate",
        "Initial Principal Limit",
        "Maximum Claim Amount",
        "Year",
        "Month",
        "Current Servicer ID",
        "Previous Servicer ID",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df = _cast_numeric_reporting_loss(df, col)

    # Before the Int64 cast below truncates the fractional part that exposes them.
    df = _null_fabricated_hecm_identifiers(df)

    # Convert to appropriate data types based on schema
    df = df.with_columns(
        [pl.col(c).cast(dt) for c, dt in HECM_DATA_TYPES.items() if c in df.columns]
    )

    return df


def _read_hecm_records(reader: fastexcel.ExcelReader) -> list[pl.DataFrame]:
    """Load and clean every HECM sheet carrying loan-level records."""
    named_sheets = [x for x in reader.sheet_names if any(t in x for t in _HECM_SHEET_TOKENS)]
    frames = _read_hecm_frames(reader, named_sheets)
    if not frames:
        # A few vintages file the records under a sheet the tokens miss --
        # 2013-12 uses "Report Generator December 2013" -- so fall back to
        # selecting by content instead of by name.
        frames = _read_hecm_frames(reader, [x for x in reader.sheet_names if x not in named_sheets])
    return frames


@dataclass(frozen=True)
class _BronzeSpec:
    """Per-dataset differences in the bronze conversion.

    ``read_frames`` is a callable rather than data because the two products
    genuinely diverge: HECM vintages need banner-row probing and a content-based
    sheet fallback that the single-family workbooks do not.
    """

    file_stem: str
    concat_how: Literal["diagonal", "diagonal_relaxed"]
    index_prefix: str
    read_frames: Callable[[fastexcel.ExcelReader], list[pl.DataFrame]]


_BRONZE_SPECS: dict[SnapshotType, _BronzeSpec] = {
    "single_family": _BronzeSpec(
        file_stem="fha_sf_snapshot",
        concat_how="diagonal_relaxed",
        index_prefix="",
        read_frames=_read_sf_frames,
    ),
    "hecm": _BronzeSpec(
        file_stem="fha_hecm_snapshot",
        concat_how="diagonal",
        index_prefix="H",
        read_frames=_read_hecm_records,
    ),
}


def _verify_written_rows(output_file: Path, expected: int) -> None:
    """Check the parquet on disk holds exactly the rows the builder produced.

    A short bronze file is indistinguishable from a complete one on the next run
    -- ``should_process_file`` skips whatever exists -- so a truncated write is
    permanent and silent. Deleting the bad output is what keeps the failure
    retryable.

    Raises:
        ValueError: If the file is empty or its row count differs from
            ``expected``. The partial output is removed first.
    """
    written = int(pl.scan_parquet(output_file).select(pl.len()).collect().item())
    if written == expected and written > 0:
        return

    output_file.unlink(missing_ok=True)
    msg = (
        f"Bronze row-count check failed for {output_file.name}: built {expected:,} rows, "
        f"file holds {written:,}. Removed the partial output."
    )
    raise ValueError(msg)


def _convert_snapshot(task: _SnapshotConversionTask) -> None:
    """Worker function for converting one monthly snapshot workbook to parquet."""
    logger.info("Reading and Converting File: %s", task.input_file)
    spec = _BRONZE_SPECS[task.snapshot_type]

    import fastexcel

    try:
        frames = spec.read_frames(fastexcel.read_excel(task.input_file))

        if not frames:
            logger.warning("No relevant sheets found in %s", task.input_file)
            return

        df = pl.concat(frames, how=spec.concat_how)

        # Add FHA_Index
        df = (
            df.with_columns((pl.int_range(0, len(df)) + 1).alias("row_num"))
            .with_columns(
                pl.concat_str(
                    [
                        pl.lit(f"{spec.index_prefix}{task.year}{task.month:02d}01_"),
                        pl.col("row_num").cast(pl.Utf8).str.zfill(7),
                    ]
                ).alias("FHA_Index")
            )
            .drop("row_num")
        )

        with atomic_output(task.output_file) as tmp:
            df.write_parquet(tmp)

    except Exception as exc:
        # Read/convert failures leave whatever was already on disk alone: with
        # overwrite=True that is the previous good vintage, and discarding it
        # would turn a bad read into data loss.
        logger.error("Error converting file %s: %s", task.input_file, exc)
        return

    # Deliberately outside the handler above: a file that does not hold the rows
    # it was just given is a corrupt artifact, not a snapshot to skip and carry
    # on from, so it aborts the run rather than being logged and forgotten.
    _verify_written_rows(task.output_file, df.height)


def _convert_snapshots(
    snapshot_type: SnapshotType,
    data_folder: Path,
    save_folder: Path,
    overwrite: bool = False,
) -> None:
    """Convert every raw monthly snapshot of one dataset to bronze parquet."""
    spec = _BRONZE_SPECS[snapshot_type]
    save_folder.mkdir(parents=True, exist_ok=True)

    tasks: list[_SnapshotConversionTask] = []

    # Read data file-by-file
    for year in range(2010, 2099):
        for mon in range(1, 13):
            files = sorted(data_folder.glob(f"{spec.file_stem}_{year}{mon:02d}01*.xls*"))
            if not files:
                continue

            output_file = save_folder / f"{spec.file_stem}_{year}{mon:02d}01.parquet"

            if not should_process_file(output_file, overwrite=overwrite):
                logger.info("File %s already exists!", output_file)
                continue

            tasks.append(
                _SnapshotConversionTask(
                    input_file=files[0],
                    output_file=output_file,
                    year=year,
                    month=mon,
                    snapshot_type=snapshot_type,
                )
            )

    logger.info("Found %d files to process", len(tasks))
    _run_parallel_conversions(tasks)


def convert_fha_sf_snapshots(data_folder: Path, save_folder: Path, overwrite: bool = False) -> None:
    """Convert raw single-family snapshots to cleaned parquet files using Polars.

    Args:
        data_folder: Directory containing the raw Excel monthly SF snapshots.
        save_folder: Directory where cleaned parquet snapshots are saved.
        overwrite: Whether to overwrite output files if a version already exists.
            The default is False.

    Returns:
        None.
    """
    _convert_snapshots("single_family", data_folder, save_folder, overwrite=overwrite)


def convert_fha_hecm_snapshots(
    data_folder: Path, save_folder: Path, overwrite: bool = False
) -> None:
    """Convert raw HECM snapshots to cleaned parquet files using Polars.

    Args:
        data_folder: Directory containing the raw Excel monthly HECM snapshots.
        save_folder: Directory where cleaned parquet HECM snapshots are saved.
        overwrite: Whether to overwrite output files if a version already exists.
            The default is False.

    Returns:
        None.
    """
    _convert_snapshots("hecm", data_folder, save_folder, overwrite=overwrite)
