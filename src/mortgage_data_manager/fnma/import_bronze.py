"""Import FNMA loan performance data from zipped CSVs to Parquet files.

Reads schema from the Combined Glossary Excel data dictionary and applies
proper data types. Uses Polars for efficient memory usage with streaming.

Two source layouts are supported:

- Per-quarter zips (legacy): ``data/fnma/raw/2019Q1.zip`` etc., each holding
  a single ``{quarter}.csv``.
- Bulk archive: ``Performance_All.zip`` holding many ``{quarter}.csv`` members
  side by side (the snapshot Fannie publishes covering the full history).
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fnma.config import FNMAConfig

logger = get_logger(__name__)

# Per-quarter zips look like 2019Q1.zip; exclusion zips like 2019Q1EXCL.zip.
# Used to filter out bulk archives (Performance_All.zip, Exclusions.zip, ...).
QUARTER_ZIP_PATTERN = re.compile(r'^(?P<year>\d{4})Q(?P<quarter>[1-4])(?P<suffix>.*)\.zip$')
QUARTER_CSV_PATTERN = re.compile(r'^(?P<year>\d{4})Q(?P<quarter>[1-4])\.csv$')
EXCL_CSV_PATTERN = re.compile(r'^(?P<year>\d{4})Q(?P<quarter>[1-4])EXCL\.csv$')

# Per the Historical Loan Performance Exclusion Dataset reference (Fannie Mae,
# 6.17.21), exclusion files use the Primary dataset layout as of mid-2021 (108
# fields) plus 4 extra indicator columns appended at positions 109-112.
EXCLUSION_PRIMARY_FIELD_COUNT = 108
EXCLUSION_EXTRA_FIELDS: list[tuple[str, type[pl.DataType]]] = [
    ('Non-Standard Documentation Indicator', pl.Utf8),
    ('Non-Standard Underwriting or Eligibility Indicator', pl.Utf8),
    ('Government Insured/Guarantee Indicator', pl.Utf8),
    ('Negative Amortization Indicator', pl.Utf8),
]


def read_data_dictionary(excel_path: Path) -> pd.DataFrame:
    """Read the Combined Glossary sheet and extract field definitions."""
    logger.info(f"Reading data dictionary from {excel_path}")
    df_dict = pd.read_excel(excel_path, sheet_name='Combined Glossary')
    df_dict = df_dict[['Field Position', 'Field Name', 'Type', 'Max Length']].copy()
    df_dict = df_dict.dropna(subset=['Field Position'])
    df_dict['Field Position'] = df_dict['Field Position'].astype(int)
    logger.info(f"Found {len(df_dict)} field definitions")
    return df_dict


def infer_polars_dtype(type_str, max_length_str):
    """Infer Polars dtype from glossary Type and Max Length columns."""
    if pd.isna(type_str):
        return pl.Utf8

    type_str = str(type_str).strip().upper()
    max_length_str = str(max_length_str) if not pd.isna(max_length_str) else ''

    if type_str == 'NUMERIC':
        return pl.Float64 if '.' in max_length_str else pl.Int64
    if type_str == 'DATE':
        return pl.Utf8
    return pl.Utf8


def create_column_schema(dict_df: pd.DataFrame) -> tuple[list[str], dict[str, type[pl.DataType]]]:
    """Build ordered column names + Polars schema from the glossary."""
    dict_df = dict_df.sort_values('Field Position').reset_index(drop=True)
    schema: dict[str, type[pl.DataType]] = {}
    for _, row in dict_df.iterrows():
        schema[row['Field Name']] = infer_polars_dtype(row['Type'], row['Max Length'])
    column_names = list(schema.keys())
    logger.info(f"Column schema created: {len(column_names)} fields")
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
        separator='|',
        has_header=False,
        new_columns=column_names,
        schema_overrides=schema,
        null_values=['', 'XX'],
        try_parse_dates=False,
        ignore_errors=True,
    )
    row_count = lf.select(pl.len()).collect().item()
    logger.info(f"  {csv_path.name}: {row_count:,} rows")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lf.sink_parquet(output_path, compression='snappy')
    size_mb = output_path.stat().st_size / 1024 ** 2
    logger.info(f"  Wrote {output_path.name} ({size_mb:.1f} MB)")
    return row_count


def import_member_from_archive(
    archive_path: Path,
    member: str,
    output_stem: str,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    overwrite: bool = False,
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

    Returns:
        True on success, False on failure.
    """
    output_path = bronze_dir / f'{output_stem}.parquet'
    if not should_process_file(output_path, overwrite):
        logger.info(f"  Skipping {output_path.name} (exists; --overwrite to rebuild)")
        return True

    try:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with zipfile.ZipFile(archive_path, 'r') as z:
                logger.info(f"  Extracting {member} from {archive_path.name}")
                z.extract(member, td_path)
            _csv_to_parquet(td_path / member, column_names, schema, output_path)
        return True
    except Exception as e:
        logger.error(f"  Failed {member} from {archive_path.name}: {e}")
        return False


def import_quarter_zip(
    zip_path: Path,
    column_names: list[str],
    schema: dict[str, type[pl.DataType]],
    bronze_dir: Path,
    overwrite: bool = False,
) -> bool:
    """Import a per-quarter zip (single CSV named ``{stem}.csv``)."""
    return import_member_from_archive(
        archive_path=zip_path,
        member=f'{zip_path.stem}.csv',
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

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    with zipfile.ZipFile(archive_path, 'r') as z:
        all_members = z.namelist()

    matches: list[tuple[str, str]] = []  # (member, output_stem)
    for name in all_members:
        m = QUARTER_CSV_PATTERN.match(name)
        if not m:
            continue
        stem = f"{m.group('year')}Q{m.group('quarter')}"
        year = int(m.group('year'))
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
    logger.info(f"Performance_All import: {archive_path.name}")
    logger.info(f"Bronze: {bronze_dir}")
    logger.info(f"Quarters to process: {len(matches)}")
    logger.info("=" * 60)

    successful = failed = 0
    for i, (member, stem) in enumerate(matches, 1):
        logger.info(f"[{i}/{len(matches)}] {stem}")
        ok = import_member_from_archive(
            archive_path=archive_path,
            member=member,
            output_stem=stem,
            column_names=column_names,
            schema=schema,
            bronze_dir=bronze_dir,
            overwrite=overwrite,
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
    min_year: int = 2018,
    max_year: int | None = None,
    overwrite: bool = True,
    quarters: list[str] | None = None,
) -> dict[str, int]:
    """Import exclusion CSVs from the bulk Exclusions archive.

    Bronze parquets are written as ``{quarter}EXCL.parquet`` alongside the
    Primary quarterly parquets in ``bronze_dir`` (no subfolder).

    Args:
        archive_path: Path to ``Exclusions.zip``.
        column_names: Ordered field names from the full Primary glossary.
        schema: Polars dtypes from the full Primary glossary.
        bronze_dir: Destination bronze directory.
        min_year: Lowest year to import (inclusive). Defaults to 2018.
        max_year: Highest year to import (inclusive). None = no upper bound.
        overwrite: If True, replace existing bronze parquets.
        quarters: Optional explicit list of quarter stems (e.g.
            ``["2018Q1EXCL"]``). When set, year filters are ignored.

    Returns:
        Counts dict with keys ``processed``, ``successful``, ``failed``.
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    excl_names, excl_schema = build_exclusion_schema(column_names, schema)

    with zipfile.ZipFile(archive_path, 'r') as z:
        all_members = z.namelist()

    matches: list[tuple[str, str]] = []
    for name in all_members:
        m = EXCL_CSV_PATTERN.match(name)
        if not m:
            continue
        stem = f"{m.group('year')}Q{m.group('quarter')}EXCL"
        year = int(m.group('year'))
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
    logger.info(f"Exclusions import: {archive_path.name}")
    logger.info(f"Bronze: {bronze_dir}")
    logger.info(f"Quarters to process: {len(matches)}  (schema: {len(excl_names)} fields)")
    logger.info("=" * 60)

    successful = failed = 0
    for i, (member, stem) in enumerate(matches, 1):
        logger.info(f"[{i}/{len(matches)}] {stem}")
        ok = import_member_from_archive(
            archive_path=archive_path,
            member=member,
            output_stem=stem,
            column_names=excl_names,
            schema=excl_schema,
            bronze_dir=bronze_dir,
            overwrite=overwrite,
        )
        if ok:
            successful += 1
        else:
            failed += 1

    return {"processed": len(matches), "successful": successful, "failed": failed}


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
        schema_file: Path to the Combined Glossary xlsx. Defaults to
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
        p for p in raw_dir.glob('*.zip')
        if QUARTER_ZIP_PATTERN.match(p.name) and 'EXCL' not in p.stem
    )

    logger.info(f"Found {len(quarter_zips)} per-quarter zip(s) in {raw_dir}")
    successful = failed = 0
    for i, zp in enumerate(quarter_zips, 1):
        logger.info(f"[{i}/{len(quarter_zips)}] {zp.name}")
        ok = import_quarter_zip(zp, column_names, schema, bronze_dir, overwrite=overwrite)
        if ok:
            successful += 1
        else:
            failed += 1

    return {"processed": len(quarter_zips), "successful": successful, "failed": failed}


if __name__ == '__main__':
    run_bronze_import()
