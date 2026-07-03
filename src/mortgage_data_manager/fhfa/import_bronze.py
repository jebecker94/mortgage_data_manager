"""FHFA data loading functions - bronze layer."""

from __future__ import annotations

import fnmatch
import io
import zipfile
from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fhfa.config import BRONZE_DIR, DATASET_MAP, RAW_DIR, SCHEMAS_DIR
from mortgage_data_manager.fhfa.schemas import load_master_dictionary

logger = get_logger(__name__)


def load_fixed_width_with_master_from_buffer(
    buffer: io.BytesIO,
    year: int,
    dataset: str,
    *,
    dictionary_root: Path | None = None,
    encoding: str = 'latin-1',
) -> pl.DataFrame:
    """Load fixed-width FHFA file from buffer using master dictionary.

    Args:
        buffer: Buffer containing file data
        year: Year of the data
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b')
        dictionary_root: Dictionary root. Defaults to SCHEMAS_DIR.
        encoding: File encoding. Default 'latin-1'.

    Returns:
        Loaded data with 'year' column added
    """
    dict_root = dictionary_root or SCHEMAS_DIR
    master = load_master_dictionary(dataset, dictionary_root=dict_root)
    year_dict = master.filter(pl.col('year') == year)

    if len(year_dict) == 0:
        raise ValueError(f"No dictionary found for {dataset} year {year}")

    names = year_dict['Field Name'].to_list()
    widths = year_dict['Field Width'].cast(pl.Int64).to_list()

    # Make field names unique if there are duplicates
    seen = {}
    unique_names = []
    for name in names:
        if name in seen:
            seen[name] += 1
            unique_names.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            unique_names.append(name)
    names = unique_names

    # Build column specs
    colspecs = []
    cursor = 0
    for w in widths:
        colspecs.append((cursor, cursor + w))
        cursor += w + 1  # +1 for separator

    # Load using pandas read_fwf from buffer
    pdf = pd.read_fwf(buffer, colspecs=colspecs, names=names, header=None, encoding=encoding)
    df = pl.from_pandas(pdf, include_index=False)

    # Add metadata
    return df.with_columns([pl.lit(year).alias('year')])


def save_to_bronze(
    dataset: str,
    years: list[int],
    *,
    raw_dir: Path | None = None,
    output_dir: Path | None = None,
    dictionary_root: Path | None = None,
    overwrite: bool = False,
) -> None:
    """Batch load fixed-width files to bronze layer.

    Args:
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b')
        years: Years to load
        raw_dir: Raw data directory. Defaults to RAW_DIR.
        output_dir: Output directory. Defaults to BRONZE_DIR.
        dictionary_root: Dictionary root. Defaults to SCHEMAS_DIR.
        overwrite: Whether to overwrite existing bronze files. Default False.

    Returns:
        None
    """
    raw = raw_dir or RAW_DIR
    output = output_dir or BRONZE_DIR

    # Create bronze directory
    bronze_dir = output / dataset
    bronze_dir.mkdir(parents=True, exist_ok=True)

    if dataset not in DATASET_MAP:
        raise ValueError(f"Unknown dataset: {dataset}. Valid types: {list(DATASET_MAP.keys())}")

    family, letter, suffix = DATASET_MAP[dataset]

    for year in years:

        bronze_path = bronze_dir / f'{dataset}_{year}.parquet'
        if not should_process_file(bronze_path, overwrite):
            logger.info("Bronze file already exists for %s year %d: %s", dataset, year, bronze_path)
            continue

        # Find zip file for this year. Match both the original
        # `{year}_enterprise_pudb.zip` and any later FHFA reissue such as
        # `{year}_enterprise_pudb_corrected.zip` (published 2026-04 for 2020).
        # When both exist, prefer the one whose name contains "corrected".
        zip_pattern = f'{year}_enterprise_pudb*.zip'
        zip_candidates = [
            p for p in raw.glob(f'**/{zip_pattern}') if '_archive' not in p.parts
        ]

        if not zip_candidates:
            logger.warning("No zip file found for year %d (pattern: %s)", year, zip_pattern)
            continue

        zip_candidates.sort(key=lambda p: ('corrected' not in p.name.lower(), p.name))
        zip_file = zip_candidates[0]
        logger.info("Processing %s", zip_file.name)

        # Search for data files inside zip
        file_pattern = f'*_{family}{year}{letter}_{suffix}.txt'
        year_dfs = []

        try:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                # Get all txt files matching pattern
                matching_files = [name for name in zf.namelist()
                                 if fnmatch.fnmatch(name.split('/')[-1], file_pattern)]

                if not matching_files:
                    logger.warning("  No data files found in zip (pattern: %s)", file_pattern)
                    continue

                logger.info("  Found %d file(s) in zip", len(matching_files))

                # Load each file from zip
                for file_name in matching_files:
                    logger.info("    Loading %s", file_name.split('/')[-1])
                    try:
                        # Read file content from zip
                        with zf.open(file_name) as f:
                            content = io.BytesIO(f.read())

                        # Load the file
                        df = load_fixed_width_with_master_from_buffer(
                            content, year, dataset, dictionary_root=dictionary_root
                        )
                        year_dfs.append(df)
                    except Exception as e:
                        logger.error("    Failed to load %s: %s", file_name, e)

        except Exception as e:
            logger.error("  Failed to open zip file: %s", e)
            continue

        if not year_dfs:
            logger.warning("No data loaded for %s year %d", dataset, year)
            continue

        # Combine fnma and fhlmc data for this year
        combined_df = pl.concat(year_dfs, how='diagonal_relaxed')
        logger.info("  Combined %d file(s) into %d total records", len(year_dfs), len(combined_df))

        # Save to bronze
        try:
            combined_df.write_parquet(bronze_path)
            logger.info("  Saved bronze: %s", bronze_path)
        except Exception as e:
            logger.error("  Failed to save bronze for %s year %d: %s", dataset, year, e)


# ---------------------------------------------------------------------------
# HUD-era GSE PUDB multifamily backfill (1993-2007)
# ---------------------------------------------------------------------------
# Before HERA moved the GSE Public Use Database from HUD to FHFA (July 2008),
# HUD published the same three multifamily files this package already ingests
# for 2008+ (mf_c / mf_property_b / mf_unit_b). The HUD-era files are
# whitespace-delimited (one blank space between fields) rather than fixed-width,
# carry CRLF line endings, and drift across several schema eras. They are loaded
# here with field names matching the modern FHFA bronze where the concept is
# identical, so the existing silver harmonization (rename dictionary, Census
# Year derivation and sentinel masking) processes HUD-era and modern years
# through the same code path. Years do not overlap the modern data, so HUD-era
# bronze lands in the same ``bronze/{dataset}/{dataset}_{year}.parquet`` files.
#
# Era boundaries and field counts were verified empirically against the raw
# files and the bundled PDF data dictionaries (which exist for 1996-2007). The
# 1993-1995 era ships no dictionary: its three undocumented middle census fields
# and one early property dollar field are carried under provisional names and
# excluded from harmonized silver.

# Datasets and year span covered by the HUD-era backfill.
HUD_ERA_DATASETS: list[str] = ['mf_c', 'mf_property_b', 'mf_unit_b']
HUD_ERA_MIN_YEAR: int = 1993
HUD_ERA_MAX_YEAR: int = 2007

# Original archive member suffix (upper-cased) for each dataset.
_HUD_ERA_FILE_SUFFIX: dict[str, str] = {
    'mf_c': 'C.LOA',
    'mf_property_b': 'B.LOA',
    'mf_unit_b': 'B.UNI',
}


def hud_era_layout(dataset: str, year: int) -> list[str]:
    """Return the ordered bronze column names for a HUD-era MF file.

    Field names match the modern FHFA bronze names where the concept is
    identical so that downstream silver harmonization is shared. The census
    vintage embedded in the names is 1990 for 1993-1998 and 2000 for
    1999-2007.

    Args:
        dataset: One of ``mf_c``, ``mf_property_b``, ``mf_unit_b``.
        year: Data year (1993-2007).

    Returns:
        Ordered list of column names, one per whitespace-delimited field.

    Raises:
        ValueError: If the dataset has no HUD-era layout.
    """
    v = 1990 if year <= 1998 else 2000  # census geography vintage

    if dataset == 'mf_unit_b':
        # Unit-class file: a single 6-field layout across all years 1993-2007.
        return [
            'Enterprise Flag', 'Record Number',
            'Unit Type XX-Number of Bedrooms', 'Unit Type XX-Number of Units',
            'Unit Type XX-Affordability Level', 'Unit Type XX-Tenant Income Indicator',
        ]

    if dataset == 'mf_c':
        head = [
            'Enterprise Flag', 'Record Number', 'US Postal State Code',
            'Metropolitan Statistical Area (MSA) Code',
            f'County - {v} Census', f'Census Tract - {v} Census',
        ]
        tail = [
            f'{v} Census Tract - Percent Minority',
            f'{v} Census Tract - Median Income',
            f'{v} Local Area Median Income',
            'Tract Income Ratio',
            f'Area Median Family Income ({year})',
            'Acquisition UPB Range',
        ]
        if year <= 1995:
            # 16 fields: three undocumented middle fields, no targeting flag.
            return (
                head
                + ['HUD Provisional Census Field 1',
                   'HUD Provisional Census Field 2',
                   'HUD Provisional Census Field 3']
                + tail
                + ['Type of Seller Institution']
            )
        # 14 fields (1996-2007); the targeting flag was renamed in 2004.
        targeted = (
            'Underserved Areas Indicator' if year >= 2004
            else 'Geographically Targeted Indicator'
        )
        return head + tail + ['Type of Seller Institution', targeted]

    if dataset == 'mf_property_b':
        if year <= 1995:
            # 10 fields. The first eight match the 1996-2003 layout; in place of
            # the targeting flag this early era carries two trailing dollar
            # fields that are zero for most records (almost certainly
            # goal-qualifying dollar amounts rather than acquisition UPB). They
            # are undocumented, so both are kept under provisional names rather
            # than asserting a mapping into ``upb_acquisition``.
            return [
                'Enterprise Flag', 'Record Number',
                f'{v} Census Tract - Percent Minority', 'Tract Income Ratio',
                'Affordability Category', 'Purpose of Loan', 'Government Insurance',
                'Total Number of Units', 'HUD Provisional Property Amount 1',
                'HUD Provisional Property Amount 2',
            ]
        if year <= 2003:
            # 9 fields.
            return [
                'Enterprise Flag', 'Record Number',
                f'{v} Census Tract - Percent Minority', 'Tract Income Ratio',
                'Affordability Category', 'Purpose of Loan', 'Government Insurance',
                'Total Number of Units', 'Geographically Targeted Indicator',
            ]
        # 11 fields (2004-2007): adds Date of Mortgage Note + Type of Seller.
        return [
            'Enterprise Flag', 'Record Number',
            f'{v} Census Tract - Percent Minority', 'Tract Income Ratio',
            'Affordability Category', 'Date of Mortgage Note', 'Purpose of Loan',
            'Type of Seller Institution', 'Government Insurance',
            'Total Number of Units', 'Underserved Areas Indicator',
        ]

    raise ValueError(f"No HUD-era layout for dataset {dataset!r}")


def _find_hud_era_member(
    hud_dir: Path, dataset: str, year: int
) -> tuple[Path, str] | tuple[None, None]:
    """Locate the archive zip and member holding a HUD-era MF file.

    Scans every ``*.zip`` under ``hud_dir`` for the exact member filename
    (case-insensitive, ignoring any internal sub-folder), matching both the
    two-digit (``MF93C.LOA``) and four-digit (``MF1999C.LOA``) year forms.

    Args:
        hud_dir: Directory holding the HUD-era archive zips.
        dataset: One of ``mf_c``, ``mf_property_b``, ``mf_unit_b``.
        year: Data year.

    Returns:
        ``(zip_path, member_name)`` if found, otherwise ``(None, None)``.
    """
    suffix = _HUD_ERA_FILE_SUFFIX[dataset]
    wanted = {f'MF{year}{suffix}', f'MF{year % 100:02d}{suffix}'}
    for zip_path in sorted(hud_dir.glob('*.zip')):
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if name.split('/')[-1].upper() in wanted:
                        return zip_path, name
        except zipfile.BadZipFile:
            logger.warning("Skipping unreadable zip: %s", zip_path)
    return None, None


def _parse_hud_era_delimited(raw_bytes: bytes, names: list[str]) -> pl.DataFrame:
    """Parse a whitespace-delimited HUD-era MF file into a typed DataFrame.

    Each non-blank line is split on runs of whitespace (after stripping the
    CR of the CRLF terminator). Rows whose token count does not match the
    expected field count are dropped (HUD files carry an occasional stray
    trailing line). Columns are typed by inspection: a column is Float64 if any
    value contains a decimal point, otherwise Int64 — matching the integer
    storage of FIPS/code fields in the modern bronze.

    Args:
        raw_bytes: Raw bytes of the file as read from the zip.
        names: Ordered column names from :func:`hud_era_layout`.

    Returns:
        Typed DataFrame with one column per field (no ``year`` column yet).
    """
    n = len(names)
    rows: list[list[str]] = []
    for line in raw_bytes.decode('latin-1').replace('\r', '').split('\n'):
        if not line.strip():
            continue
        toks = line.split()
        if len(toks) == n:
            rows.append(toks)

    columns = {name: [r[i] for r in rows] for i, name in enumerate(names)}
    df = pl.DataFrame(columns, schema={name: pl.Utf8 for name in names})

    cast_exprs = []
    for name in names:
        has_decimal = bool(df[name].str.contains('.', literal=True).any())
        target = pl.Float64 if has_decimal else pl.Int64
        cast_exprs.append(pl.col(name).cast(target, strict=False))
    return df.with_columns(cast_exprs)


def build_bronze_mf_hud_era(
    datasets: list[str],
    years: list[int],
    *,
    raw_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> int:
    """Build bronze parquet for the HUD-era (1993-2007) multifamily backfill.

    Reads the whitespace-delimited HUD GSE PUDB multifamily files from the
    archive zips under ``raw/hud_era`` and writes one typed parquet per
    dataset-year into the shared ``bronze/{dataset}/`` namespace.

    Args:
        datasets: Subset of :data:`HUD_ERA_DATASETS` to process.
        years: Years to process (clamped to 1993-2007).
        raw_dir: Raw root. Defaults to :data:`RAW_DIR` (``hud_era`` subdir used).
        output_dir: Bronze root. Defaults to :data:`BRONZE_DIR`.
        overwrite: Whether to rebuild existing bronze files. Default False.

    Returns:
        Number of bronze files written.
    """
    raw = raw_dir or RAW_DIR
    output = output_dir or BRONZE_DIR
    hud_dir = raw / 'hud_era'

    written = 0
    for dataset in datasets:
        if dataset not in HUD_ERA_DATASETS:
            raise ValueError(
                f"Unknown HUD-era dataset: {dataset}. Valid: {HUD_ERA_DATASETS}"
            )
        bronze_dir = output / dataset
        bronze_dir.mkdir(parents=True, exist_ok=True)

        for year in years:
            if not (HUD_ERA_MIN_YEAR <= year <= HUD_ERA_MAX_YEAR):
                continue
            bronze_path = bronze_dir / f'{dataset}_{year}.parquet'
            if not should_process_file(bronze_path, overwrite):
                logger.info("Bronze already exists for %s %d: %s",
                            dataset, year, bronze_path)
                continue

            zip_path, member = _find_hud_era_member(hud_dir, dataset, year)
            if member is None:
                logger.warning("No HUD-era file found for %s %d in %s",
                               dataset, year, hud_dir)
                continue

            names = hud_era_layout(dataset, year)
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    raw_bytes = zf.read(member)
                df = _parse_hud_era_delimited(raw_bytes, names)
                df = df.with_columns(pl.lit(year).cast(pl.Int32).alias('year'))
                df.write_parquet(bronze_path)
                written += 1
                logger.info("Saved HUD-era bronze %s %d (%d rows): %s",
                            dataset, year, len(df), bronze_path)
            except Exception as e:
                logger.error("Failed HUD-era bronze for %s %d: %s",
                             dataset, year, e)

    return written
