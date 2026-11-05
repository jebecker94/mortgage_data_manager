"""Input preparation for HMDA-FHLB matching: the FHLB member list and both cleaned sides.

Uses lazy evaluation with Polars LazyFrames for memory efficiency.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.matching.match_hmda_fhlb.config import (
    HMDA_POST2018_COLS,
    HMDAFHLBMatchingConfig,
)
from mortgage_data_manager.matching.match_hmda_fhlb.utils import create_census_tract_from_fips
from mortgage_data_manager.matching.terms import is_bin_edge, round_to_fhfa_bin
from mortgage_data_manager.sources.hmda.load import scan_originations

logger = get_logger(__name__)


def get_fhlb_members_pre2018(
    match_folder: Path, overwrite: bool = False, crosswalk_folder: Path | None = None
) -> None:
    """Extract FHLB members from HMDA lender panel files (pre-2018).

    Args:
        match_folder: Output directory for intermediate data
        overwrite: If True, overwrite existing file
        crosswalk_folder: Directory for crosswalk outputs. Defaults to ``HMDA_FHLB_CROSSWALK_DIR``.
    """
    if crosswalk_folder is None:
        crosswalk_folder = HMDAFHLBMatchingConfig.HMDA_FHLB_CROSSWALK_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    output_file = crosswalk_folder / "hmda_fhlb_lender_crosswalk_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("FHLB member crosswalk already exists: %s", output_file)
        return

    logger.info("Extracting FHLB members from Avery file (pre-2018)")

    avery_path = HMDAFHLBMatchingConfig.AVERY_PRE2018_FILE_PATH
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

    with atomic_output(output_file) as tmp:
        df.write_parquet(tmp)
    logger.info("Saved FHLB member crosswalk to %s", output_file)


# District names in FHFA's member listings -> the Avery file's FHLB code. Seattle merged into
# Des Moines in 2015, so no post-2018 listing prints it.
_DISTRICT_CODES: dict[str, int] = {
    "Boston": 1,
    "New York": 2,
    "Pittsburgh": 3,
    "Atlanta": 4,
    "Cincinnati": 5,
    "Indianapolis": 6,
    "Chicago": 7,
    "Des Moines": 8,
    "Dallas": 9,
    "Topeka": 10,
    "San Francisco": 11,
    "Seattle": 12,
}

# Institution identifiers the member listings print: (Avery column, listing column).
_MEMBER_KEYS: tuple[tuple[str, str], ...] = (
    ("NCUA", "CREDIT UNION CHARTER NUMBER"),
    ("CERT", "FDIC CERTIFICATE NUMBER"),
    ("RSSD", "FEDERAL RESERVE ID"),
)

CHARTER_ALIASES_FILE = "member_charter_aliases.csv"


def _load_member_listings() -> pl.DataFrame:
    """Every FHFA quarterly member listing on disk, one row per member-quarter.

    Returns:
        Columns YEAR, QUARTER, fhfb_id, district (Avery FHLB code) and one Int64 column per
        institution identifier in ``_MEMBER_KEYS`` (null where not printed).

    Raises:
        FileNotFoundError: If no member listing has been built to bronze.
    """
    fhlb_conf = HMDAFHLBMatchingConfig.get_fhlb_config()
    files = sorted(
        (fhlb_conf.FHLBConfig.FHLB_BRONZE_DIR / "members").glob("fhlb_members_*.parquet")
    )
    if not files:
        raise FileNotFoundError(
            "No FHLB member listings found; run 'mortgage-data fhlb pipeline' for members"
        )
    listings = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    return listings.filter(pl.col("FHFB ID").is_not_null()).select(
        pl.col("YEAR").cast(pl.Int32),
        pl.col("QUARTER").cast(pl.Int32),
        pl.col("FHFB ID").cast(pl.Int64).alias("fhfb_id"),
        pl.col("DISTRICT").replace_strict(_DISTRICT_CODES, return_dtype=pl.Int64).alias("district"),
        *[pl.col(col).cast(pl.Int64, strict=False).alias(key) for key, col in _MEMBER_KEYS],
    )


def _load_charter_aliases() -> pl.DataFrame:
    """The hand-maintained NCUA charters FHFA never printed for a member (ncua_charter, fhfb_id)."""
    fhlb_conf = HMDAFHLBMatchingConfig.get_fhlb_config()
    path = fhlb_conf.FHLBConfig.FHLB_SCHEMAS_DIR / CHARTER_ALIASES_FILE
    return pl.read_csv(
        path, schema_overrides={"ncua_charter": pl.Int64, "fhfb_id": pl.Int64}
    ).select("ncua_charter", "fhfb_id")


def _supplement_fhlb_members_post2018(
    avery: pl.DataFrame,
    listings: pl.DataFrame,
    aliases: pl.DataFrame,
) -> pl.DataFrame:
    """Avery LEI-years that lack the FHLB flag although the lender was a year-end member.

    Avery assigns the flag by joining FHFA's member listing on the identifier printed that
    quarter. A credit union that converts charter (federal <-> state) gets a new NCUA number,
    and the listings kept printing the retired one, in some cases for years, so Avery misses
    the member. Here an identifier reaches every FHFA member id it was *ever* printed with
    (2009 onward), plus the charter aliases (numbers FHFA never printed for the member). Of
    those members, the ones on that year's year-end listing are kept, and the LEI-year is
    linked only when exactly one member remains across all its identifiers. Membership
    therefore always comes from the year's FHFA listing; the identifiers only say which
    member the lender is.

    Args:
        avery: Avery rows (YEAR, LEI, NCUA, CERT, RSSD) that lack the FHLB flag.
        listings: FHFA member listings from ``_load_member_listings``.
        aliases: (ncua_charter, fhfb_id) pairs from ``_load_charter_aliases``.

    Returns:
        (LEI, FHFBID, Year) rows, FHFBID being the district code as in the Avery rows.
    """
    history = pl.concat(
        [
            listings.filter(pl.col(key) > 0).select(
                pl.lit(key).alias("key"), pl.col(key).alias("id"), "fhfb_id"
            )
            for key, _ in _MEMBER_KEYS
        ]
        + [
            aliases.select(
                pl.lit("NCUA").alias("key"), pl.col("ncua_charter").alias("id"), "fhfb_id"
            )
        ]
    ).unique()

    # Year-end = the latest listing within the year; a member printed under two districts
    # in it names neither.
    year_end = (
        listings.filter(pl.col("QUARTER") == pl.col("QUARTER").max().over("YEAR"))
        .select("YEAR", "fhfb_id", "district")
        .unique()
        .filter(pl.len().over("YEAR", "fhfb_id") == 1)
    )

    lender_ids = pl.concat(
        [
            avery.filter(pl.col(key) > 0).select(
                pl.col("YEAR").cast(pl.Int32),
                "LEI",
                pl.lit(key).alias("key"),
                pl.col(key).cast(pl.Int64).alias("id"),
            )
            for key, _ in _MEMBER_KEYS
        ]
    )
    candidates = (
        lender_ids.join(history, on=["key", "id"], how="inner")
        .join(year_end, on=["YEAR", "fhfb_id"], how="inner")
        .select("YEAR", "LEI", "fhfb_id", "district")
        .unique()
    )
    ambiguous = candidates.filter(pl.len().over("YEAR", "LEI") > 1).select("YEAR", "LEI")
    if ambiguous.height:
        logger.info(
            "FHLB member supplement: %s LEI-years reach several members; left unlinked",
            ambiguous.n_unique(),
        )
    linked = candidates.filter(pl.len().over("YEAR", "LEI") == 1)

    for row in linked.group_by("YEAR").len().sort("YEAR").iter_rows(named=True):
        logger.info("FHLB member supplement %s: %s LEI-years", row["YEAR"], row["len"])
    return linked.select(
        "LEI",
        pl.col("district").alias("FHFBID"),
        pl.col("YEAR").alias("Year"),
    )


def get_fhlb_members_post2018(
    match_folder: Path,
    max_year: int,
    overwrite: bool = False,
    crosswalk_folder: Path | None = None,
) -> None:
    """Extract FHLB members from the Avery lender file, supplemented from FHFA's listings.

    Args:
        match_folder: Output directory for intermediate data.
        max_year: Maximum year to include.
        overwrite: If True, overwrite existing file.
        crosswalk_folder: Directory for crosswalk outputs.
            Defaults to ``HMDA_FHLB_CROSSWALK_DIR``.
    """
    if crosswalk_folder is None:
        crosswalk_folder = HMDAFHLBMatchingConfig.HMDA_FHLB_CROSSWALK_DIR
    crosswalk_folder.mkdir(parents=True, exist_ok=True)
    output_file = crosswalk_folder / "hmda_fhlb_lender_crosswalk_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("FHLB member crosswalk already exists: %s", output_file)
        return

    logger.info("Extracting FHLB members from Avery file (post-2018)")

    avery_path = HMDAFHLBMatchingConfig.AVERY_FILE_PATH
    if not avery_path.exists():
        raise FileNotFoundError(f"Avery file not found: {avery_path}")

    import pandas as pd

    # The data sheet is first; its name is a release label ("beta7", "beta3") that
    # changes between vintages, so it is read by position.
    avery_full = pd.read_excel(avery_path, sheet_name=0)
    logger.info(
        "Loaded Avery file: %s rows, %s columns", f"{avery_full.shape[0]:,}", avery_full.shape[1]
    )

    # Statistics
    total_by_year = avery_full.groupby("YEAR").size()
    fhlb_members = avery_full[avery_full["FHLB"].notna()]
    fhlb_by_year = fhlb_members.groupby("YEAR").size()

    for year in sorted(total_by_year.index):
        total = total_by_year.get(year, 0)
        fhlb = fhlb_by_year.get(year, 0)
        pct = fhlb / total if total > 0 else 0
        logger.info(
            "Year %s: %s FHLB members / %s total LEIs (%s)",
            year,
            f"{fhlb:,}",
            f"{total:,}",
            f"{pct:.1%}",
        )

    pdf = fhlb_members.rename(columns={"LEI": "LEI", "FHLB": "FHFBID", "YEAR": "Year"})
    pdf = pdf[["LEI", "FHFBID", "Year"]]

    df = pl.from_pandas(pdf)

    df = df.with_columns(
        [
            pl.col("Year").cast(pl.Int32),
            pl.col("FHFBID").cast(pl.Float64).cast(pl.Int64),
        ]
    ).filter(pl.col("Year") <= max_year)

    # Supplement from FHFA's member listings: members Avery misses because the listing
    # printed a retired identifier (credit-union charter conversions).
    unflagged = pl.from_pandas(
        avery_full.loc[avery_full["FHLB"].isna(), ["YEAR", "LEI", "NCUA", "CERT", "RSSD"]]
    ).filter(pl.col("LEI").is_not_null(), pl.col("YEAR").is_between(2018, max_year))
    df_supplement = _supplement_fhlb_members_post2018(
        unflagged, _load_member_listings(), _load_charter_aliases()
    )
    df = pl.concat([df, df_supplement], how="vertical_relaxed")
    logger.info("Added %s supplemental FHLB member-year rows", f"{len(df_supplement):,}")

    with atomic_output(output_file) as tmp:
        df.write_parquet(tmp)
    logger.info("Saved FHLB member crosswalk to %s", output_file)


def prepare_fhlb_pre2018(match_folder: Path, max_year: int, overwrite: bool = False) -> None:
    """Clean FHLB acquisition data for pre-2018 matching.

    Args:
        match_folder: Output directory
        max_year: Maximum year to process
        overwrite: If True, overwrite existing file
    """
    output_file = match_folder / "fhlb_acquisitions_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("FHLB cleaned data already exists: %s", output_file)
        return

    logger.info("Cleaning FHLB data (pre-2018)")

    fhlb_conf = HMDAFHLBMatchingConfig.get_fhlb_config()
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
        "TotalAnnualIncomeAmount",
        "Borrower1SexType",
        "Borrower2SexType",
        "FHFBID",
    ]

    dfs = []
    for year in range(2009, max_year + 1):
        ffile = fhlb_dir / f"fhlb_ama_{year}.parquet"
        if not ffile.exists():
            logger.warning("FHLB data not found for %s: %s", year, ffile)
            continue

        # Every column is in every AMA silver year; a missing one means stale silver (e.g.
        # an income column renamed upstream), and a null stand-in would silently drop
        # every candidate pair at the income screen.
        missing = [c for c in keep_cols if c not in pl.scan_parquet(ffile).collect_schema()]
        if missing:
            raise ValueError(f"FHLB AMA silver {ffile.name} lacks {missing}; rebuild FHLB silver")
        dfs.append(pl.read_parquet(ffile, columns=keep_cols))

    if not dfs:
        raise FileNotFoundError(f"No FHLB data found in {fhlb_dir}")

    df = pl.concat(dfs, how="diagonal").filter(pl.col("data_year") <= 2017)

    # Silver AMA already stores CensusTractIdentifier as the 6-digit integer tract
    # code (raw dotted-decimal 5203.01 -> 520301, scaled in fhlb/import_silver.py);
    # scaling again here would overflow tract digits into the county field and zero
    # out HMDA tract matching. Cast only.
    df = df.with_columns(pl.col("CensusTractIdentifier").round(0).cast(pl.Int64))

    df = create_census_tract_from_fips(
        df.lazy(),
        state_col="FIPSStateNumericCode",
        county_col="FIPSCountyCode",
        tract_col="CensusTractIdentifier",
        output_col="census_tract",
    ).collect()

    df = df.rename(
        {
            "NoteDate": "Note Date",
            "PropertyUsageType": "Property Usage Type",
            "LoanPurposeType": "Loan Purpose Type",
            "Borrower1SexType": "Borrower 1 Gender Type",
            "Borrower2SexType": "Borrower 2 Gender Type",
        }
    )

    df = df.drop(["FIPSStateNumericCode", "FIPSCountyCode", "CensusTractIdentifier"])

    df = df.with_columns([(pl.col("MortgageType") + 1).alias("Loan Type")]).drop("MortgageType")

    df = df.with_columns([(pl.col("NoteAmount") / 1000).round(0).alias("Round Loan Amount")])

    # LoanCharacteristicsID is unique - no deduplication needed
    df.write_parquet(output_file)
    logger.info("Saved cleaned FHLB data to %s", output_file)


def prepare_fhlb_post2018(match_folder: Path, max_year: int, overwrite: bool = False) -> None:
    """Clean FHLB acquisition data for post-2018 matching.

    Args:
        match_folder: Output directory
        max_year: Maximum year to process
        overwrite: If True, overwrite existing file
    """
    output_file = match_folder / "fhlb_acquisitions_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("FHLB cleaned data already exists: %s", output_file)
        return

    logger.info("Cleaning FHLB data (post-2018)")

    fhlb_conf = HMDAFHLBMatchingConfig.get_fhlb_config()
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
        "TotalAnnualIncomeAmount",
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
            logger.warning("FHLB data not found for %s: %s", year, ffile)

    if not dfs:
        raise FileNotFoundError(f"No FHLB data found in {fhlb_dir}")

    df = pl.concat(dfs, how="diagonal").filter(pl.col("data_year") >= 2018)

    # See note in the pre-2018 path: silver already carries the scaled 6-digit
    # integer tract code; do not scale again. Cast only.
    df = df.with_columns(pl.col("CensusTractIdentifier").round(0).cast(pl.Int64))

    df = create_census_tract_from_fips(
        df.lazy(),
        state_col="FIPSStateNumericCode",
        county_col="FIPSCountyCode",
        tract_col="CensusTractIdentifier",
    ).collect()

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
    df_dup = df.filter(is_bin_edge(pl.col("NoteAmount"))).with_columns(
        [pl.lit(1).cast(pl.Int64).alias("i_Duplicate")]
    )

    df_all = pl.concat([df, df_dup], how="diagonal")

    df_all = df_all.with_columns(
        round_to_fhfa_bin(pl.col("NoteAmount") - 10000 * pl.col("i_Duplicate"))
        .cast(pl.Int64)
        .alias("Round Loan Amount")
    )

    df_all.write_parquet(output_file)
    logger.info("Saved cleaned FHLB data to %s", output_file)


def prepare_hmda_pre2018(
    match_folder: Path, overwrite: bool = False, crosswalk_folder: Path | None = None
) -> None:
    """Clean HMDA data for FHLB matching (pre-2018).

    Args:
        match_folder: Output directory
        overwrite: If True, overwrite existing file
        crosswalk_folder: Directory for crosswalk files. Defaults to ``HMDA_FHLB_CROSSWALK_DIR``.
    """
    if crosswalk_folder is None:
        crosswalk_folder = HMDAFHLBMatchingConfig.HMDA_FHLB_CROSSWALK_DIR
    output_file = match_folder / "hmda_fhlb_members_pre2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("HMDA cleaned data already exists: %s", output_file)
        return

    logger.info("Cleaning HMDA data for FHLB members (pre-2018)")

    hmda_conf = HMDAFHLBMatchingConfig.get_hmda_config()
    cw_file = crosswalk_folder / "hmda_fhlb_lender_crosswalk_pre2018.parquet"

    if not cw_file.exists():
        raise FileNotFoundError(f"Crosswalk file not found: {cw_file}")

    cw = pl.scan_parquet(cw_file)

    hmda_dir = hmda_conf.HMDAConfig.get_dataset_dir("silver", "loans", "period_2007_2017")

    hmda_cols = [
        "HMDAIndex",  # native string ID — preserved for stable crosswalk keys
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
            logger.warning("HMDA silver data not found for %s: %s", year, year_path)
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
                logger.warning("Skipping %s - missing required columns", file)
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

    df = pl.concat(dfs, how="diagonal")

    # Join with FHLB members
    df = df.join(
        cw.collect(),
        left_on=["respondent_id", "agency_code", "activity_year"],
        right_on=["hmprid", "code", "Year"],
        how="inner",
    )

    df.write_parquet(output_file)
    logger.info("Saved HMDA FHLB member data to %s", output_file)


def prepare_hmda_post2018(
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
        crosswalk_folder: Directory for crosswalk files. Defaults to ``HMDA_FHLB_CROSSWALK_DIR``.
    """
    if crosswalk_folder is None:
        crosswalk_folder = HMDAFHLBMatchingConfig.HMDA_FHLB_CROSSWALK_DIR
    output_file = match_folder / "hmda_fhlb_members_post2018.parquet"

    if not should_process_file(output_file, overwrite):
        logger.info("HMDA cleaned data already exists: %s", output_file)
        return

    logger.info("Cleaning HMDA data for FHLB members (post-2018)")

    cw_file = crosswalk_folder / "hmda_fhlb_lender_crosswalk_post2018.parquet"
    if not cw_file.exists():
        raise FileNotFoundError(f"Crosswalk file not found: {cw_file}")

    cw = pl.read_parquet(cw_file)

    total_loans_counts = {}
    dfs = []

    for year in range(2018, max_year + 1):
        try:
            # Release selection (most mature available) belongs to hmda.load.
            lf = scan_originations("post2018", year, year)
        except FileNotFoundError as e:
            logger.warning("HMDA silver data not found for %s: %s", year, e)
            continue

        lf = lf.filter(pl.col("purchaser_type") != 3).select(HMDA_POST2018_COLS)

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
        logger.info(
            "Year %s: %s FHLB member loans / %s total (%s)",
            year,
            f"{fhlb_loans:,}",
            f"{total:,}",
            f"{pct:.1%}",
        )

    result.write_parquet(output_file)
    logger.info("Saved HMDA FHLB member data to %s", output_file)
