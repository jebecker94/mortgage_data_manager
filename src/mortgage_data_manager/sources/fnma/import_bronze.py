"""Import FNMA loan performance data from zipped CSVs to Parquet files.

Names and types the headerless files from the field layout in
``schemas/fnma/sf_loan_layout.csv`` (see `read_data_dictionary`). Uses Polars for
efficient memory usage with streaming.

Four source layouts are supported:

- Per-quarter zips (legacy): ``data/fnma/raw/2019Q1.zip`` etc., each holding
  a single ``{quarter}.csv``.
- Bulk archive: ``Performance_All.zip`` holding many ``{quarter}.csv`` members
  side by side (the snapshot Fannie publishes covering the full history).
- HARP: ``HARP_Files.zip``, holding the whole HARP refinance population in one
  un-partitioned CSV plus the original-loan bridge (see `import_harp`).
- Multifamily: ``Multifamily.zip`` / ``Multifamily_DSCR.zip``, each holding one
  release-stamped full-history CSV (comma-delimited with header; see
  `import_multifamily`).
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import cast

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.sources.fnma.config import FNMAConfig

logger = get_logger(__name__)

# Per-quarter zips look like 2019Q1.zip; exclusion zips like 2019Q1EXCL.zip.
# Used to filter out bulk archives (Performance_All.zip, Exclusions.zip, ...).
QUARTER_ZIP_PATTERN = re.compile(r"^(?P<year>\d{4})Q(?P<quarter>[1-4])(?P<suffix>.*)\.zip$")
QUARTER_CSV_PATTERN = re.compile(r"^(?P<year>\d{4})Q(?P<quarter>[1-4])\.csv$")
EXCL_CSV_PATTERN = re.compile(r"^(?P<year>\d{4})Q(?P<quarter>[1-4])EXCL\.csv$")

#: The bulk archive carrying the full 2000-forward quarterly history. The
#: per-quarter zips only cover recent vintages, so this is the sole source for
#: the back history.
PERFORMANCE_ALL_ARCHIVE = "Performance_All.zip"

#: The one-time (June 2021) Exclusion Dataset release: acquisitions 2000Q1-2020Q3.
EXCLUSIONS_ARCHIVE = "Exclusions.zip"

# Per the Historical Loan Performance Exclusion Dataset reference (Fannie Mae,
# 6.17.21), exclusion files use the Primary dataset layout as of mid-2021 (108
# fields) plus 4 extra indicator columns appended at positions 109-112.
EXCLUSION_PRIMARY_FIELD_COUNT = 108
EXCLUSION_EXTRA_FIELDS: list[tuple[str, type[pl.DataType]]] = [
    ("Non-Standard Documentation Indicator", pl.Utf8),
    ("Non-Standard Underwriting or Eligibility Indicator", pl.Utf8),
    ("Government Insured/Guarantee Indicator", pl.Utf8),
    ("Negative Amortization Indicator", pl.Utf8),
]

# HARP refinance dataset. One zip holding the entire HARP population in a single
# un-partitioned CSV (~26 GB) that uses the SAME pipe-delimited Primary glossary
# layout as the quarterly files, plus a comma-delimited bridge from each original
# loan to the HARP loan that refinanced it (1:1, no header).
HARP_ZIP_NAME = "HARP_Files.zip"
HARP_PERF_CSV = "HARPLPPub.csv"
HARP_MAPPING_TXT = "Loan_Mapping.txt"

# The bridge's two 12-digit id columns, in file order. Direction verified against
# the data (2026-07-31): 100% of the HARP file's Loan Identifiers are in the
# SECOND column and 0% in the first, so column 1 is the loan being refinanced and
# column 2 is the HARP loan itself — named to match the record it keys, the way
# FHLMC carries 'Pre-HARP Loan Sequence Number' on the HARP loan's row. Both are
# zero-paddable identifiers, so they are pinned to Utf8 (inference would strip
# leading zeros and silently break the join back to the performance files).
HARP_MAPPING_COLUMNS: list[str] = ["Pre-HARP Loan Identifier", "Loan Identifier"]

# Multifamily Loan Performance Dataset. The raw zips keep Fannie's download
# names; each quarterly release restates the FULL loan x month history, so the
# CSV member carries the release stamp (perf: YYYYMM; the current-DSCR
# supplement: MMYYYY, normalized to YYYYMM for the bronze stem).
MF_PERF_ZIP_NAME = "Multifamily.zip"
MF_DSCR_ZIP_NAME = "Multifamily_DSCR.zip"
MF_PERF_CSV_PATTERN = re.compile(r"^FNMA_MF_Loan_Performance_Data_(?P<yyyymm>\d{6})\.csv$")
MF_DSCR_CSV_PATTERN = re.compile(r"^FNMA_MF_LOAN_Current DSCR_(?P<mm>\d{2})(?P<yyyy>\d{4})\.csv$")

# Dollar-formatted amount columns ('$1,234.56'; parentheses = negative).
MF_DOLLAR_COLS = (
    "Loan Acquisition UPB",
    "Original UPB",
    "Foreclosure Value",
    "Lifetime Net Credit Loss Amount",
    "Sale Price",
    "Default Amount",
    "UPB - Current",
    "Delinquency UPB",
)

# Plain-numeric MF columns cast at bronze; everything else (identifiers, ISO
# date strings, code sets) stays Utf8 for silver typing. Pinned so inference
# never silently mistypes (e.g. 'Loan Number' has leading zeros).
MF_PERF_NUMERIC_COLS: dict[str, type[pl.DataType]] = {
    "Amortization Term": pl.Int64,
    "Original Interest Rate": pl.Float64,
    "Loan Acquisition LTV": pl.Float64,
    "Underwritten DSCR": pl.Float64,
    "Original Term": pl.Int64,
    "Original I/O Term": pl.Int64,
    "Modified Loss Sharing Percentage": pl.Float64,
    "Number of Properties at Acquisition": pl.Int64,
    "Property Acquisition Total Unit Count": pl.Int64,
    "Year Built": pl.Int64,
    "Physical Occupancy %": pl.Float64,
    "Loan Active Property Count": pl.Int64,
    "Note Rate": pl.Float64,
    "Loan Age": pl.Int64,
}
MF_DSCR_NUMERIC_COLS: dict[str, type[pl.DataType]] = {
    "Year": pl.Int64,
    "Year DSCR": pl.Float64,
}


def read_data_dictionary(layout_path: Path) -> pl.DataFrame:
    """Read the single-family field layout and extract field definitions.

    The layout is ``schemas/fnma/sf_loan_layout.csv``: the ``Field Position``,
    ``Field Name``, ``Type`` and ``Max Length`` columns of the "Combined Glossary"
    sheet of Fannie Mae's CRT file layout and glossary workbook (the 113-field edition),
    published on the Single-Family Loan Performance Data page
    (https://capitalmarkets.fanniemae.com/credit-risk-transfer/fannie-mae-single-family-loan-performance-data).
    Values are kept verbatim, so every column is read as a string.

    Args:
        layout_path: Path of the field-layout CSV.

    Returns:
        The field definitions, one row per field position.
    """
    logger.info("Reading data dictionary from %s", layout_path)
    df_dict = (
        pl.read_csv(layout_path, infer_schema=False)
        .select("Field Position", "Field Name", "Type", "Max Length")
        .filter(pl.col("Field Position").is_not_null())
        .with_columns(pl.col("Field Position").cast(pl.Int64))
    )
    logger.info("Found %s field definitions", df_dict.height)
    return df_dict


def infer_polars_dtype(type_str: str | None, max_length_str: str | None) -> type[pl.DataType]:
    """Infer Polars dtype from the layout's Type and Max Length columns.

    Args:
        type_str: The layout ``Type`` cell.
        max_length_str: The layout ``Max Length`` cell; a decimal point selects
            Float64 over Int64.

    Returns:
        The inferred polars dtype, falling back to ``pl.Utf8``.
    """
    if type_str is None:
        return pl.Utf8

    type_str = type_str.strip().upper()
    max_length_str = max_length_str or ""

    if type_str == "NUMERIC":
        return pl.Float64 if "." in max_length_str else pl.Int64
    if type_str == "DATE":
        return pl.Utf8
    return pl.Utf8


def create_column_schema(dict_df: pl.DataFrame) -> tuple[list[str], dict[str, type[pl.DataType]]]:
    """Build ordered column names + Polars schema from the field layout.

    Args:
        dict_df: The field definitions from `read_data_dictionary`.

    Returns:
        The column names in field-position order, and the matching schema.
    """
    schema: dict[str, type[pl.DataType]] = {}
    for row in dict_df.sort("Field Position").iter_rows(named=True):
        schema[row["Field Name"]] = infer_polars_dtype(row["Type"], row["Max Length"])
    column_names = list(schema.keys())
    logger.info("Column schema created: %s fields", len(column_names))
    return column_names, schema


def _csv_to_parquet(
    csv_path: Path,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    output_path: Path,
) -> int:
    """Lazy-scan a pipe-delimited CSV and stream to parquet. Returns row count."""
    lf = pl.scan_csv(
        csv_path,
        separator="|",
        has_header=False,
        new_columns=column_names,
        schema_overrides=schema,
        null_values=["", "XX"],
        try_parse_dates=False,
        ignore_errors=True,
    )
    row_count = lf.select(pl.len()).collect().item()
    logger.info("  %s: %s rows", csv_path.name, f"{row_count:,}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lf.sink_parquet(output_path, compression="snappy")
    size_mb = output_path.stat().st_size / 1024**2
    logger.info("  Wrote %s (%.1f MB)", output_path.name, size_mb)
    return cast(int, row_count)


def import_member_from_archive(
    archive_path: Path,
    member: str,
    output_stem: str,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    overwrite: bool = False,
    tmp_dir: Path | None = None,
) -> bool:
    """Extract one CSV member from a zip archive and write to bronze parquet.

    Args:
        archive_path: Path to the zip archive (single-CSV or multi-CSV).
        member: CSV member name inside the zip (e.g., ``"2018Q1.csv"``).
        output_stem: Output parquet stem (e.g., ``"2018Q1"`` → ``2018Q1.parquet``).
        column_names: Ordered field names from the glossary.
        schema: Polars dtypes keyed by field name.
        bronze_dir: Destination bronze directory.
        overwrite: If False, skip when output parquet already exists.
        tmp_dir: Parent directory for the extraction scratch space. ``None`` uses
            the system temp dir, which is right for the quarterly members (<=9 GB);
            pass a directory on the data volume for members too big to land on the
            boot disk (HARP's single CSV is ~26 GB).

    Returns:
        True on success, False on failure.
    """
    output_path = bronze_dir / f"{output_stem}.parquet"
    if not should_process_file(output_path, overwrite):
        logger.info("  Skipping %s (exists; --overwrite to rebuild)", output_path.name)
        return True

    try:
        with tempfile.TemporaryDirectory(dir=tmp_dir) as td:
            td_path = Path(td)
            with zipfile.ZipFile(archive_path, "r") as z:
                logger.info("  Extracting %s from %s", member, archive_path.name)
                z.extract(member, td_path)
            _csv_to_parquet(td_path / member, column_names, schema, output_path)
        return True
    except Exception as e:
        logger.error("  Failed %s from %s: %s", member, archive_path.name, e)
        return False


def import_quarter_zip(
    zip_path: Path,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    overwrite: bool = False,
) -> bool:
    """Import a per-quarter zip (single CSV named ``{stem}.csv``).

    Args:
        zip_path: Path of the quarterly archive.
        column_names: Column names to apply, in field-position order.
        schema: Polars schema keyed by column name.
        bronze_dir: Destination bronze directory.
        overwrite: Rebuild the parquet even when it already exists.

    Returns:
        True if a parquet was written, False if the import was skipped.
    """
    return import_member_from_archive(
        archive_path=zip_path,
        member=f"{zip_path.stem}.csv",
        output_stem=zip_path.stem,
        column_names=column_names,
        schema=schema,
        bronze_dir=bronze_dir,
        overwrite=overwrite,
    )


def import_performance_all(
    archive_path: Path,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    *,
    min_year: int = 2018,
    max_year: int | None = None,
    overwrite: bool = True,
    quarters: list[str] | None = None,
    tmp_dir: Path | None = None,
) -> dict[str, int]:
    """Import quarterly CSVs from the bulk Performance_All archive.

    Args:
        archive_path: Path to ``Performance_All.zip``.
        column_names: Ordered field names from the glossary.
        schema: Polars dtypes keyed by field name.
        bronze_dir: Destination bronze directory.
        min_year: Lowest year to import (inclusive). Defaults to 2018.
        max_year: Highest year to import (inclusive). None = no upper bound.
        overwrite: If True, replace existing bronze parquets. Defaults to True
            because Performance_All is treated as the freshest snapshot.
        quarters: Optional explicit list of quarter stems (e.g., ``["2025Q4"]``)
            to import. When set, ``min_year``/``max_year`` are ignored.
        tmp_dir: Parent directory for extraction scratch space. ``None`` uses the
            system temp dir, which only suits the recent quarters -- the
            back-history members run to 40.5 GB (2003Q3) and must extract to the
            data volume.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    with zipfile.ZipFile(archive_path, "r") as z:
        all_members = z.namelist()

    matches: list[tuple[str, str]] = []  # (member, output_stem)
    for name in all_members:
        m = QUARTER_CSV_PATTERN.match(name)
        if not m:
            continue
        stem = f"{m.group('year')}Q{m.group('quarter')}"
        year = int(m.group("year"))
        if quarters is not None:
            if stem in quarters:
                matches.append((name, stem))
        else:
            if year < min_year:
                continue
            if max_year is not None and year > max_year:
                continue
            matches.append((name, stem))

    matches.sort(key=lambda x: x[1])

    logger.info("=" * 60)
    logger.info("Performance_All import: %s", archive_path.name)
    logger.info("Bronze: %s", bronze_dir)
    logger.info("Quarters to process: %s", len(matches))
    logger.info("=" * 60)

    successful = failed = 0
    for i, (member, stem) in enumerate(matches, 1):
        logger.info("[%s/%s] %s", i, len(matches), stem)
        ok = import_member_from_archive(
            archive_path=archive_path,
            member=member,
            output_stem=stem,
            column_names=column_names,
            schema=schema,
            bronze_dir=bronze_dir,
            overwrite=overwrite,
            tmp_dir=tmp_dir,
        )
        if ok:
            successful += 1
        else:
            failed += 1

    return {"processed": len(matches), "successful": successful, "failed": failed}


def build_exclusion_schema(
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
) -> tuple[list[str], dict[str, type[pl.DataType]]]:
    """Derive the 112-field exclusion-file schema from the Primary glossary.

    Takes the first ``EXCLUSION_PRIMARY_FIELD_COUNT`` columns of the Primary
    glossary (the layout as of the June 2021 exclusion release) and appends
    the 4 exclusion-specific Y/N indicator fields.

    Args:
        column_names: Ordered field names from the full Primary glossary.
        schema: Polars dtypes keyed by field name.

    Returns:
        Tuple of (exclusion column names, exclusion schema).
    """
    primary_names = column_names[:EXCLUSION_PRIMARY_FIELD_COUNT]
    excl_names = primary_names + [n for n, _ in EXCLUSION_EXTRA_FIELDS]
    excl_schema = {n: schema[n] for n in primary_names}
    excl_schema.update({n: t for n, t in EXCLUSION_EXTRA_FIELDS})
    return excl_names, excl_schema


def import_exclusions(
    archive_path: Path,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    *,
    overwrite: bool = False,
    tmp_dir: Path | None = None,
) -> dict[str, int]:
    """Import every quarter in the bulk Exclusions archive.

    Fannie's Historical Loan Performance Exclusion Dataset carries the loans left
    out of the standard fixed-rate dataset -- ARM, balloon, interest-only,
    government-insured, Alt-A, reduced-documentation, lender-recourse and
    negotiated-bulk acquisitions -- for acquisitions 2000Q1-2020Q3. It is a
    one-time release, so every quarter is imported; there is no year window.
    Bronze parquets are written as ``{quarter}EXCL.parquet`` alongside the Primary
    quarterly parquets in ``bronze_dir`` (no subfolder), where silver picks them
    up with the standard quarters.

    Args:
        archive_path: Path to ``Exclusions.zip``.
        column_names: Ordered field names from the full Primary glossary.
        schema: Polars dtypes from the full Primary glossary.
        bronze_dir: Destination bronze directory.
        overwrite: Rebuild quarters whose bronze parquet already exists.
        tmp_dir: Extraction scratch space; the largest members run to 14.5 GB.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.

    Raises:
        FileNotFoundError: If ``archive_path`` does not exist.
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    excl_names, excl_schema = build_exclusion_schema(column_names, schema)

    with zipfile.ZipFile(archive_path, "r") as z:
        all_members = z.namelist()

    matches: list[tuple[str, str]] = []
    for name in all_members:
        if m := EXCL_CSV_PATTERN.match(name):
            matches.append((name, f"{m.group('year')}Q{m.group('quarter')}EXCL"))
    matches.sort(key=lambda x: x[1])

    logger.info("=" * 60)
    logger.info("Exclusions import: %s", archive_path.name)
    logger.info("Bronze: %s", bronze_dir)
    logger.info("Quarters to process: %s  (schema: %s fields)", len(matches), len(excl_names))
    logger.info("=" * 60)

    successful = failed = 0
    for i, (member, stem) in enumerate(matches, 1):
        logger.info("[%s/%s] %s", i, len(matches), stem)
        ok = import_member_from_archive(
            archive_path=archive_path,
            member=member,
            output_stem=stem,
            column_names=excl_names,
            schema=excl_schema,
            bronze_dir=bronze_dir,
            overwrite=overwrite,
            tmp_dir=tmp_dir,
        )
        if ok:
            successful += 1
        else:
            failed += 1

    return {"processed": len(matches), "successful": successful, "failed": failed}


def import_harp(
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    schema_file: Path | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import the HARP refinance dataset to bronze parquet.

    ``HARP_Files.zip`` holds the entire HARP population in ONE un-partitioned CSV
    — no quarterly split and no release stamp — so bronze mirrors that shape in a
    ``harp/`` subfolder rather than adding stems to the quarterly bronze root:

    - ``HARPLPPub.csv`` -> ``harp/loan_performance.parquet``. Same 113-field
      pipe-delimited Primary glossary layout as the quarterly files, so it reuses
      their column names and dtypes (and conforms to the same silver spec).
    - ``Loan_Mapping.txt`` -> ``harp/loan_mapping.parquet``. Headerless
      comma-delimited 1:1 bridge between each HARP loan and the loan it
      refinanced; both ids are pinned ``Utf8`` (see ``HARP_MAPPING_COLUMNS``).

    The performance member unpacks to ~26 GB, which does not fit in the boot
    disk's temp space, so extraction scratches onto the bronze volume instead.
    A missing zip is skipped with a log line, so this is safe to run alongside
    the other importers.

    Args:
        raw_dir: Source directory holding ``HARP_Files.zip``. Defaults to
            ``FNMAConfig.FNMA_RAW_DIR``.
        bronze_dir: Destination bronze root (outputs go in its ``harp/``
            subfolder). Defaults to ``FNMAConfig.FNMA_BRONZE_DIR``.
        schema_file: Path to the field-layout CSV. Defaults to
            ``FNMAConfig.FNMA_SCHEMA_FILE``.
        overwrite: If False, skip outputs whose bronze parquet already exists.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    raw_dir = raw_dir or FNMAConfig.FNMA_RAW_DIR
    bronze_dir = bronze_dir or FNMAConfig.FNMA_BRONZE_DIR
    schema_file = schema_file or FNMAConfig.FNMA_SCHEMA_FILE
    harp_bronze_dir = bronze_dir / "harp"

    archive = raw_dir / HARP_ZIP_NAME
    if not archive.exists():
        logger.info("No %s in %s — skipping HARP import", HARP_ZIP_NAME, raw_dir)
        return {"processed": 0, "successful": 0, "failed": 0}

    column_names, schema = create_column_schema(read_data_dictionary(schema_file))

    logger.info("=" * 60)
    logger.info("HARP import: %s", archive.name)
    logger.info("Bronze: %s", harp_bronze_dir)
    logger.info("=" * 60)

    successful = failed = 0

    logger.info("[1/2] loan_performance")
    if import_member_from_archive(
        archive_path=archive,
        member=HARP_PERF_CSV,
        output_stem="loan_performance",
        column_names=column_names,
        schema=schema,
        bronze_dir=harp_bronze_dir,
        overwrite=overwrite,
        tmp_dir=bronze_dir,
    ):
        successful += 1
    else:
        failed += 1

    logger.info("[2/2] loan_mapping")
    mapping_out = harp_bronze_dir / "loan_mapping.parquet"
    if not should_process_file(mapping_out, overwrite):
        logger.info("  Skipping %s (exists; --overwrite to rebuild)", mapping_out.name)
        successful += 1
    else:
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                with zipfile.ZipFile(archive, "r") as z:
                    logger.info("  Extracting %s from %s", HARP_MAPPING_TXT, archive.name)
                    z.extract(HARP_MAPPING_TXT, td_path)
                # schema= (not new_columns=) so the headerless file gets both names
                # and dtypes, and any short row is null-padded rather than dropped.
                lf = pl.scan_csv(
                    td_path / HARP_MAPPING_TXT,
                    has_header=False,
                    schema=dict.fromkeys(HARP_MAPPING_COLUMNS, pl.Utf8),
                )
                row_count = lf.select(pl.len()).collect().item()
                logger.info("  %s: %s rows", HARP_MAPPING_TXT, f"{row_count:,}")
                mapping_out.parent.mkdir(parents=True, exist_ok=True)
                lf.sink_parquet(mapping_out, compression="snappy")
                logger.info("  Wrote %s", mapping_out.name)
            successful += 1
        except Exception:
            logger.exception("  Failed %s from %s", HARP_MAPPING_TXT, archive.name)
            failed += 1

    return {"processed": 2, "successful": successful, "failed": failed}


def _dollars_to_float(col: str) -> pl.Expr:
    """Parse an accounting-formatted dollar string to ``Float64`` ('($1.50)' -> -1.5)."""
    return (
        pl.col(col)
        .str.replace_all(r"[$,]", "")
        .str.replace(r"^\((.+)\)$", "-${1}")
        .cast(pl.Float64)
    )


def _mf_csv_to_parquet(
    csv_path: Path,
    numeric_cols: dict[str, type[pl.DataType]],
    dollar_cols: tuple[str, ...],
    output_path: Path,
) -> int:
    """Scan a headered comma CSV all-Utf8, type MF numerics, and stream to parquet."""
    lf = pl.scan_csv(csv_path, infer_schema_length=0)
    names = set(lf.collect_schema().names())
    lf = lf.with_columns(
        [_dollars_to_float(c) for c in dollar_cols if c in names]
        + [pl.col(c).cast(dt, strict=False) for c, dt in numeric_cols.items() if c in names]
    )
    row_count = int(lf.select(pl.len()).collect().item())
    logger.info("  %s: %s rows", csv_path.name, f"{row_count:,}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lf.sink_parquet(output_path, compression="snappy")
    size_mb = output_path.stat().st_size / 1024**2
    logger.info("  Wrote %s (%.1f MB)", output_path.name, size_mb)
    return row_count


def import_multifamily(
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import the Multifamily Loan Performance zips to bronze parquet.

    Each quarterly release restates the full loan x month history, so bronze
    outputs are release-stamped and accumulate under ``bronze/multifamily/``:

    - ``Multifamily.zip`` -> ``loan_performance_{yyyymm}.parquet``
    - ``Multifamily_DSCR.zip`` -> ``current_dscr_{yyyymm}.parquet``

    Unlike the pipe-delimited headerless SF files, the MF CSVs are
    comma-delimited WITH header. Dollar-formatted amounts are parsed to
    ``Float64`` here, plain numerics are cast per the pinned column maps, and
    everything else stays ``Utf8`` for silver typing. Zips that are absent
    from ``raw_dir`` are skipped silently, so this is safe to run alongside
    the SF importers.

    Args:
        raw_dir: Source directory holding the download zips. Defaults to
            ``FNMAConfig.FNMA_RAW_DIR``.
        bronze_dir: Destination bronze root (outputs go in its
            ``multifamily/`` subfolder). Defaults to
            ``FNMAConfig.FNMA_BRONZE_DIR``.
        overwrite: If False, skip releases whose bronze parquet already exists.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    raw_dir = raw_dir or FNMAConfig.FNMA_RAW_DIR
    bronze_dir = bronze_dir or FNMAConfig.FNMA_BRONZE_DIR
    mf_bronze_dir = bronze_dir / "multifamily"

    # (archive, member, output stem, numeric map, dollar cols) per release CSV
    jobs: list[tuple[Path, str, str, dict[str, type[pl.DataType]], tuple[str, ...]]] = []
    perf_zip = raw_dir / MF_PERF_ZIP_NAME
    if perf_zip.exists():
        with zipfile.ZipFile(perf_zip, "r") as z:
            for name in z.namelist():
                m = MF_PERF_CSV_PATTERN.match(name)
                if m:
                    stem = f"loan_performance_{m.group('yyyymm')}"
                    jobs.append((perf_zip, name, stem, MF_PERF_NUMERIC_COLS, MF_DOLLAR_COLS))
    dscr_zip = raw_dir / MF_DSCR_ZIP_NAME
    if dscr_zip.exists():
        with zipfile.ZipFile(dscr_zip, "r") as z:
            for name in z.namelist():
                m = MF_DSCR_CSV_PATTERN.match(name)
                if m:
                    stem = f"current_dscr_{m.group('yyyy')}{m.group('mm')}"
                    jobs.append((dscr_zip, name, stem, MF_DSCR_NUMERIC_COLS, ()))

    logger.info("=" * 60)
    logger.info("Multifamily import: %s CSV(s) found in %s", len(jobs), raw_dir)
    logger.info("Bronze: %s", mf_bronze_dir)
    logger.info("=" * 60)

    successful = failed = 0
    for i, (archive, member, stem, numeric_cols, dollar_cols) in enumerate(jobs, 1):
        logger.info("[%s/%s] %s", i, len(jobs), stem)
        output_path = mf_bronze_dir / f"{stem}.parquet"
        if not should_process_file(output_path, overwrite):
            logger.info("  Skipping %s (exists; --overwrite to rebuild)", output_path.name)
            successful += 1
            continue
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                with zipfile.ZipFile(archive, "r") as z:
                    logger.info("  Extracting %s from %s", member, archive.name)
                    z.extract(member, td_path)
                _mf_csv_to_parquet(td_path / member, numeric_cols, dollar_cols, output_path)
            successful += 1
        except Exception:
            logger.exception("  Failed %s from %s", member, archive.name)
            failed += 1

    return {"processed": len(jobs), "successful": successful, "failed": failed}


def run_bronze_import(
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    schema_file: Path | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import all per-quarter zip files from the raw directory to bronze.

    Skips bulk archives (Performance_All.zip, Exclusions.zip, etc.) by matching
    only filenames that look like ``YYYYQ[1-4]*.zip``.

    Args:
        raw_dir: Source directory containing per-quarter zips. Defaults to
            ``FNMAConfig.FNMA_RAW_DIR``.
        bronze_dir: Destination bronze directory. Defaults to
            ``FNMAConfig.FNMA_BRONZE_DIR``.
        schema_file: Path to the field-layout CSV. Defaults to
            ``FNMAConfig.FNMA_SCHEMA_FILE``.
        overwrite: If False, skip files whose bronze parquet already exists.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    raw_dir = raw_dir or FNMAConfig.FNMA_RAW_DIR
    bronze_dir = bronze_dir or FNMAConfig.FNMA_BRONZE_DIR
    schema_file = schema_file or FNMAConfig.FNMA_SCHEMA_FILE

    dict_df = read_data_dictionary(schema_file)
    column_names, schema = create_column_schema(dict_df)

    quarter_zips = sorted(
        p
        for p in raw_dir.glob("*.zip")
        if QUARTER_ZIP_PATTERN.match(p.name) and "EXCL" not in p.stem
    )

    logger.info("Found %s per-quarter zip(s) in %s", len(quarter_zips), raw_dir)
    successful = failed = 0
    for i, zp in enumerate(quarter_zips, 1):
        logger.info("[%s/%s] %s", i, len(quarter_zips), zp.name)
        ok = import_quarter_zip(zp, column_names, schema, bronze_dir, overwrite=overwrite)
        if ok:
            successful += 1
        else:
            failed += 1

    return {"processed": len(quarter_zips), "successful": successful, "failed": failed}


def run_performance_all_import(
    archive_path: Path | None = None,
    bronze_dir: Path | None = None,
    schema_file: Path | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    quarters: list[str] | None = None,
    tmp_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import quarters from the bulk ``Performance_All.zip`` archive.

    The per-quarter zips `run_bronze_import` reads cover only the recent
    vintages; the full 2000-forward history is published solely inside this bulk
    archive. Defaults to ``overwrite=False`` so a long run resumes where it left
    off rather than rebuilding quarters already in bronze -- the opposite of
    `import_performance_all`'s own default, which treats the archive as the
    freshest snapshot for a targeted refresh.

    Args:
        archive_path: Path to the archive. Defaults to ``Performance_All.zip``
            under ``FNMAConfig.FNMA_RAW_DIR``.
        bronze_dir: Destination bronze directory. Defaults to
            ``FNMAConfig.FNMA_BRONZE_DIR``.
        schema_file: Path to the field-layout CSV. Defaults to
            ``FNMAConfig.FNMA_SCHEMA_FILE``.
        min_year: Lowest year to import (inclusive). ``None`` = no lower bound.
        max_year: Highest year to import (inclusive). ``None`` = no upper bound.
        quarters: Explicit quarter stems (e.g. ``["2005Q3"]``); when set,
            ``min_year``/``max_year`` are ignored.
        tmp_dir: Extraction scratch space. Defaults to ``FNMAConfig.FNMA_TMP_DIR``
            on the data volume -- NOT the system temp dir, which cannot hold the
            back-history members (2003Q3 is 40.5 GB).
        overwrite: Rebuild quarters whose bronze parquet already exists.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    archive_path = archive_path or FNMAConfig.FNMA_RAW_DIR / PERFORMANCE_ALL_ARCHIVE
    bronze_dir = bronze_dir or FNMAConfig.FNMA_BRONZE_DIR
    schema_file = schema_file or FNMAConfig.FNMA_SCHEMA_FILE
    tmp_dir = tmp_dir or FNMAConfig.FNMA_TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if not archive_path.exists():
        logger.warning("%s not found; skipping: %s", PERFORMANCE_ALL_ARCHIVE, archive_path)
        return {"processed": 0, "successful": 0, "failed": 0}

    dict_df = read_data_dictionary(schema_file)
    column_names, schema = create_column_schema(dict_df)

    return import_performance_all(
        archive_path=archive_path,
        column_names=column_names,
        schema=schema,
        bronze_dir=bronze_dir,
        min_year=0 if min_year is None else min_year,
        max_year=max_year,
        overwrite=overwrite,
        quarters=quarters,
        tmp_dir=tmp_dir,
    )


def run_exclusions_import(
    archive_path: Path | None = None,
    bronze_dir: Path | None = None,
    schema_file: Path | None = None,
    *,
    tmp_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import the bulk ``Exclusions.zip`` archive whenever it is present.

    In the default bronze set: the exclusion loans are always imported alongside
    the standard quarters. A missing archive is not an error -- there is simply
    nothing to import.

    Args:
        archive_path: Path to the archive. Defaults to ``Exclusions.zip`` under
            ``FNMAConfig.FNMA_RAW_DIR``.
        bronze_dir: Destination bronze directory. Defaults to
            ``FNMAConfig.FNMA_BRONZE_DIR``.
        schema_file: Path to the field-layout CSV. Defaults to
            ``FNMAConfig.FNMA_SCHEMA_FILE``.
        tmp_dir: Extraction scratch space. Defaults to ``FNMAConfig.FNMA_TMP_DIR``
            on the data volume -- the largest members run to 14.5 GB.
        overwrite: Rebuild quarters whose bronze parquet already exists.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    archive_path = archive_path or FNMAConfig.FNMA_RAW_DIR / EXCLUSIONS_ARCHIVE
    bronze_dir = bronze_dir or FNMAConfig.FNMA_BRONZE_DIR
    schema_file = schema_file or FNMAConfig.FNMA_SCHEMA_FILE

    if not archive_path.exists():
        logger.info("%s not found; no exclusion data to import", archive_path)
        return {"processed": 0, "successful": 0, "failed": 0}

    tmp_dir = tmp_dir or FNMAConfig.FNMA_TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)
    column_names, schema = create_column_schema(read_data_dictionary(schema_file))
    return import_exclusions(
        archive_path, column_names, schema, bronze_dir, overwrite=overwrite, tmp_dir=tmp_dir
    )


if __name__ == "__main__":
    run_bronze_import()
