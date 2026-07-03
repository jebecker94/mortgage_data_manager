"""Data cleaning and preparation functions for FHLB matching.

Uses lazy evaluation with Polars LazyFrames for memory efficiency.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import pandas as pd

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

from . import config
from .utils import create_census_tract_from_fips

logger = get_logger(__name__)


def get_fhlb_members_pre2018(
    match_folder: Path, overwrite: bool = False, crosswalk_folder: Path | None = None
) -> None:
    """Extract FHLB members from HMDA lender panel files (pre-2018).

    Args:
        match_folder: Output directory for intermediate data
        overwrite: If True, overwrite existing file
        crosswalk_folder: Directory for crosswalk outputs. Defaults to config.CROSSWALK_OUTPUT_DIR.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    output_file = crosswalk_folder / "lender_fhlb_crosswalk_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"FHLB member crosswalk already exists: {output_file}")
        return

    logger.info("Extracting FHLB members from Avery file (pre-2018)")

    avery_path = config.AVERY_PRE2018_FILE_PATH
    if not avery_path.exists():
        raise FileNotFoundError(f"Pre-2018 Avery file not found: {avery_path}")

    import pandas as pd

    usecols = ["YEAR", "HMPRID", "CODE", "FHLB", "FHLBID"]

    pdf = pd.read_excel(avery_path, usecols=lambda c: c.upper() in usecols)
    pdf.columns = [c.upper() for c in pdf.columns]

    # Filter for FHLB members
    pdf = pdf[pdf["FHLB"].notna()]

    pdf = pdf.rename(
        columns={"HMPRID": "hmprid", "CODE": "code", "YEAR": "Year", "FHLBID": "FHFBID"}
    )

    if "FHFBID" not in pdf.columns and "FHLB" in pdf.columns:
        pdf["FHFBID"] = pdf["FHLB"]

    df = pl.from_pandas(pdf)

    df = df.with_columns(
        [
            pl.col("Year").cast(pl.Int32),
            pl.col("hmprid").cast(pl.Utf8),
            pl.col("code").cast(pl.Int32),
            pl.col("FHFBID").fill_null(0).cast(pl.Float64).cast(pl.Int64),
        ]
    )

    df.write_parquet(output_file)
    logger.info(f"Saved FHLB member crosswalk to {output_file}")


_FIPS_TO_ABBR: dict[int, str] = {
    1: "AL",
    2: "AK",
    4: "AZ",
    5: "AR",
    6: "CA",
    8: "CO",
    9: "CT",
    10: "DE",
    11: "DC",
    12: "FL",
    13: "GA",
    15: "HI",
    16: "ID",
    17: "IL",
    18: "IN",
    19: "IA",
    20: "KS",
    21: "KY",
    22: "LA",
    23: "ME",
    24: "MD",
    25: "MA",
    26: "MI",
    27: "MN",
    28: "MS",
    29: "MO",
    30: "MT",
    31: "NE",
    32: "NV",
    33: "NH",
    34: "NJ",
    35: "NM",
    36: "NY",
    37: "NC",
    38: "ND",
    39: "OH",
    40: "OK",
    41: "OR",
    42: "PA",
    44: "RI",
    45: "SC",
    46: "SD",
    47: "TN",
    48: "TX",
    49: "UT",
    50: "VT",
    51: "VA",
    53: "WA",
    54: "WV",
    55: "WI",
    56: "WY",
    60: "AS",
    66: "GU",
    69: "MP",
    72: "PR",
    78: "VI",
}


def _fips_to_abbr(fips_code: float | int | None) -> str | None:
    """Convert a numeric FIPS state code to a two-letter abbreviation."""
    if fips_code is None or (isinstance(fips_code, float) and fips_code != fips_code):
        return None
    return _FIPS_TO_ABBR.get(int(fips_code))


def _load_ncua_fhlb_members() -> dict[int, set[int]] | None:
    """Load FHLB membership flags from NCUA Call Report data.

    Reads ACCT_896 ("Member of the Federal Home Loan Bank") from the FS220B
    file type in NCUA silver data. Returns a mapping from CU_NUMBER to the
    set of years in which FHLB membership = 1.

    Returns:
        Dict mapping CU_NUMBER to set of years with FHLB membership,
        or None if NCUA data is unavailable.
    """
    from mortgage_data_manager.core.config import MortgageDataConfig

    ncua_silver = MortgageDataConfig.get_medallion_dir("ncua", "silver")
    fs220b_dir = ncua_silver / "FILE_TYPE=fs220b"

    if not fs220b_dir.exists():
        return None

    try:
        df = (
            pl.scan_parquet(fs220b_dir / "**/*.parquet", hive_partitioning=True)
            .select("CU_NUMBER", "FILE_YEAR", "ACCT_896")
            .filter(pl.col("ACCT_896") == 1.0)
            .collect()
        )
    except Exception:
        return None

    if len(df) == 0:
        return None

    # Build CU_NUMBER -> {years} mapping from hive-partitioned FILE_YEAR
    result: dict[int, set[int]] = {}
    for cu, year in zip(
        df["CU_NUMBER"].cast(pl.Int64).to_list(),
        df["FILE_YEAR"].cast(pl.Int32).to_list(),
    ):
        result.setdefault(cu, set()).add(year)
    return result


def _supplement_fhlb_members_post2018(
    avery_pdf: pd.DataFrame,
    avery_members: pl.DataFrame,
    max_year: int,
    *,
    use_ncua: bool = True,
) -> pl.DataFrame | None:
    """Supplement Avery-based FHLB membership with external data sources.

    Identifies FHLB members missing the FHLB flag in the Avery panel using
    three strategies:
      1. NCUA Call Report ACCT_896 — authoritative FHLB flag for credit unions
      2. RSSD match against FHLB quarterly member files — for banks
      3. Name-prefix + state matching against FHLB member files — fallback

    Args:
        avery_pdf: Full Avery panel DataFrame (all institutions, not just FHLB)
        avery_members: Polars DataFrame of already-identified FHLB members
            with columns (LEI, FHFBID, Year)
        max_year: Maximum year to process
        use_ncua: Whether to use NCUA data for CU FHLB identification.
            Defaults to True; gracefully skips if NCUA data is unavailable.

    Returns:
        Polars DataFrame with supplemental (LEI, FHFBID, Year) rows,
        or None if no supplemental members are found.
    """
    import pandas as pd

    fhlb_conf = config.get_fhlb_manager_config()
    members_dir = fhlb_conf.FHLBConfig.FHLB_BRONZE_DIR / "members"

    if not members_dir.exists():
        logger.warning(f"FHLB members directory not found: {members_dir}")
        return None

    already_lei_years = set(
        zip(
            avery_members["LEI"].to_list(),
            avery_members["Year"].to_list(),
        )
    )

    # Build Avery lookup tables (all institutions, not just FHLB-flagged)
    avery_all = avery_pdf.copy()
    avery_all["RSSD"] = pd.to_numeric(avery_all["RSSD"], errors="coerce")
    avery_all["YEAR"] = pd.to_numeric(avery_all["YEAR"], errors="coerce")
    avery_all["NCUA"] = pd.to_numeric(avery_all.get("NCUA"), errors="coerce")

    # Normalize Avery name: uppercase, stripped
    if "NAME" in avery_all.columns:
        avery_all["_name_norm"] = avery_all["NAME"].fillna("").str.upper().str.strip()

    supplement_rows: list[dict] = []

    # Strategy 1: NCUA Call Report FHLB membership (credit unions)
    # ACCT_896 = "Member of the Federal Home Loan Bank" in FS220B
    ncua_fhlb: dict[int, set[int]] | None = None
    if use_ncua:
        ncua_fhlb = _load_ncua_fhlb_members()
        if ncua_fhlb is None:
            logger.warning(
                "NCUA silver data not available — skipping NCUA-based FHLB "
                "membership supplement. Run 'mortgage-data ncua pipeline' to "
                "download and process NCUA Call Report data."
            )

    if ncua_fhlb is not None:
        n_ncua = 0
        for year in range(2018, max_year + 1):
            avery_year = avery_all[
                (avery_all["YEAR"] == year) & avery_all["FHLB"].isna() & avery_all["NCUA"].notna()
            ]
            for _, row in avery_year.iterrows():
                lei = row.get("LEI")
                if pd.isna(lei) or (lei, year) in already_lei_years:
                    continue
                ncua_id = int(row["NCUA"])
                if ncua_id == 0:
                    continue
                member_years = ncua_fhlb.get(ncua_id)
                if member_years is None or year not in member_years:
                    continue
                # Need FHLB district — look up from FHLB member file
                # (NCUA doesn't report district, only binary membership)
                supplement_rows.append(
                    {
                        "LEI": lei,
                        "FHFBID": 0,  # placeholder — resolved below
                        "Year": year,
                        "_ncua_id": ncua_id,
                    }
                )
                already_lei_years.add((lei, year))
                n_ncua += 1

        # Resolve FHLB district IDs for NCUA-matched rows
        # Match CU_NUMBER against FHLB member file "CREDIT UNION CHARTER NUMBER"
        # These use different ID systems, so fall back to name matching
        # within the FHLB file for district assignment.
        if n_ncua > 0:
            _resolve_ncua_districts(supplement_rows, members_dir, avery_all, max_year)
        logger.info(f"NCUA ACCT_896 supplement: {n_ncua} LEI-year rows")

    # Load FHLB member files for strategies 2 and 3
    for year in range(2018, max_year + 1):
        fhlb_file = None
        for q in [4, 3, 2, 1]:
            candidate = members_dir / f"fhlb_members_{year}_q{q}.parquet"
            if candidate.exists():
                fhlb_file = candidate
                break
        if fhlb_file is None:
            continue

        try:
            fhlb_members = pl.read_parquet(fhlb_file)
        except Exception:
            logger.warning(f"Failed to read {fhlb_file}")
            continue

        if "FHFB ID" not in fhlb_members.columns:
            continue

        avery_year = avery_all[(avery_all["YEAR"] == year) & avery_all["FHLB"].isna()]
        if len(avery_year) == 0:
            continue

        # Strategy 2: RSSD match (banks)
        fhlb_with_rssd = fhlb_members.filter(
            pl.col("FEDERAL RESERVE ID").is_not_null() & (pl.col("FEDERAL RESERVE ID") != 0)
        )
        if len(fhlb_with_rssd) > 0:
            rssd_to_district = dict(
                zip(
                    fhlb_with_rssd["FEDERAL RESERVE ID"].cast(pl.Int64).to_list(),
                    fhlb_with_rssd["FHFB ID"].cast(pl.Int64).to_list(),
                )
            )
            for _, row in avery_year.iterrows():
                rssd = row.get("RSSD")
                lei = row.get("LEI")
                if pd.isna(rssd) or pd.isna(lei) or rssd == 0:
                    continue
                rssd_int = int(rssd)
                if rssd_int in rssd_to_district and (lei, year) not in already_lei_years:
                    supplement_rows.append(
                        {
                            "LEI": lei,
                            "FHFBID": rssd_to_district[rssd_int],
                            "Year": year,
                        }
                    )
                    already_lei_years.add((lei, year))

        # Strategy 3: Prefix-of name matching (fallback)
        # Avery truncates names (~30 chars), so "MAINSTREET FEDERAL CREDIT UNIO"
        # must match FHLB's "Mainstreet Federal Credit Union". Also, Avery may
        # use abbreviations like "ROGUE" for "Rogue Credit Union".
        # Two passes: (a) prefix match with min 8 chars (safe without state),
        # (b) shorter names (3+ chars) validated by state to avoid false positives
        # from generic names like "BLUE" or "VALLEY".
        fhlb_name_state_district: list[tuple[str, str, int]] = []
        for row in fhlb_members.iter_rows(named=True):
            name = (row.get("MEMBER NAME") or "").upper().strip()
            state = (row.get("STATE") or "").upper().strip()
            if name:
                fhlb_name_state_district.append((name, state, int(row["FHFB ID"])))

        for _, row in avery_year.iterrows():
            lei = row.get("LEI")
            if pd.isna(lei) or (lei, year) in already_lei_years:
                continue
            avery_name = row.get("_name_norm", "")
            if len(avery_name) < 3:
                continue
            avery_state_abbr = _fips_to_abbr(row.get("STATE"))
            need_state = len(avery_name) < 8
            candidates = []
            for fhlb_name, fhlb_state, district in fhlb_name_state_district:
                if not (fhlb_name.startswith(avery_name) or avery_name.startswith(fhlb_name)):
                    continue
                if need_state and (not avery_state_abbr or avery_state_abbr != fhlb_state):
                    continue
                candidates.append(district)
            # Require exactly one match for short names to avoid ambiguity
            if len(candidates) == 1 or (len(candidates) > 0 and not need_state):
                supplement_rows.append(
                    {
                        "LEI": lei,
                        "FHFBID": candidates[0],
                        "Year": year,
                    }
                )
                already_lei_years.add((lei, year))

    # Clean up internal keys; keep FHFBID=0 rows (NCUA-confirmed members
    # where district couldn't be resolved — the matching filter allows
    # FHFBID=0 or null to pass through)
    for row in supplement_rows:
        row.pop("_ncua_id", None)

    if not supplement_rows:
        return None

    result = pl.DataFrame(supplement_rows).with_columns(
        [
            pl.col("Year").cast(pl.Int32),
            pl.col("FHFBID").cast(pl.Int64),
        ]
    )

    n_unique_leis = result["LEI"].n_unique()
    logger.info(
        f"FHLB member supplement total: {n_unique_leis} unique LEIs, {len(result)} LEI-year rows"
    )
    return result


def _resolve_ncua_districts(
    supplement_rows: list[dict],
    members_dir: Path,
    avery_all: pd.DataFrame,
    max_year: int,
) -> None:
    """Resolve FHLB district IDs for NCUA-matched supplement rows.

    NCUA ACCT_896 confirms FHLB membership but doesn't report the district.
    This function matches the Avery name against the FHLB member file to
    find the correct district ID. Rows that can't be resolved keep FHFBID=0
    and are filtered out by the caller.
    """
    # Build a per-year name→district lookup from FHLB member files
    # and resolve each NCUA-matched row
    ncua_rows = [r for r in supplement_rows if "_ncua_id" in r]
    if not ncua_rows:
        return

    # Group NCUA rows by year for batch processing
    by_year: dict[int, list[dict]] = {}
    for row in ncua_rows:
        by_year.setdefault(row["Year"], []).append(row)

    for year, rows in by_year.items():
        fhlb_file = None
        for q in [4, 3, 2, 1]:
            candidate = members_dir / f"fhlb_members_{year}_q{q}.parquet"
            if candidate.exists():
                fhlb_file = candidate
                break
        if fhlb_file is None:
            continue

        try:
            fhlb_members = pl.read_parquet(fhlb_file)
        except Exception:
            continue

        # Build name+state → district lookup
        fhlb_name_state_district: list[tuple[str, str, int]] = []
        for frow in fhlb_members.iter_rows(named=True):
            name = (frow.get("MEMBER NAME") or "").upper().strip()
            state = (frow.get("STATE") or "").upper().strip()
            if name:
                fhlb_name_state_district.append((name, state, int(frow["FHFB ID"])))

        for row in rows:
            lei = row["LEI"]
            # Look up Avery name and state for this LEI+year
            avery_match = avery_all[(avery_all["LEI"] == lei) & (avery_all["YEAR"] == year)]
            if len(avery_match) == 0:
                continue
            avery_name = avery_match.iloc[0].get("_name_norm", "")
            avery_state = _fips_to_abbr(avery_match.iloc[0].get("STATE"))

            # Try prefix matching (same logic as strategy 3)
            candidates = []
            for fhlb_name, fhlb_state, district in fhlb_name_state_district:
                if not (fhlb_name.startswith(avery_name) or avery_name.startswith(fhlb_name)):
                    continue
                if avery_state and avery_state == fhlb_state:
                    candidates.append(district)
            if len(candidates) == 1:
                row["FHFBID"] = candidates[0]
            elif len(candidates) > 1:
                # Multiple matches in-state — use first (ambiguous but
                # district is secondary; the key value is LEI identification)
                row["FHFBID"] = candidates[0]


def get_fhlb_members_post2018(
    match_folder: Path,
    max_year: int,
    overwrite: bool = False,
    crosswalk_folder: Path | None = None,
    use_ncua: bool = True,
) -> None:
    """Extract FHLB members from HMDA lender panel files (post-2018).

    Args:
        match_folder: Output directory for intermediate data.
        max_year: Maximum year to include.
        overwrite: If True, overwrite existing file.
        crosswalk_folder: Directory for crosswalk outputs.
            Defaults to config.CROSSWALK_OUTPUT_DIR.
        use_ncua: Whether to use NCUA Call Report data (ACCT_896)
            for credit union FHLB membership identification.
            Defaults to True; gracefully skips if data is unavailable.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    output_file = crosswalk_folder / "lender_fhlb_crosswalk_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"FHLB member crosswalk already exists: {output_file}")
        return

    logger.info("Extracting FHLB members from Avery file (post-2018)")

    avery_path = config.AVERY_FILE_PATH
    if not avery_path.exists():
        raise FileNotFoundError(f"Avery file not found: {avery_path}")

    import pandas as pd

    xl = pd.ExcelFile(avery_path)
    logger.debug(f"Avery file sheets: {xl.sheet_names}")

    avery_full = pd.read_excel(avery_path, sheet_name="beta7")
    logger.info(f"Loaded Avery file: {avery_full.shape[0]:,} rows, {avery_full.shape[1]} columns")

    # Statistics
    total_by_year = avery_full.groupby("YEAR").size()
    fhlb_members = avery_full[avery_full["FHLB"].notna()]
    fhlb_by_year = fhlb_members.groupby("YEAR").size()

    for year in sorted(total_by_year.index):
        total = total_by_year.get(year, 0)
        fhlb = fhlb_by_year.get(year, 0)
        pct = fhlb / total if total > 0 else 0
        logger.info(f"Year {year}: {fhlb:,} FHLB members / {total:,} total LEIs ({pct:.1%})")

    pdf = fhlb_members.rename(columns={"LEI": "LEI", "FHLB": "FHFBID", "YEAR": "Year"})
    pdf = pdf[["LEI", "FHFBID", "Year"]]

    df = pl.from_pandas(pdf)

    df = df.with_columns(
        [
            pl.col("Year").cast(pl.Int32),
            pl.col("FHFBID").cast(pl.Float64).cast(pl.Int64),
        ]
    ).filter(pl.col("Year") <= max_year)

    # Supplement with FHLB quarterly member files to catch institutions
    # missing the FHLB flag in the Avery panel (240 known gaps as of 2026-03).
    # See investigations/reports/investigation_fhlb_ama_open_matching_2026-03-07.md
    df_supplement = _supplement_fhlb_members_post2018(
        avery_full,
        df,
        max_year,
        use_ncua=use_ncua,
    )
    if df_supplement is not None and len(df_supplement) > 0:
        df = pl.concat([df, df_supplement], how="diagonal")
        logger.info(f"Added {len(df_supplement):,} supplemental FHLB member-year rows")

    df.write_parquet(output_file)
    logger.info(f"Saved FHLB member crosswalk to {output_file}")


def clean_fhlb_data_pre2018(match_folder: Path, max_year: int, overwrite: bool = False) -> None:
    """Clean FHLB acquisition data for pre-2018 matching.

    Args:
        match_folder: Output directory
        max_year: Maximum year to process
        overwrite: If True, overwrite existing file
    """
    output_file = match_folder / "fhlb_acquisitions_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"FHLB cleaned data already exists: {output_file}")
        return

    logger.info("Cleaning FHLB data (pre-2018)")

    fhlb_conf = config.get_fhlb_manager_config()
    fhlb_dir = fhlb_conf.FHLBConfig.FHLB_SILVER_DIR / "ama"

    keep_cols = [
        "data_year",
        "LoanCharacteristicsID",
        "NoteDate",
        "MortgageType",
        "NoteAmount",
        "FIPSStateNumericCode",
        "FIPSCountyCode",
        "CensusTractIdentifier",
        "PropertyUsageType",
        "LoanPurposeType",
        "TotalMonthlyIncomeAmount",
        "Borrower1SexType",
        "Borrower2SexType",
        "FHFBID",
    ]

    dfs = []
    for year in range(2009, max_year + 1):
        ffile = fhlb_dir / f"fhlb_ama_{year}.parquet"
        if not ffile.exists():
            logger.warning(f"FHLB data not found for {year}: {ffile}")
            continue

        file_cols = pl.scan_parquet(ffile).collect_schema().names()
        valid_cols = [c for c in keep_cols if c in file_cols]

        df_year = pl.read_parquet(ffile, columns=valid_cols)

        for c in keep_cols:
            if c not in df_year.columns:
                df_year = df_year.with_columns(pl.lit(None).cast(pl.Int64).alias(c))

        dfs.append(df_year)

    if not dfs:
        raise FileNotFoundError(f"No FHLB data found in {fhlb_dir}")

    df = pl.concat(dfs, how="diagonal").filter(pl.col("data_year") <= 2017)

    # AMA CensusTractIdentifier is a dotted-decimal float (e.g. 5203.01 = tract
    # "5203.01"); convert to the 6-digit integer tract code (520301) before
    # building the GEOID. Otherwise the .cast(UInt64) in create_census_tract_string
    # truncates the decimal (5203.01 -> 5203) and misplaces the tract digits, so
    # the resulting census_tract matches nothing in HMDA.
    df = df.with_columns(
        (pl.col("CensusTractIdentifier") * 100).round(0).cast(pl.Int64).alias("CensusTractIdentifier")
    )

    df = create_census_tract_from_fips(
        df,
        state_col="FIPSStateNumericCode",
        county_col="FIPSCountyCode",
        tract_col="CensusTractIdentifier",
        output_col="census_tract",
    )

    df = df.rename(
        {
            "NoteDate": "Note Date",
            "PropertyUsageType": "Property Usage Type",
            "LoanPurposeType": "Loan Purpose Type",
            "TotalMonthlyIncomeAmount": "Total Yearly Income Amount",
            "Borrower1SexType": "Borrower 1 Gender Type",
            "Borrower2SexType": "Borrower 2 Gender Type",
        }
    )

    df = df.drop(["FIPSStateNumericCode", "FIPSCountyCode", "CensusTractIdentifier"])

    df = df.with_columns([(pl.col("MortgageType") + 1).alias("Loan Type")]).drop("MortgageType")

    df = df.with_columns([(pl.col("NoteAmount") / 1000).round(0).alias("Round Loan Amount")])

    # LoanCharacteristicsID is unique - no deduplication needed
    df.write_parquet(output_file)
    logger.info(f"Saved cleaned FHLB data to {output_file}")


def clean_fhlb_data_post2018(match_folder: Path, max_year: int, overwrite: bool = False) -> None:
    """Clean FHLB acquisition data for post-2018 matching.

    Args:
        match_folder: Output directory
        max_year: Maximum year to process
        overwrite: If True, overwrite existing file
    """
    output_file = match_folder / "fhlb_acquisitions_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"FHLB cleaned data already exists: {output_file}")
        return

    logger.info("Cleaning FHLB data (post-2018)")

    fhlb_conf = config.get_fhlb_manager_config()
    fhlb_dir = fhlb_conf.FHLBConfig.FHLB_SILVER_DIR / "ama"

    columns = [
        "data_year",
        "MortgageType",
        "NoteAmount",
        "NoteDate",
        "FIPSStateNumericCode",
        "FIPSCountyCode",
        "CensusTractIdentifier",
        "LoanPurposeType",
        "TotalMonthlyIncomeAmount",
        "LoanAmortizationMaxTermMonths",
        "NoteRatePercent",
        "LoanAcquisitionActualUPBAmt",
        "Borrower1SexType",
        "Borrower2SexType",
        "LTVRatioPercent",
        "TotalDebtExpenseRatioPercent",
        "PropertyUnitCount",
        "PropertyUsageType",
        "LoanCharacteristicsID",
    ]

    dfs = []
    for year in range(2018, max_year + 1):
        ffile = fhlb_dir / f"fhlb_ama_{year}.parquet"
        if ffile.exists():
            dfs.append(pl.read_parquet(ffile, columns=columns))
        else:
            logger.warning(f"FHLB data not found for {year}: {ffile}")

    if not dfs:
        raise FileNotFoundError(f"No FHLB data found in {fhlb_dir}")

    df = pl.concat(dfs, how="diagonal").filter(pl.col("data_year") >= 2018)

    # See note in the pre-2018 path: AMA CensusTractIdentifier is a dotted-decimal
    # float; scale to the 6-digit integer tract code before building the GEOID.
    df = df.with_columns(
        (pl.col("CensusTractIdentifier") * 100).round(0).cast(pl.Int64).alias("CensusTractIdentifier")
    )

    df = create_census_tract_from_fips(
        df,
        state_col="FIPSStateNumericCode",
        county_col="FIPSCountyCode",
        tract_col="CensusTractIdentifier",
    )

    drop_cols = [
        "FIPSStateNumericCode",
        "FIPSCountyCode",
        "CoreBasedStatisticalAreaCode",
        "CensusTractIdentifier",
        "CensusTractMinorityRatioPercent",
        "CensusTractMedFamIncomeAmount",
        "LocalAreaMedianIncomeAmount",
        "HUDMedianIncomeAmount",
    ]
    df = df.drop([c for c in drop_cols if c in df.schema])

    df = df.with_columns(
        [
            (pl.col("MortgageType") + 1).alias("Loan Type"),
            pl.lit(0).cast(pl.Int64).alias("i_Duplicate"),
        ]
    ).drop("MortgageType")

    # Create duplicates for loans at $10k round amounts
    df_dup = df.filter((pl.col("NoteAmount") % 10000) == 0).with_columns(
        [pl.lit(1).cast(pl.Int64).alias("i_Duplicate")]
    )

    df_all = pl.concat([df, df_dup], how="diagonal")

    df_all = df_all.with_columns(
        [
            (
                (pl.col("NoteAmount") - 10000 * pl.col("i_Duplicate")).floordiv(10000) * 10000
                + 5000
            ).alias("Round Loan Amount")
        ]
    )

    df_all.write_parquet(output_file)
    logger.info(f"Saved cleaned FHLB data to {output_file}")


def clean_hmda_data_pre2018(
    match_folder: Path, overwrite: bool = False, crosswalk_folder: Path | None = None
) -> None:
    """Clean HMDA data for FHLB matching (pre-2018).

    Args:
        match_folder: Output directory
        overwrite: If True, overwrite existing file
        crosswalk_folder: Directory for crosswalk files. Defaults to config.CROSSWALK_OUTPUT_DIR.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    output_file = match_folder / "hmda_fhlb_members_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"HMDA cleaned data already exists: {output_file}")
        return

    logger.info("Cleaning HMDA data for FHLB members (pre-2018)")

    hmda_conf = config.get_hmda_manager_config()
    cw_file = crosswalk_folder / "lender_fhlb_crosswalk_pre2018.parquet"

    if not cw_file.exists():
        raise FileNotFoundError(f"Crosswalk file not found: {cw_file}")

    cw = pl.scan_parquet(cw_file)

    hmda_dir = hmda_conf.HMDAConfig.get_dataset_dir("silver", "loans", "period_2007_2017")

    hmda_cols = [
        "activity_year",
        "respondent_id",
        "agency_code",
        "sequence_number",
        "loan_amount",
        "loan_type",
        "occupancy_type",
        "loan_purpose",
        "action_taken",
        "income",
        "applicant_sex",
        "co_applicant_sex",
        "census_tract",
        "purchaser_type",
    ]

    dfs = []
    for year in range(2009, 2018):
        year_path = hmda_dir / f"activity_year={year}"
        if not year_path.exists():
            logger.warning(f"HMDA silver data not found for {year}: {year_path}")
            continue

        # Use only file_type=d (nationwide) for consistency across all pre-2018 years.
        # 2017 has multiple file types (a, c, d); other years only have d.
        ft_d = year_path / "file_type=d"
        if ft_d.exists():
            files = list(ft_d.glob("*.parquet"))
        else:
            files = list(year_path.glob("**/*.parquet"))
        if not files:
            continue

        # Process each file separately to handle schema differences within years
        year_dfs = []
        for file in files:
            # Check if file has required columns before processing
            file_schema = pl.scan_parquet(file).collect_schema().names()
            required_cols = ["respondent_id", "agency_code", "action_taken"]

            if not all(col in file_schema for col in required_cols):
                logger.warning(f"Skipping {file} - missing required columns")
                continue

            lf = (
                pl.scan_parquet(file, allow_missing_columns=True)
                .select(hmda_cols)
                .filter(pl.col("action_taken") == 1)
            )
            year_dfs.append(lf.collect())

        if year_dfs:
            dfs.append(pl.concat(year_dfs, how="diagonal"))

    if not dfs:
        logger.warning("No Pre-2018 HMDA files found")
        return

    df = pl.concat(dfs, how="diagonal").with_row_index("HMDAIndex")

    # Join with FHLB members
    df = df.join(
        cw.collect(),
        left_on=["respondent_id", "agency_code", "activity_year"],
        right_on=["hmprid", "code", "Year"],
        how="inner",
    )

    df.write_parquet(output_file)
    logger.info(f"Saved HMDA FHLB member data to {output_file}")


def clean_hmda_data_post2018(
    match_folder: Path,
    max_year: int,
    overwrite: bool = False,
    crosswalk_folder: Path | None = None,
) -> None:
    """Clean HMDA data for FHLB matching (post-2018).

    Args:
        match_folder: Output directory
        max_year: Maximum year to process
        overwrite: If True, overwrite existing file
        crosswalk_folder: Directory for crosswalk files. Defaults to config.CROSSWALK_OUTPUT_DIR.
    """
    if crosswalk_folder is None:
        crosswalk_folder = config.CROSSWALK_OUTPUT_DIR
    output_file = match_folder / "hmda_fhlb_members_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info(f"HMDA cleaned data already exists: {output_file}")
        return

    logger.info("Cleaning HMDA data for FHLB members (post-2018)")

    cw_file = crosswalk_folder / "lender_fhlb_crosswalk_post2018.parquet"
    if not cw_file.exists():
        raise FileNotFoundError(f"Crosswalk file not found: {cw_file}")

    cw = pl.read_parquet(cw_file)

    hmda_conf = config.get_hmda_manager_config()
    hmda_dir = hmda_conf.HMDAConfig.get_dataset_dir("silver", "loans", "post2018")

    hmda_cols = [
        "HMDAIndex",  # native string ID — preserved for stable crosswalk keys
        "activity_year",
        "lei",
        "census_tract",
        "loan_amount",
        "loan_type",
        "total_units",
        "occupancy_type",
        "loan_purpose",
        "income",
        "loan_term",
        "interest_rate",
        "applicant_sex",
        "co_applicant_sex",
        "combined_loan_to_value_ratio",
        "debt_to_income_ratio",
        "purchaser_type",
    ]

    total_loans_counts = {}
    dfs = []

    for year in range(2018, max_year + 1):
        year_path = hmda_dir / f"activity_year={year}"
        if not year_path.exists():
            logger.warning(f"HMDA silver data not found for {year}: {year_path}")
            continue

        # Find file_type partitions if they exist
        file_type_dirs = sorted(
            [d for d in year_path.iterdir() if d.is_dir() and "file_type" in d.name]
        )

        target_path = file_type_dirs[0] if file_type_dirs else year_path

        lf = (
            pl.scan_parquet(target_path / "*.parquet")
            .filter((pl.col("action_taken") == 1) & (pl.col("purchaser_type") != 3))
            .select(hmda_cols)
        )

        df_year = lf.collect()
        total_loans_counts[year] = len(df_year)

        # Filter by FHLB membership
        df_year = df_year.join(
            cw, left_on=["lei", "activity_year"], right_on=["LEI", "Year"], how="inner"
        )
        dfs.append(df_year)

    if not dfs:
        logger.warning("No Post-2018 HMDA files found")
        return

    result = pl.concat(dfs, how="diagonal")

    # Log statistics
    fhlb_counts = result.group_by("activity_year").len().sort("activity_year")
    for row in fhlb_counts.iter_rows(named=True):
        year = row["activity_year"]
        fhlb_loans = row["len"]
        total = total_loans_counts.get(year, 0)
        pct = fhlb_loans / total if total > 0 else 0
        logger.info(f"Year {year}: {fhlb_loans:,} FHLB member loans / {total:,} total ({pct:.1%})")

    result.write_parquet(output_file)
    logger.info(f"Saved HMDA FHLB member data to {output_file}")
