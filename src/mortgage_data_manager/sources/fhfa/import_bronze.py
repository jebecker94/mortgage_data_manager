"""FHFA data loading functions - bronze layer."""

from __future__ import annotations

import fnmatch
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.sources.fhfa.config import (
    CSV_FILE_STEMS,
    DATASET_MAP,
    FIRST_CSV_YEAR,
    FHFAConfig,
)
from mortgage_data_manager.sources.fhfa.schemas import load_master_dictionary, load_year_dictionary

logger = get_logger(__name__)

# CSV-era fields that restate the file's own vintage. They are consumed while
# loading (checked against the requested year / used to resolve the ``[CYEAR]``
# placeholder) and not carried into bronze, whose fixed-width era has no such
# columns: ``year`` is added as before and silver derives ``Census Year`` from the
# census-tract column names.
_CSV_ACQUISITION_YEAR = "Acquisition Year"
_CSV_CENSUS_YEAR = "Census Year"


def _csv_field_name(template: str, year: int, census_year: int) -> str:
    """Render a CSV-era dictionary field name into its fixed-width-era spelling.

    The 2025+ dictionaries template the vintage (``County - [CYEAR] Census``,
    ``Area Median Family Income ([YEAR])``); every earlier year spells it out, and
    the silver rename dictionary keys on those spellings. The multifamily file
    drops the parentheses around ``[YEAR]``, which every earlier year carried, so
    both forms render as ``(year)``.
    """
    name = re.sub(r"\(?\[YEAR\]\)?", f"({year})", template)
    return name.replace("[CYEAR]", str(census_year))


def load_csv_with_dictionary(
    raw_bytes: bytes,
    year: int,
    dataset: str,
    *,
    dictionary_root: Path | None = None,
) -> pl.DataFrame:
    """Load a CSV-era (2025+) FHFA file under its fixed-width-era bronze names.

    The CSV headers are short codes (``tract_minority_pct``); the year's converted
    dictionary maps each to the ``Field Name`` the fixed-width bronze uses, so
    silver processes both eras through one code path. The header set must equal
    the dictionary's exactly -- a renamed or added column raises rather than
    passing through unmapped.

    Args:
        raw_bytes: The CSV file's bytes, as read from its zip.
        year: Acquisition year of the file.
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b').
        dictionary_root: Dictionary root. Defaults to FHFAConfig.FHFA_SCHEMAS_DIR.

    Returns:
        Loaded data with a 'year' column added.

    Raises:
        ValueError: If the headers disagree with the dictionary, or the file's
            acquisition/census year columns are not a single expected value.
    """
    dictionary = load_year_dictionary(dataset, year, dictionary_root=dictionary_root)
    templates = dict(zip(dictionary["Header Name"], dictionary["Field Name"], strict=True))

    # Full-file inference keeps dtypes independent of where a column's first
    # non-integer value happens to sit. Blank cells are FHFA's CSV-era
    # "not available" (the fixed-width era used in-band codes such as 999999).
    df = pl.read_csv(raw_bytes, infer_schema_length=None)

    unmapped = sorted(set(df.columns) - set(templates))
    missing = sorted(set(templates) - set(df.columns))
    if unmapped or missing:
        raise ValueError(
            f"{dataset} {year}: CSV headers disagree with the data dictionary "
            f"(not in dictionary: {unmapped}; in dictionary but not the file: {missing})"
        )

    header_of = {field: header for header, field in templates.items()}
    if _CSV_ACQUISITION_YEAR not in header_of or _CSV_CENSUS_YEAR not in header_of:
        raise ValueError(f"{dataset} {year}: dictionary lacks the acquisition/census year fields")
    acq_col, census_col = header_of[_CSV_ACQUISITION_YEAR], header_of[_CSV_CENSUS_YEAR]
    acq_years = df[acq_col].unique().to_list()
    census_years = df[census_col].unique().to_list()
    if acq_years != [year]:
        raise ValueError(f"{dataset} {year}: {acq_col} holds {acq_years}, expected [{year}]")
    if len(census_years) != 1 or census_years[0] is None:
        raise ValueError(f"{dataset} {year}: {census_col} is not a single vintage: {census_years}")
    census_year = int(census_years[0])

    df = df.drop(acq_col, census_col)
    df = df.rename({h: _csv_field_name(templates[h], year, census_year) for h in df.columns})
    return df.with_columns(pl.lit(year).alias("year"))


def load_fixed_width_with_master_from_buffer(
    buffer: io.BytesIO,
    year: int,
    dataset: str,
    *,
    dictionary_root: Path | None = None,
    encoding: str = "latin-1",
) -> pl.DataFrame:
    """Load fixed-width FHFA file from buffer using master dictionary.

    Args:
        buffer: Buffer containing file data
        year: Year of the data
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b')
        dictionary_root: Dictionary root. Defaults to FHFAConfig.FHFA_SCHEMAS_DIR.
        encoding: File encoding. Default 'latin-1'.

    Returns:
        Loaded data with 'year' column added
    """
    dict_root = dictionary_root or FHFAConfig.FHFA_SCHEMAS_DIR
    master = load_master_dictionary(dataset, dictionary_root=dict_root)
    year_dict = master.filter(pl.col("year") == year)

    if len(year_dict) == 0:
        raise ValueError(f"No dictionary found for {dataset} year {year}")

    names = year_dict["Field Name"].to_list()
    widths = year_dict["Field Width"].cast(pl.Int64).to_list()

    # The PUDB layouts are fixed-width with exactly one blank space between fields, so a
    # correct dictionary must satisfy sum(widths) + n_fields - 1 == record length. A PDF
    # extraction that drops or mis-reads a field shifts every column after it, producing
    # plausible-looking garbage rather than an error, so verify against the data itself
    # before parsing. See DICTIONARY_PATCHES in fhfa/schemas.py.
    first_line = buffer.readline().decode(encoding).rstrip("\r\n")
    buffer.seek(0)
    expected_length = sum(widths) + len(widths) - 1
    if first_line and expected_length != len(first_line):
        raise ValueError(
            f"Dictionary width mismatch for {dataset} year {year}: the dictionary implies "
            f"{expected_length}-char records but the data has {len(first_line)}-char "
            f"records (delta {expected_length - len(first_line)}). The extracted dictionary "
            f"is wrong; the parse would silently shift columns."
        )

    # Make field names unique if there are duplicates
    seen: dict[str, int] = {}
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

    # Load using pandas read_fwf from buffer. FHFA publishes the PUDB out of SAS, so a
    # lone "." is its missing-numeric marker; without declaring it, pandas sees a
    # non-numeric token and demotes the whole column to object for that file only. That
    # made bronze dtypes depend on whether a given year happened to contain a "." -- and a
    # downstream String -> Int cast nulls decimal-formatted text ("1.0"), which silently
    # emptied mf_unit_b.unit_number_of_units for 2016 (531,374 rows). Declaring the
    # sentinel keeps inference deterministic across years and nulls only the marker.
    pdf = pd.read_fwf(
        buffer,
        colspecs=colspecs,
        names=names,
        header=None,
        encoding=encoding,
        na_values=["."],
    )
    df = pl.from_pandas(pdf, include_index=False)

    # Add metadata
    return df.with_columns([pl.lit(year).alias("year")])


def _load_csv_year(
    raw: Path, dataset: str, year: int, *, dictionary_root: Path | None
) -> pl.DataFrame | None:
    """Load one CSV-era dataset-year from ``{year}_pudb_{stem}.zip``, or None if absent."""
    stem = CSV_FILE_STEMS.get(dataset)
    if stem is None:
        logger.warning("%s has no file from %d on (FHFA discontinued it)", dataset, FIRST_CSV_YEAR)
        return None
    zip_path = raw / f"{year}_pudb_{stem}.zip"
    if not zip_path.exists():
        logger.warning("No CSV-era zip for %s year %d: %s", dataset, year, zip_path)
        return None
    logger.info("Processing %s", zip_path.name)
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"{zip_path.name}: expected one CSV member, found {members}")
        raw_bytes = zf.read(members[0])
    return load_csv_with_dictionary(raw_bytes, year, dataset, dictionary_root=dictionary_root)


def save_to_bronze(
    dataset: str,
    years: list[int],
    *,
    raw_dir: Path | None = None,
    output_dir: Path | None = None,
    dictionary_root: Path | None = None,
    overwrite: bool = False,
) -> None:
    """Batch load FHFA files to bronze layer.

    Years before `FIRST_CSV_YEAR` parse the fixed-width text files in
    ``{year}_enterprise_pudb*.zip``; later years read the headed CSV in
    ``{year}_pudb_{stem}.zip``. Both land under the same fixed-width-era column
    names, so silver is era-agnostic.

    Args:
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b')
        years: Years to load
        raw_dir: Raw data directory. Defaults to FHFAConfig.FHFA_RAW_DIR.
        output_dir: Output directory. Defaults to FHFAConfig.FHFA_BRONZE_DIR.
        dictionary_root: Dictionary root. Defaults to FHFAConfig.FHFA_SCHEMAS_DIR.
        overwrite: Whether to overwrite existing bronze files. Default False.

    Returns:
        None
    """
    raw = raw_dir or FHFAConfig.FHFA_RAW_DIR
    output = output_dir or FHFAConfig.FHFA_BRONZE_DIR

    # Create bronze directory
    bronze_dir = output / dataset
    bronze_dir.mkdir(parents=True, exist_ok=True)

    if dataset not in DATASET_MAP:
        raise ValueError(f"Unknown dataset: {dataset}. Valid types: {list(DATASET_MAP.keys())}")

    family, letter, suffix = DATASET_MAP[dataset]

    for year in years:
        bronze_path = bronze_dir / f"{dataset}_{year}.parquet"
        if not should_process_file(bronze_path, overwrite):
            logger.info("Bronze file already exists for %s year %d: %s", dataset, year, bronze_path)
            continue

        if year >= FIRST_CSV_YEAR:
            csv_df = _load_csv_year(raw, dataset, year, dictionary_root=dictionary_root)
            if csv_df is not None:
                csv_df.write_parquet(bronze_path)
                logger.info("  Saved bronze (%d rows): %s", len(csv_df), bronze_path)
            continue

        # Find zip file for this year. Match both the original
        # `{year}_enterprise_pudb.zip` and any later FHFA reissue such as
        # `{year}_enterprise_pudb_corrected.zip` (published 2026-04 for 2020).
        # When both exist, prefer the one whose name contains "corrected".
        zip_pattern = f"{year}_enterprise_pudb*.zip"
        zip_candidates = [p for p in raw.glob(f"**/{zip_pattern}") if "_archive" not in p.parts]

        if not zip_candidates:
            logger.warning("No zip file found for year %d (pattern: %s)", year, zip_pattern)
            continue

        zip_candidates.sort(key=lambda p: ("corrected" not in p.name.lower(), p.name))
        zip_file = zip_candidates[0]
        logger.info("Processing %s", zip_file.name)

        # Search for data files inside zip
        file_pattern = f"*_{family}{year}{letter}_{suffix}.txt"
        year_dfs = []

        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                # Get all txt files matching pattern
                matching_files = [
                    name
                    for name in zf.namelist()
                    if fnmatch.fnmatch(name.split("/")[-1], file_pattern)
                ]

                if not matching_files:
                    logger.warning("  No data files found in zip (pattern: %s)", file_pattern)
                    continue

                logger.info("  Found %d file(s) in zip", len(matching_files))

                # Load each file from zip
                for file_name in matching_files:
                    logger.info("    Loading %s", file_name.split("/")[-1])
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
        combined_df = pl.concat(year_dfs, how="diagonal_relaxed")
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
HUD_ERA_DATASETS: list[str] = ["mf_c", "mf_property_b", "mf_unit_b"]
HUD_ERA_MIN_YEAR: int = 1993
HUD_ERA_MAX_YEAR: int = 2007

# Original archive member suffix (upper-cased) for each dataset.
_HUD_ERA_FILE_SUFFIX: dict[str, str] = {
    "mf_c": "C.LOA",
    "mf_property_b": "B.LOA",
    "mf_unit_b": "B.UNI",
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

    if dataset == "mf_unit_b":
        # Unit-class file: a single 6-field layout across all years 1993-2007.
        return [
            "Enterprise Flag",
            "Record Number",
            "Unit Type XX-Number of Bedrooms",
            "Unit Type XX-Number of Units",
            "Unit Type XX-Affordability Level",
            "Unit Type XX-Tenant Income Indicator",
        ]

    if dataset == "mf_c":
        head = [
            "Enterprise Flag",
            "Record Number",
            "US Postal State Code",
            "Metropolitan Statistical Area (MSA) Code",
            f"County - {v} Census",
            f"Census Tract - {v} Census",
        ]
        tail = [
            f"{v} Census Tract - Percent Minority",
            f"{v} Census Tract - Median Income",
            f"{v} Local Area Median Income",
            "Tract Income Ratio",
            f"Area Median Family Income ({year})",
            "Acquisition UPB Range",
        ]
        if year <= 1995:
            # 16 fields: three undocumented middle fields, no targeting flag.
            return (
                head
                + [
                    "HUD Provisional Census Field 1",
                    "HUD Provisional Census Field 2",
                    "HUD Provisional Census Field 3",
                ]
                + tail
                + ["Type of Seller Institution"]
            )
        # 14 fields (1996-2007); the targeting flag was renamed in 2004.
        targeted = (
            "Underserved Areas Indicator" if year >= 2004 else "Geographically Targeted Indicator"
        )
        return head + tail + ["Type of Seller Institution", targeted]

    if dataset == "mf_property_b":
        if year <= 1995:
            # 10 fields. The first eight match the 1996-2003 layout; in place of
            # the targeting flag this early era carries two trailing dollar
            # fields that are zero for most records (almost certainly
            # goal-qualifying dollar amounts rather than acquisition UPB). They
            # are undocumented, so both are kept under provisional names rather
            # than asserting a mapping into ``upb_acquisition``.
            return [
                "Enterprise Flag",
                "Record Number",
                f"{v} Census Tract - Percent Minority",
                "Tract Income Ratio",
                "Affordability Category",
                "Purpose of Loan",
                "Government Insurance",
                "Total Number of Units",
                "HUD Provisional Property Amount 1",
                "HUD Provisional Property Amount 2",
            ]
        if year <= 2003:
            # 9 fields.
            return [
                "Enterprise Flag",
                "Record Number",
                f"{v} Census Tract - Percent Minority",
                "Tract Income Ratio",
                "Affordability Category",
                "Purpose of Loan",
                "Government Insurance",
                "Total Number of Units",
                "Geographically Targeted Indicator",
            ]
        # 11 fields (2004-2007): adds Date of Mortgage Note + Type of Seller.
        return [
            "Enterprise Flag",
            "Record Number",
            f"{v} Census Tract - Percent Minority",
            "Tract Income Ratio",
            "Affordability Category",
            "Date of Mortgage Note",
            "Purpose of Loan",
            "Type of Seller Institution",
            "Government Insurance",
            "Total Number of Units",
            "Underserved Areas Indicator",
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
    wanted = {f"MF{year}{suffix}", f"MF{year % 100:02d}{suffix}"}
    for zip_path in sorted(hud_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if name.split("/")[-1].upper() in wanted:
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
        names: Ordered column names from `hud_era_layout`.

    Returns:
        Typed DataFrame with one column per field (no ``year`` column yet).
    """
    n = len(names)
    rows: list[list[str]] = []
    for line in raw_bytes.decode("latin-1").replace("\r", "").split("\n"):
        if not line.strip():
            continue
        toks = line.split()
        if len(toks) == n:
            rows.append(toks)

    columns = {name: [r[i] for r in rows] for i, name in enumerate(names)}
    df = pl.DataFrame(columns, schema={name: pl.Utf8 for name in names})

    cast_exprs = []
    for name in names:
        has_decimal = bool(df[name].str.contains(".", literal=True).any())
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
        datasets: Subset of `HUD_ERA_DATASETS` to process.
        years: Years to process (clamped to 1993-2007).
        raw_dir: Raw root. Defaults to `FHFAConfig.FHFA_RAW_DIR` (``hud_era`` subdir used).
        output_dir: Bronze root. Defaults to `FHFAConfig.FHFA_BRONZE_DIR`.
        overwrite: Whether to rebuild existing bronze files. Default False.

    Returns:
        Number of bronze files written.
    """
    raw = raw_dir or FHFAConfig.FHFA_RAW_DIR
    output = output_dir or FHFAConfig.FHFA_BRONZE_DIR
    hud_dir = raw / "hud_era"

    written = 0
    for dataset in datasets:
        if dataset not in HUD_ERA_DATASETS:
            raise ValueError(f"Unknown HUD-era dataset: {dataset}. Valid: {HUD_ERA_DATASETS}")
        bronze_dir = output / dataset
        bronze_dir.mkdir(parents=True, exist_ok=True)

        for year in years:
            if not (HUD_ERA_MIN_YEAR <= year <= HUD_ERA_MAX_YEAR):
                continue
            bronze_path = bronze_dir / f"{dataset}_{year}.parquet"
            if not should_process_file(bronze_path, overwrite):
                logger.info("Bronze already exists for %s %d: %s", dataset, year, bronze_path)
                continue

            zip_path, member = _find_hud_era_member(hud_dir, dataset, year)
            if zip_path is None or member is None:
                logger.warning("No HUD-era file found for %s %d in %s", dataset, year, hud_dir)
                continue

            names = hud_era_layout(dataset, year)
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    raw_bytes = zf.read(member)
                df = _parse_hud_era_delimited(raw_bytes, names)
                df = df.with_columns(pl.lit(year).cast(pl.Int32).alias("year"))
                df.write_parquet(bronze_path)
                written += 1
                logger.info(
                    "Saved HUD-era bronze %s %d (%d rows): %s", dataset, year, len(df), bronze_path
                )
            except Exception as e:
                logger.error("Failed HUD-era bronze for %s %d: %s", dataset, year, e)

    return written
