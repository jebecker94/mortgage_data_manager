"""HMDA silver-layer builders (era-split: pre-2007, 2007-2017, post-2018).

Each HMDA era has its own raw format, so silver construction is split into
three era-specific builders rather than a single function. All three read the
flat per-dataset bronze directory (``bronze/<dataset>/``, every era side by
side) and write to the era-split silver layer:

    - build_silver_pre2007: legacy LAR (1981-2006).
    - build_silver_period_2007_2017: standardized period (2007-2017).
    - build_silver_post2018: modern format (2018+).

Loans silver is one parquet per ``activity_year=YYYY/file_type=X`` release partition.
A build skips the partitions already on disk; ``overwrite`` rebuilds a year in a
sibling temp tree and swaps it in only when every partition is written and its row
count verified, so a failed rebuild leaves the previous year intact.

Silver also derives, once for every consumer: ``number_of_borrowers`` and
``purchaser_type_label`` on the 2007-2017 and post-2018 loans, ``respondent_key`` on
the 2007-2017 loans and panel, and the latest panel name per LEI
(`build_silver_lei_names`). Pre-2018 ``income`` 9999 (thousands) is the ceiling of
its 4-character field, not an amount, and becomes null.

The 2017 nationwide file (``file_type=d``) publishes ``edit_status``,
``sequence_number`` and ``application_date_indicator`` empty on every row (the CFPB's
own CSV), and the 2017 three-year file (``a``) has no such columns, so all three are
null for 2017. ``HMDAIndex`` is the per-record identifier there.

Example:
    >>> from mortgage_data_manager.sources.hmda.import_silver import build_silver_post2018
    >>> build_silver_post2018("loans", min_year=2020, max_year=2024)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import (
    atomic_output,
    atomic_output_dir,
    should_process_file,
)
from mortgage_data_manager.core.types import (
    apply_silver_types,
    enforce_schema,
    null_sentinels,
    strip_column_names,
)
from mortgage_data_manager.sources.hmda.config import (
    DERIVED_COLUMNS,
    HMDA_INDEX_COLUMN,
    HMDA_SILVER_SPECS,
    PANEL_2007_2017_RENAMES,
    PERIOD_2007_2017_FLOAT_COLUMNS,
    PERIOD_2007_2017_INTEGER_COLUMNS,
    POST2018_EXEMPT_COLUMNS,
    POST2018_FLOAT_COLUMNS,
    POST2018_INTEGER_COLUMNS,
    PRE2018_INCOME_CEILING_000S,
    RENAME_DICTIONARY,
    TRANSMITTAL_SERIES_2007_2017_RENAMES,
    HMDAConfig,
)
from mortgage_data_manager.sources.hmda.utils import (
    append_hmda_index,
    check_expected_row_count,
    number_of_borrowers,
    purchaser_type_label,
    respondent_key,
)

#: A standalone 4-digit year token in a bronze filename (not part of a longer run
#: of digits), e.g. ``hmda_2007_panel`` or ``2019_public_lar_one_year``.
_YEAR_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _bronze_files_for_year(bronze_dir: Path, year: int) -> list[Path]:
    """Return the bronze parquet files belonging to one activity year.

    Bronze is a flat directory per dataset — every era's files sit side by side,
    since the era is recoverable from the filename's year. That makes a bare
    ``glob(f"*{year}*")`` load-bearing in a way it was not when each era had its
    own folder, so match on a standalone year *token* instead: a substring test
    would let a filename that merely contains the digits (a future vintage or
    range suffix) be pulled into the wrong era's builder, which would silently
    mix schemas and code spaces rather than fail.

    Returns:
        The bronze parquet files for that activity year, sorted.
    """
    return sorted(
        f for f in bronze_dir.glob("*.parquet") if str(year) in _YEAR_TOKEN.findall(f.stem)
    )


def _apply_hmda_spec(lf: pl.LazyFrame, dataset: str, era: str) -> pl.LazyFrame:
    """Apply the (dataset, era) close-to-final dtype spec and assert the schema.

    Right-sizes the Int64 code columns to the smallest signed int that holds their
    full range (sentinels stay live so matchers detecting -99999/1111/9999 keep
    working), keeps geo/identifier columns Utf8, and conforms to the per-era target
    so every year/file_type shares one schema. No-op if no spec is registered.

    Returns:
        The frame conformed to the era's target schema, or unchanged when no
        spec is registered.
    """
    spec = HMDA_SILVER_SPECS.get((dataset, era))
    if spec is None:
        return lf
    lf = apply_silver_types(lf, spec)
    enforce_schema(lf.collect_schema(), spec.target, name=f"hmda/{dataset}/{era}")
    return lf


logger = get_logger(__name__)


def _standardize_geographic_codes(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Standardize geographic codes for pre-2007 data.

    Creates properly formatted FIPS codes and MSA/MD codes:
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county) with leading zeros
    - census_tract: 11-digit string (state + county + tract) with leading zeros
    - msa_md: 5-digit string with leading zeros

    Pre-2007 format:
    - state_code: numeric 1-2 digits
    - county_code: numeric 3 digits (county within state)
    - census_tract: string format "####.##" (e.g., "9509.02")
    - msa_md: numeric variable length

    Post-standardization format:
    - state_code: "01" to "56" (2-digit FIPS)
    - county_code: "01001" (state + county, 5-digit)
    - census_tract: "01001950902" (state + county + tract, 11-digit)
    - msa_md: "01234" (5-digit MSA/MD code)

    Args:
        lf: Input lazy frame with geographic codes as strings

    Returns:
        Lazy frame with standardized geographic codes
    """
    # The panel and transmittal-series datasets carry no loan-level geography
    # (only respondent_state / msa_md), so there is nothing to standardize here.
    if "state_code" not in lf.collect_schema().names():
        return lf

    # Step 1: Standardize state_code to 2-digit string with leading zeros
    lf = lf.with_columns(
        pl.col("state_code")
        .cast(pl.Int64, strict=False)  # Convert to int first
        .cast(pl.String)  # Then to string
        .str.zfill(2)  # Pad to 2 digits
        .alias("state_code")
    )

    # Step 2: Create 5-digit county_code (state + county)
    # Bronze county_code is 3-digit county within state
    lf = lf.with_columns(
        # Combine state (2-digit) + county (3-digit)
        (
            pl.col("state_code")
            + pl.col("county_code").cast(pl.Int64, strict=False).cast(pl.String).str.zfill(3)
        ).alias("county_code")
    )

    # Step 3: Standardize census_tract to 11-digit string
    # Format: "####.##" -> convert to "01001950902"
    # Formula: state (2) + county (3) + tract*100 (6 digits)
    if "census_tract" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                # Take state_code (2-digit) + county last 3 digits + tract (6-digit)
                pl.col("state_code")
                + pl.col("county_code").str.slice(-3, 3)
                + pl.col("census_tract")
                .cast(pl.Float64, strict=False)
                .mul(100)
                .round(0)
                .cast(pl.Int64, strict=False)
                .cast(pl.String)
                .str.zfill(6)
            ).alias("census_tract")
        )

    # Step 4: Standardize msa_md to 5-digit string with leading zeros
    if "msa_md" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("msa_md")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(5)
            .alias("msa_md")
        )

    return lf


def _harmonize_schema_pre2007(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Harmonize schema for pre-2007 data - type conversions and standardization.

    This function performs type conversions from bronze (all strings) to silver
    (appropriate types for analysis):

    Numeric conversions (string -> integer):
    - activity_year, occupancy_type, edit_status, sequence_number: int
    - loan_amount: int (multiplied by 1000 to get actual dollar amount)
    - income: int (multiplied by 1000 to get actual dollar amount)

    String standardization:
    - msa_md: 5-digit string with leading zeros (like county_code)

    Geographic codes (handled separately by _standardize_geographic_codes):
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county)
    - census_tract: 11-digit string (state + county + tract)

    Columns NOT converted (kept as strings due to non-numeric values):
    - respondent_id: contains Tax ID formats like "95-2318940"
    - agency_code: contains letters (C, E, D, B)
    - Other categorical variables with non-numeric codes

    Args:
        lf: Input lazy frame with all columns as strings

    Returns:
        Lazy frame with harmonized schema
    """
    from mortgage_data_manager.sources.hmda.config import (
        PRE2007_FLOAT_COLUMNS,
        PRE2007_INTEGER_COLUMNS,
    )

    lf_columns = lf.collect_schema().names()

    # Convert integer columns (except loan_amount and income which need special handling)
    # Use strict=False to convert non-numeric values to null (treats stray characters as errors)
    integer_cols_to_convert = [
        col
        for col in PRE2007_INTEGER_COLUMNS
        if col in lf_columns and col not in ["loan_amount", "income"]
    ]

    if integer_cols_to_convert:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Int64, strict=False).alias(col) for col in integer_cols_to_convert]
        )

    # Convert float columns
    float_cols_to_convert = [col for col in PRE2007_FLOAT_COLUMNS if col in lf_columns]

    if float_cols_to_convert:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in float_cols_to_convert]
        )

    # Convert loan_amount: string -> int -> multiply by 1000
    if "loan_amount" in lf_columns and "loan_amount" in PRE2007_INTEGER_COLUMNS:
        lf = lf.with_columns(
            pl.col("loan_amount").cast(pl.Int64, strict=False).mul(1000).alias("loan_amount")
        )

    # Convert income: string -> int -> multiply by 1000. 9999 is the 4-character field's
    # ceiling, not an amount, so it becomes null rather than 9,999,000 dollars.
    if "income" in lf_columns and "income" in PRE2007_INTEGER_COLUMNS:
        lf = lf.with_columns(
            null_sentinels(
                pl.col("income").cast(pl.Int64, strict=False), [PRE2018_INCOME_CEILING_000S]
            )
            .mul(1000)
            .alias("income")
        )

    return lf


#: The one file each release partition holds.
_PARTITION_FILE = "00000000.parquet"


def _write_partition(
    lf: pl.LazyFrame, out_file: Path, dataset: str, year: int, file_type: str
) -> None:
    """Publish one release partition as a single parquet, verified before it is visible.

    The chunked ``pl.PartitionBy`` sink splits large partitions into several numbered
    files, which a reader that opens only the first truncates silently, so each
    partition is one deterministically named file in bronze (raw CFPB) row order. It
    is sunk to a temp sibling and its row count checked against the pinned manifest
    there: a short write raises before it replaces anything, so neither the previous
    file nor a skip guard ever sees a truncated one. Stale chunk files from earlier
    multi-file writes are removed only once the new file is in place.
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(out_file) as tmp:
        lf.sink_parquet(tmp)
        n_rows = pl.scan_parquet(tmp).select(pl.len()).collect().item()
        check_expected_row_count(dataset, year, file_type, n_rows, out_file)
    for stale in out_file.parent.glob("*.parquet"):
        if stale != out_file:
            stale.unlink()


def _sink_release_partitions(
    lf: pl.LazyFrame, year_dir: Path, year: int, dataset: str, overwrite: bool
) -> int:
    """Write each release in ``lf`` to ``year_dir/file_type=X/``; skip those on disk.

    Returns:
        The number of release partitions written.

    Raises:
        ValueError: If ``lf`` holds rows of another (or no) activity year, or rows
            without a ``file_type``: they have no partition here.
    """
    keys = lf.select("activity_year", "file_type").unique().collect()
    stray = keys.filter(pl.col("activity_year").ne_missing(year) | pl.col("file_type").is_null())
    if not stray.is_empty():
        raise ValueError(
            f"hmda/{dataset} {year}: bronze rows outside activity_year={year} or without a "
            f"file_type: {stray.rows()}"
        )
    written = 0
    for file_type in sorted(keys["file_type"].to_list()):
        out_file = year_dir / f"file_type={file_type}" / _PARTITION_FILE
        if not should_process_file(out_file, overwrite):
            logger.debug("Skipping existing silver partition: %s", out_file.parent)
            continue
        part = lf.filter(pl.col("file_type") == file_type)
        _write_partition(part, out_file, dataset, year, str(file_type))
        written += 1
    return written


def _write_year(
    frames: list[pl.LazyFrame], output_dir: Path, year: int, dataset: str, overwrite: bool
) -> int:
    """Write one activity year's release partitions under ``output_dir/activity_year=YYYY``.

    Without ``overwrite``, a release partition already on disk is skipped and a missing
    one is written in place. With ``overwrite``, the year is rebuilt in a sibling temp
    tree and swapped in only once every partition is written and verified: the old
    year stays readable until then, and a failure leaves it untouched. The swap
    replaces exactly this year's subtree (partition-scoped), so a release whose bronze
    file is gone drops out with it.

    Returns:
        The number of release partitions written.
    """
    year_dir = output_dir / f"activity_year={year}"
    if not overwrite:
        return sum(_sink_release_partitions(lf, year_dir, year, dataset, False) for lf in frames)
    with atomic_output_dir(year_dir) as tmp:
        written = sum(_sink_release_partitions(lf, tmp, year, dataset, True) for lf in frames)
    return written


def build_silver_pre2007(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 1990,
    max_year: int = 2006,
    overwrite: bool = False,
    output_dir: Path | None = None,
) -> int:
    """Create silver layer parquet files for pre-2007 data.

    Reads bronze parquet files, applies schema harmonization (type conversions),
    geographic code standardization, and column renaming. Outputs Hive-partitioned
    parquet files to data/silver/<dataset>/pre2007/.

    Transformations applied:
    - Integer conversions: 31 columns (activity_year, loan_amount*1000, income*1000,
      loan_type, loan_purpose, action_taken, demographics, denial_reasons, etc.)
    - Float conversions: rate_spread
    - Geographic standardization: state_code (2-digit), county_code (5-digit),
      census_tract (11-digit), msa_md (5-digit)
    - Column renaming: occupancy -> occupancy_type, msamd -> msa_md

    Args:
        dataset: Dataset type to process
        min_year: First year to process (default: 1990)
        max_year: Last year to process (default: 2006)
        overwrite: Whether to replace existing silver files (default: False)
        output_dir: Era directory to write into; defaults to the configured silver layout.

    Returns:
        The number of silver files written.
    """
    bronze_dir = HMDAConfig.get_dataset_dir("bronze", dataset)
    silver_dir = output_dir or HMDAConfig.get_dataset_dir("silver", dataset, "pre2007")
    silver_dir.resolve().mkdir(parents=True, exist_ok=True)

    written = 0
    for year in range(min_year, max_year + 1):
        bronze_file = bronze_dir / f"{dataset}_{year}.parquet"
        if not bronze_file.exists():
            logger.debug("Bronze file not found for year %s: %s", year, bronze_file)
            continue

        # Output filename (year-based partitioning)
        save_file = silver_dir / f"activity_year={year}" / f"{dataset}_{year}.parquet"

        if not should_process_file(save_file, overwrite):
            logger.debug("Skipping existing silver file: %s", save_file)
            continue

        logger.info("[silver pre2007] Processing %s year %s", dataset, year)

        # Load bronze as LazyFrame
        lf = pl.scan_parquet(bronze_file)

        # Apply column renames (only renames columns that exist)
        existing_cols = lf.collect_schema().names()
        renames_to_apply = {
            old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
        }
        if renames_to_apply:
            logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
            lf = lf.rename(renames_to_apply)

        # Apply transformations
        lf = _harmonize_schema_pre2007(lf)
        lf = _standardize_geographic_codes(lf)

        # Recover the garbled pre-2007 denial_reason_3: the fixed-width parse
        # captured a packed multi-code value (a giant int); only the leading
        # character is the real single-digit reason code.
        if "denial_reason_3" in lf.collect_schema().names():
            lf = lf.with_columns(
                pl.col("denial_reason_3")
                .cast(pl.String)
                .str.slice(0, 1)
                .cast(pl.Int64, strict=False)
                .alias("denial_reason_3")
            )

        # Close-to-final dtypes + schema enforcement.
        lf = _apply_hmda_spec(lf, dataset, "pre2007")

        # Create output directory
        save_file.parent.resolve().mkdir(parents=True, exist_ok=True)

        # Write to parquet
        with atomic_output(save_file) as tmp:
            lf.sink_parquet(tmp)
        written += 1
        logger.info("Saved silver file: %s", save_file)
    return written


def _destring_and_cast_hmda_cols_2007_2017(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Destring numeric HMDA variables and cast to consistent types.

    Casts integer columns to Int64 and float columns to Float64.
    Non-numeric strings are automatically converted to null via strict=False.

    Special handling for loan_amount and income:
    - Stored in thousands in raw data (e.g., "150" = $150,000)
    - Multiplied by 1000 to get actual dollar amounts
    - income 9999 is the 4-character field's ceiling, not an amount: null

    Args:
        lf: HMDA data with potentially mixed string/numeric columns

    Returns:
        LazyFrame with consistent Int64/Float64 types for numeric columns
    """
    logger.info("Destringing HMDA variables and casting to consistent types (2007-2017)")

    # Cast float columns to Float64
    float_cols_to_cast = [
        col for col in lf.collect_schema().names() if col in PERIOD_2007_2017_FLOAT_COLUMNS
    ]
    if float_cols_to_cast:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in float_cols_to_cast]
        )

    # Cast integer columns to Int64
    int_cols_to_cast = [
        col for col in lf.collect_schema().names() if col in PERIOD_2007_2017_INTEGER_COLUMNS
    ]
    if int_cols_to_cast:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Int64, strict=False).alias(col) for col in int_cols_to_cast]
        )

    # Special handling: loan_amount stored in thousands, multiply by 1000
    if "loan_amount" in lf.collect_schema().names():
        lf = lf.with_columns(pl.col("loan_amount").mul(1000).alias("loan_amount"))

    # Special handling: income stored in thousands, multiply by 1000; the field's
    # 4-character ceiling 9999 is not an amount and becomes null.
    if "income" in lf.collect_schema().names():
        lf = lf.with_columns(
            null_sentinels(pl.col("income"), [PRE2018_INCOME_CEILING_000S])
            .mul(1000)
            .alias("income")
        )

    return lf


def _standardize_geographic_codes_period_2007_2017(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Standardize geographic codes for 2007-2017 data.

    Creates properly formatted FIPS codes and MSA/MD codes:
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county) with leading zeros
    - census_tract: 11-digit string (state + county + tract) with leading zeros
    - msa_md: 5-digit string with leading zeros

    2007-2017 format (after renaming):
    - state_code: numeric 1-2 digits (or already 2-digit string)
    - county_code: numeric 3 digits (county within state)
    - census_tract: string/numeric format (varies by year)
    - msa_md: numeric variable length

    Post-standardization format:
    - state_code: "01" to "56" (2-digit FIPS)
    - county_code: "01001" (state + county, 5-digit)
    - census_tract: "01001950902" (state + county + tract, 11-digit)
    - msa_md: "01234" (5-digit MSA/MD code)

    Args:
        lf: Input lazy frame with geographic codes

    Returns:
        Lazy frame with standardized geographic codes
    """
    # Step 1: Standardize state_code to 2-digit string with leading zeros
    if "state_code" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("state_code")
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(2)
            .alias("state_code")
        )

    # Step 2: Create 5-digit county_code (state + county)
    if "county_code" in lf.collect_schema().names() and "state_code" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                pl.col("state_code")
                + pl.col("county_code").cast(pl.Int64, strict=False).cast(pl.String).str.zfill(3)
            ).alias("county_code")
        )

    # Step 3: Standardize census_tract to 11-digit string
    if "census_tract" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                pl.col("state_code")
                + pl.col("county_code").str.slice(-3, 3)
                + pl.col("census_tract")
                .cast(pl.Float64, strict=False)
                .mul(100)
                .round(0)
                .cast(pl.Int64, strict=False)
                .cast(pl.String)
                .str.zfill(6)
            ).alias("census_tract")
        )

    # Step 4: Standardize msa_md to 5-digit string with leading zeros
    if "msa_md" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("msa_md")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(5)
            .alias("msa_md")
        )

    return lf


def _infer_pre2018_file_type_from_name(name: str) -> str:
    """Infer a single-character file_type code from a pre-2018 filename.

    'a' = three-year aggregate, 'b' = one-year, 'c' = public LAR,
    'd' = nationwide, 'e' = MLAR.

    Returns:
        The single-character file_type code.
    """
    lower = name.lower()
    if "three_year" in lower:
        return "a"
    elif "one_year" in lower:
        return "b"
    elif "public_lar" in lower:
        return "c"
    elif "nationwide" in lower:
        return "d"
    elif "mlar" in lower:
        return "e"
    else:
        raise ValueError(f"Unknown file type: {name}")


def _build_silver_institution_2007_2017(
    dataset: Literal["panel", "transmittal_series"],
    min_year: int,
    max_year: int,
    overwrite: bool,
    output_dir: Path | None,
) -> int:
    """Silver for the 2007-2017 panel / transmittal series.

    These are institution-level rosters, not loan records, so they skip the
    loans machinery entirely — no LAR renames, no geographic standardization,
    no ``file_type`` axis and no ``HMDAIndex``. Output is partitioned by
    ``activity_year`` only, matching how the pre-2007 and post-2018 panel and
    transmittal-series silver are laid out.

    Both datasets change layout mid-era; the per-dataset rename map covers both
    header spellings and conform-to-target fills the absent columns with typed
    nulls, so every year lands on one schema. The panel also carries
    ``respondent_key``, the normalized id the loans silver carries.

    Returns:
        The number of silver files written.
    """
    renames = (
        PANEL_2007_2017_RENAMES if dataset == "panel" else TRANSMITTAL_SERIES_2007_2017_RENAMES
    )
    bronze_dir = HMDAConfig.get_dataset_dir("bronze", dataset)
    silver_dir = output_dir or HMDAConfig.get_dataset_dir("silver", dataset, "period_2007_2017")
    silver_dir.resolve().mkdir(parents=True, exist_ok=True)

    written = 0
    for year in range(min_year, max_year + 1):
        bronze_files = _bronze_files_for_year(bronze_dir, year)
        if not bronze_files:
            logger.debug("No bronze found for %s %s", dataset, year)
            continue
        if len(bronze_files) > 1:
            logger.warning(
                "Multiple bronze files for %s %s (%s); using the first",
                dataset,
                year,
                [f.name for f in bronze_files],
            )

        save_file = silver_dir / f"activity_year={year}" / f"{dataset}_{year}.parquet"
        if not should_process_file(save_file, overwrite):
            logger.debug("Skipping existing silver file: %s", save_file)
            continue

        logger.info("[silver 2007-2017] Processing %s year %s", dataset, year)
        lf = pl.scan_parquet(bronze_files[0])

        # The transmittal-series CSVs put a space after each delimiter, so their
        # headers arrive as " Respondent ID". Strip the column names before the
        # rename map is consulted, or none of its keys match.
        lf = strip_column_names(lf)

        # Header text differs between the era's two halves; map both onto the
        # house names, then strip the fixed-width padding the CSVs carry.
        present = {old: new for old, new in renames.items() if old in lf.collect_schema().names()}
        if present:
            lf = lf.rename(present)
        lf = lf.with_columns(
            pl.col(c).str.strip_chars() for c, dt in lf.collect_schema().items() if dt == pl.String
        )

        if "activity_year" not in lf.collect_schema().names():
            lf = lf.with_columns(pl.lit(year).alias("activity_year"))
        if dataset == "panel":
            lf = lf.with_columns(respondent_key())

        lf = _apply_hmda_spec(lf, dataset, "period_2007_2017")

        save_file.parent.resolve().mkdir(parents=True, exist_ok=True)
        with atomic_output(save_file) as tmp:
            lf.sink_parquet(tmp)
        written += 1
        logger.info("Saved silver file: %s", save_file)
    return written


def build_silver_period_2007_2017(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
    output_dir: Path | None = None,
) -> int:
    """Create hive-partitioned silver layer for 2007–2017 data.

    Loans are processed one bronze file at a time with the LAR standardization
    (renames, destring, geographic codes) and written to
    data/silver/loans/period_2007_2017/activity_year=YYYY/file_type=X/, one file per
    release partition (see `_write_year` for the skip and overwrite rules).

    Panel and transmittal series are institution rosters and take a separate
    path — see `_build_silver_institution_2007_2017`.

    Args:
        dataset: Dataset to process.
        min_year: First year to process.
        max_year: Last year to process.
        overwrite: Rebuild the requested years, replacing each one atomically.
        output_dir: Era directory to write into; defaults to the configured silver layout.

    Returns:
        The number of silver partitions (loans) or files (panel, transmittal series)
        written.
    """
    if dataset != "loans":
        return _build_silver_institution_2007_2017(
            dataset, min_year, max_year, overwrite, output_dir
        )

    bronze_dir = HMDAConfig.get_dataset_dir("bronze", dataset)
    silver_dir = output_dir or HMDAConfig.get_dataset_dir("silver", dataset, "period_2007_2017")
    silver_dir.resolve().mkdir(parents=True, exist_ok=True)

    written = 0
    for year in range(min_year, max_year + 1):
        frames: list[pl.LazyFrame] = []
        for file in _bronze_files_for_year(bronze_dir, year):
            lf = pl.scan_parquet(file, low_memory=True)

            # Apply column renames (only renames columns that exist)
            existing_cols = lf.collect_schema().names()
            renames_to_apply = {
                old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
            }
            if renames_to_apply:
                logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
                lf = lf.rename(renames_to_apply)

            # Destring and cast integer columns to consistent Int64 types
            lf = _destring_and_cast_hmda_cols_2007_2017(lf)

            # Standardize geographic codes (state, county, census_tract, msa_md)
            lf = _standardize_geographic_codes_period_2007_2017(lf)

            # Drop derived columns that only appear in some files (inconsistent)
            lf = lf.drop(DERIVED_COLUMNS, strict=False)

            # Ensure partition keys exist
            if "activity_year" not in lf.collect_schema().names():
                lf = lf.with_columns(pl.lit(year).alias("activity_year"))

            file_type_code = _infer_pre2018_file_type_from_name(file.name)
            lf = lf.with_columns(pl.lit(file_type_code).alias("file_type"))

            # Stable per-record locator, identical scheme to post-2018
            # ({year}{file_type}_{row:09d}). Post-2018 assigns this at bronze via
            # scan_csv(row_index_name=...); pre-2018 bronze predates the column,
            # so we assign it here over the bronze parquet (whose row order is the
            # raw-CSV order). This gives pre-2018 a uniform join key — and in
            # particular gives 2017 a usable identifier, since its native
            # sequence_number is entirely null.
            lf = lf.with_row_index(HMDA_INDEX_COLUMN)
            lf = append_hmda_index(lf, year, file_type_code)

            if lf.limit(1).collect().height == 0:
                logger.warning("Skipping empty file: %s", file)
                continue

            lf = lf.with_columns(
                respondent_key(),
                number_of_borrowers("period_2007_2017"),
                purchaser_type_label("period_2007_2017"),
            )

            # Close-to-final dtypes + schema enforcement.
            frames.append(_apply_hmda_spec(lf, dataset, "period_2007_2017"))

        if not frames:
            logger.debug("No bronze found for %s %s", dataset, year)
            continue
        written += _write_year(frames, silver_dir, year, dataset, overwrite)
    return written


def _harmonize_schema(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Harmonize schema for post-2018 HMDA data.

    This function consolidates all schema transformations including:
    - Destringing numeric variables
    - Casting integer-like floats to integers
    - Standardizing census tract format
    - Handling exempt/special values

    Args:
        lf: HMDA data with numeric columns represented as strings.

    Returns:
        LazyFrame with harmonized schema including properly typed numeric fields
        and formatted census tract column.
    """
    # Get existing columns once at the start
    lf_columns = lf.collect_schema().names()

    # Replace exempt columns with -99999 (only if they exist)
    for exempt_col in POST2018_EXEMPT_COLUMNS:
        if exempt_col in lf_columns:
            lf = lf.with_columns(pl.col(exempt_col).replace("Exempt", "-99999").alias(exempt_col))

    # Clean Units (only if column exists)
    if "total_units" in lf_columns:
        lf = lf.with_columns(
            pl.col("total_units")
            .replace(
                ["5-24", "25-49", "50-99", "100-149", ">149"],
                [5, 6, 7, 8, 9],
            )
            .cast(pl.Int16, strict=False)
            .alias("total_units")
        )

    # Clean Age (only if columns exist)
    for replace_column in ["applicant_age", "co_applicant_age"]:
        if replace_column in lf_columns:
            lf = lf.with_columns(
                pl.col(replace_column)
                .replace(
                    ["<25", "25-34", "35-44", "45-54", "55-64", "65-74", ">74"],
                    [1, 2, 3, 4, 5, 6, 7],
                )
                .cast(pl.Int16, strict=False)
                .alias(replace_column)
            )

    # Clean Age Dummy Variables (only if columns exist)
    for replace_column in ["applicant_age_above_62", "co_applicant_age_above_62"]:
        if replace_column in lf_columns:
            lf = lf.with_columns(
                pl.col(replace_column)
                .replace(
                    ["No", "no", "NO", "Yes", "yes", "YES", "Na", "na", "NA"],
                    [0, 0, 0, 1, 1, 1, None, None, None],
                )
                .cast(pl.Int16, strict=False)
                .alias(replace_column)
            )

    # Clean Debt-to-Income (only if column exists)
    if "debt_to_income_ratio" in lf_columns:
        lf = lf.with_columns(
            pl.col("debt_to_income_ratio")
            .replace(
                ["<20%", "20%-<30%", "30%-<36%", "50%-60%", ">60%", "Exempt"],
                [10, 20, 30, 50, 60, -99999],
            )
            .cast(pl.Int64, strict=False)
            .alias("debt_to_income_ratio")
        )

    # Clean Conforming Loan Limit (only if column exists)
    if "conforming_loan_limit" in lf_columns:
        lf = lf.with_columns(
            pl.col("conforming_loan_limit")
            .replace(["NC", "C", "U", "NA"], [0, 1, -99999, -99999])
            .cast(pl.Int64, strict=False)
            .alias("conforming_loan_limit")
        )

    # Cast safe strings to floats
    for column in POST2018_FLOAT_COLUMNS:
        if column in lf_columns:
            lf = lf.with_columns(pl.col(column).cast(pl.Float64, strict=False).alias(column))
    # Through Float64: the 2020 snapshot publishes loan_amount as "165000.0", which a direct
    # integer cast nulls.
    for column in POST2018_INTEGER_COLUMNS:
        if column in lf_columns:
            lf = lf.with_columns(
                pl.col(column)
                .cast(pl.Float64, strict=False)
                .cast(pl.Int64, strict=False)
                .alias(column)
            )

    # Clean income columns (only if column exists)
    if "income" in lf_columns:
        lf = lf.with_columns(
            pl.col("income").mul(1000).replace([-99999000], [-99999]).alias("income")
        )

    # Standardize census_tract column format (if present)
    if "census_tract" in lf_columns:
        lf = (
            lf.cast({"census_tract": pl.Float64}, strict=False)
            .cast({"census_tract": pl.Int64}, strict=False)
            .cast({"census_tract": pl.String}, strict=False)
            .with_columns(pl.col("census_tract").str.zfill(11).alias("census_tract"))
        )

    # Standardize county_code column format (if present)
    if "county_code" in lf_columns:
        lf = (
            lf.cast({"county_code": pl.Float64}, strict=False)
            .cast({"county_code": pl.Int64}, strict=False)
            .cast({"county_code": pl.String}, strict=False)
            .with_columns(pl.col("county_code").str.zfill(5).alias("county_code"))
        )

    return lf


def build_silver_post2018(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 2018,
    max_year: int = 2025,
    overwrite: bool = False,
    output_dir: Path | None = None,
) -> int:
    """Create hive-partitioned silver layer for post-2018 data.

    Processes bronze parquet files one-at-a-time, applies standard cleaning
    transforms with schema-guided typing, and writes to
    data/silver/<dataset>/post2018/activity_year=YYYY/file_type=X/, one file per
    release partition (see `_write_year` for the skip and overwrite rules).

    Args:
        dataset: Dataset family to build.
        min_year: First activity year to build, inclusive.
        max_year: Last activity year to build, inclusive.
        overwrite: Rebuild the requested years, replacing each one atomically.
        output_dir: Era directory to write into; defaults to the configured silver layout.

    Returns:
        The number of silver partitions written.
    """
    bronze_dir = HMDAConfig.get_dataset_dir("bronze", dataset)
    silver_dir = output_dir or HMDAConfig.get_dataset_dir("silver", dataset, "post2018")
    silver_dir.resolve().mkdir(parents=True, exist_ok=True)

    written = 0
    for year in range(min_year, max_year + 1):
        frames: list[pl.LazyFrame] = []
        for file in _bronze_files_for_year(bronze_dir, year):
            lf = pl.scan_parquet(file, low_memory=True)

            # Apply column renames (only renames columns that exist)
            existing_cols = lf.collect_schema().names()
            renames_to_apply = {
                old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
            }
            if renames_to_apply:
                logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
                lf = lf.rename(renames_to_apply)

            # Apply schema harmonization (type conversions) to all datasets
            lf = _harmonize_schema(lf)
            if dataset == "loans":
                lf = lf.with_columns(
                    number_of_borrowers("post2018"), purchaser_type_label("post2018")
                )

            # Close-to-final dtypes + schema enforcement.
            frames.append(_apply_hmda_spec(lf, dataset, "post2018"))

        if not frames:
            logger.debug("No bronze found for %s %s", dataset, year)
            continue
        written += _write_year(frames, silver_dir, year, dataset, overwrite)
    return written


def build_silver_lei_names(
    overwrite: bool = False,
    destination: Path | None = None,
    panel_dir: Path | None = None,
) -> int:
    """Build the latest panel ``respondent_name`` per LEI from the post-2018 panel silver.

    Each LEI takes the name it filed under in the latest activity year it appears in,
    each year read from its most mature panel release (as `scan_hmda_years` picks it).
    An LEI with two different names in that year has no latest name and is left out:
    there is no ground for picking one. Names are kept as filed (no normalization).

    Args:
        overwrite: Rebuild even when the table exists. Rebuild after any panel change.
        destination: File to write; defaults to ``HMDAConfig.HMDA_LEI_NAME_FILE``.
        panel_dir: Post-2018 panel era directory to read; defaults to the configured
            silver layout.

    Returns:
        The number of LEIs written (0 when skipped).
    """
    from mortgage_data_manager.sources.hmda.load import scan_hmda_years

    destination = destination or HMDAConfig.HMDA_LEI_NAME_FILE
    if not should_process_file(destination, overwrite):
        logger.debug("Skipping existing LEI name table: %s", destination)
        return 0
    names = (
        scan_hmda_years("post2018", dataset="panel", root=panel_dir)
        .select("lei", "respondent_name", "activity_year")
        .filter(pl.col("lei").is_not_null() & (pl.col("lei") != ""))
        .filter(pl.col("activity_year") == pl.col("activity_year").max().over("lei"))
        .unique()
        .filter(pl.len().over("lei") == 1)
        .sort("lei")
        .collect()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(destination) as tmp:
        names.write_parquet(tmp)
    logger.info("Saved %s LEI names: %s", f"{names.height:,}", destination)
    return names.height
