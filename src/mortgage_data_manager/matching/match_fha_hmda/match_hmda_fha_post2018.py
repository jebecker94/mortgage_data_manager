"""FHA-HMDA Post-2018 Matching Pipeline.

Created on Sun Apr  2 17:04:57 2023
Last Updated: 2026-01-25
@author: Jonathan Becker

This module matches FHA (Federal Housing Administration) mortgage data with HMDA
(Home Mortgage Disclosure Act) data for the post-2018 period to create loan-level
crosswalks.
"""

from __future__ import annotations

import gzip

# Standard library imports
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Third-party imports
import polars as pl
import pyarrow.parquet as pq

# Local application imports
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.reference import to_county_vintage

# Relative imports
from .config import (
    CROSSWALK_DIR,
    CROSSWALK_OUTPUT_DIR,
    FHA_SILVER_DIR,
    HMDA_SILVER_DIR,
    HUD_CROSSWALK_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    FHAHMDAMatchingConfig,
)

logger = get_logger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class MatchConfig:
    """Configuration for FHA-HMDA matching.

    Attributes:
        rate_tolerance: Maximum allowed interest rate difference (e.g., 0.005 = 0.5 bps)
        use_fallback_joins: Whether to use fallback join strategies for missing ZIP/rate
        apply_lender_filter: Whether to apply lender quality filtering
        min_lender_matches: Minimum matches per lender (only if apply_lender_filter=True)
        apply_timing_filter: Whether to apply year/month timing filters
        year_by_year: Whether to match year-by-year (True) or all years at once (False)
    """

    rate_tolerance: float = 0.005
    use_fallback_joins: bool = True
    apply_lender_filter: bool = True
    min_lender_matches: int = 25
    apply_timing_filter: bool = False
    year_by_year: bool = True


# Pre-configured settings for each round
ROUND1_CONFIG = MatchConfig(
    rate_tolerance=0.005,
    use_fallback_joins=True,
    apply_lender_filter=True,
    min_lender_matches=25,
    apply_timing_filter=False,
    year_by_year=True,
)

ROUND2_CONFIG = MatchConfig(
    rate_tolerance=0.025,
    use_fallback_joins=False,
    apply_lender_filter=False,
    min_lender_matches=0,
    apply_timing_filter=True,
    year_by_year=False,
)


# =============================================================================
# Helper Functions
# =============================================================================


def get_hmda_files(
    hmda_dir: Path,
    year: int,
    file_type_preference: list[str] | None = None,
) -> Path:
    """Get HMDA file for year with file type preference.

    HMDA data can have multiple file versions (a, b, c) where 'a' is preferred.

    Args:
        hmda_dir: Base HMDA silver directory (hive-partitioned by activity_year/file_type)
        year: Year to get files for
        file_type_preference: Order of file type preference. Defaults to ["a", "b", "c"]

    Returns:
        Path to the first available HMDA parquet file for the year

    Raises:
        FileNotFoundError: If no HMDA file found for the specified year
    """
    if file_type_preference is None:
        file_type_preference = ["a", "b", "c"]

    year_dir = hmda_dir / f"activity_year={year}"
    if not year_dir.exists():
        raise FileNotFoundError(f"No HMDA data directory found for year {year}: {year_dir}")

    for ft in file_type_preference:
        ft_dir = year_dir / f"file_type={ft}"
        if ft_dir.exists():
            files = list(ft_dir.glob("*.parquet"))
            if files:
                logger.debug(f"Found HMDA file for year {year} with file_type={ft}")
                return files[0]

    # If no preferred types found, try any available
    available_types = sorted(year_dir.glob("file_type=*"))
    if available_types:
        files = list(available_types[0].glob("*.parquet"))
        if files:
            logger.warning(
                f"Using non-preferred file type for year {year}: {available_types[0].name}"
            )
            return files[0]

    raise FileNotFoundError(f"No HMDA parquet files found for year {year} in {year_dir}")


def get_fha_files(
    fha_dir: Path,
    year: int,
    months: range | None = None,
) -> list[Path]:
    """Get FHA files for year.

    FHA data is hive-partitioned by Year and Month.

    Args:
        fha_dir: Base FHA silver directory
        year: Year to get files for
        months: Optional range of months. Defaults to all 12 months.

    Returns:
        List of paths to FHA parquet files for the year/months
    """
    if months is None:
        months = range(1, 13)

    files = []
    year_dir = fha_dir / f"Year={year}"
    if not year_dir.exists():
        logger.warning(f"FHA year directory not found: {year_dir}")
        return files

    for month in months:
        month_dir = year_dir / f"Month={month}"
        if month_dir.exists():
            month_files = list(month_dir.glob("*.parquet"))
            files.extend(month_files)

    return sorted(files)


def build_zip_tract_crosswalk(
    hud_dir: Path,
    census_vintage: Literal[2010, 2020],
    output_path: Path,
) -> Path:
    """Build consolidated ZIP-tract crosswalk from HUD data.

    Uses the HUD subpackage's load_crosswalk_by_vintage to load all quarterly
    ZIP_TRACT data for a census vintage, then deduplicates to unique pairs.

    The HUD crosswalk uses different census tract vintages:
    - 2010 Census tracts: Used for years 2012-2022
    - 2020 Census tracts: Used for years 2023+

    Args:
        hud_dir: Path to HUD raw data directory (passed to load_crosswalk_by_vintage).
        census_vintage: Census tract vintage (2010 or 2020)
        output_path: Path to save consolidated crosswalk

    Returns:
        Path to the saved crosswalk file
    """
    from mortgage_data_manager.hud.load import load_crosswalk_by_vintage

    if census_vintage not in (2010, 2020):
        raise ValueError(f"Invalid census_vintage: {census_vintage}. Must be 2010 or 2020")

    logger.info(f"Building {census_vintage} census crosswalk from HUD data in {hud_dir}")

    lf = load_crosswalk_by_vintage("ZIP_TRACT", census_vintage, data_dir=hud_dir)

    df = (
        lf.select(["zip", "geoid"])
        .collect()
        .select([
            pl.col("zip").alias("ZIP"),
            pl.col("geoid").alias("TRACT"),
        ])
        .unique()
    )

    logger.info(f"Built crosswalk with {len(df)} unique ZIP-tract pairs")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)
    logger.info(f"Saved crosswalk to {output_path}")

    return output_path


def get_crosswalk_for_year(
    year: int,
    crosswalk_dir: Path | None = None,
    hud_dir: Path | None = None,
) -> Path:
    """Return appropriate crosswalk based on matching year.

    Automatically builds crosswalk if it doesn't exist.

    Args:
        year: Year for matching (determines census vintage)
        crosswalk_dir: Directory for crosswalk files. Defaults to config.
        hud_dir: HUD raw data directory. Defaults to config.

    Returns:
        Path to appropriate crosswalk file
    """
    if crosswalk_dir is None:
        crosswalk_dir = CROSSWALK_DIR
    if hud_dir is None:
        hud_dir = HUD_CROSSWALK_DIR

    census_vintage: Literal[2010, 2020] = 2010 if year < 2020 else 2020
    crosswalk_file = crosswalk_dir / f"zip_tract_{census_vintage}_census.parquet"

    if not crosswalk_file.exists():
        logger.info(f"Building {census_vintage} census crosswalk (not found: {crosswalk_file})")
        build_zip_tract_crosswalk(hud_dir, census_vintage, crosswalk_file)

    return crosswalk_file


# =============================================================================
# Data Preparation Functions
# =============================================================================


def prepare_fha_merge_data(
    fha_folder: Path,
    match_folder: Path,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    file_suffix: str = "_post2018",
) -> Path:
    """Prepare FHA data for merge with HMDA.

    Args:
        fha_folder: Folder where FHA silver data is stored (hive-partitioned)
        match_folder: Folder where intermediate match data will be saved
        min_year: Minimum year to include
        max_year: Maximum year to include
        file_suffix: Suffix for output file names

    Returns:
        Path to saved FHA match data file
    """
    logger.info(f"Preparing FHA data for matching (years {min_year}-{max_year})")

    df = pl.scan_parquet(fha_folder / "**/*.parquet")

    # Filter to post-2018 period (include Dec of prior year, Jan-Feb of next year)
    df = df.filter(
        pl.col("Year") >= min_year - 1,
        ((pl.col("Year") >= min_year) | (pl.col("Month") >= 11)),
    )
    df = df.filter(pl.col("Year") <= max_year + 1)

    # Create Match Variables
    df = df.with_columns([
        (pl.col("Product Type") == "Adjustable Rate").cast(pl.Int8).alias("i_ARM"),
        (pl.col("Interest Rate") * 1000).round(0).cast(pl.Int16).alias("Interest Rate 1000"),
        ((pl.col("Interest Rate") * 8).round(0) * 125).cast(pl.Int16).alias("Interest Rate 125"),
        ((((pl.col("Mortgage Amount") - 5000) / 10000).round(0) * 10000) + 5000)
        .cast(pl.Int32)
        .alias("Loan Amount Round"),
    ])

    # Drop columns not needed for matching
    drop_cols = [
        "Property City",
        "Property County",
        "Down Payment Source",
        "Property Type",
        "Non Profit Number",
        "Originating Mortgagee",
        "Sponsor Name",
    ]
    df = df.drop(drop_cols, strict=False)

    df = df.sort([
        "Year", "Month", "Property State", "Property Zip", "Interest Rate", "Mortgage Amount",
    ])

    match_folder.mkdir(parents=True, exist_ok=True)
    save_file = match_folder / f"fha_match_data{file_suffix}.parquet"
    df.sink_parquet(save_file)
    logger.info(f"Saved FHA match data to {save_file}")

    return save_file


def prepare_hmda_merge_data(
    hmda_folder: Path,
    match_folder: Path,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    file_suffix: str = "_post2018",
    file_type_preference: list[str] | None = None,
    crosswalk_dir: Path | None = None,
    hud_dir: Path | None = None,
) -> Path:
    """Prepare HMDA data for merge with FHA.

    Args:
        hmda_folder: Folder where HMDA silver data is stored (hive-partitioned)
        match_folder: Folder where intermediate match data will be saved
        min_year: Minimum year to include
        max_year: Maximum year to include
        file_suffix: Suffix for output file names
        file_type_preference: Order of HMDA file type preference
        crosswalk_dir: Directory for crosswalk files
        hud_dir: HUD crosswalk source directory

    Returns:
        Path to saved HMDA match data file
    """
    logger.info(f"Preparing HMDA data for matching (years {min_year}-{max_year})")

    if crosswalk_dir is None:
        crosswalk_dir = CROSSWALK_DIR
    if hud_dir is None:
        hud_dir = HUD_CROSSWALK_DIR

    keep_cols = [
        "activity_year", "lei", "state_code", "county_code", "census_tract",
        "action_taken", "loan_type", "loan_purpose", "reverse_mortgage",
        "loan_amount", "interest_rate", "loan_term", "intro_rate_period",
        "total_units", "submission_of_application", "HMDAIndex",
        "respondent_id", "agency_code", "sequence_number",
    ]

    df_list: list[pl.LazyFrame] = []
    for year in range(min_year, max_year + 1):
        try:
            file = get_hmda_files(hmda_folder, year, file_type_preference)
        except FileNotFoundError as e:
            logger.warning(f"Skipping year {year}: {e}")
            continue

        logger.info(f"Reading HMDA data from: {file}")

        schema = pq.read_schema(file)
        available_cols = schema.names
        use_cols = [c for c in available_cols if c in keep_cols]

        lf = (
            pl.scan_parquet(file, hive_partitioning=False)
            .select(use_cols)
            .filter(
                (pl.col("action_taken") == 1) &  # Originated loans
                (pl.col("loan_type") == 2)  # FHA loans
            )
        )

        if "reverse_mortgage" in use_cols:
            lf = lf.filter(pl.col("reverse_mortgage") != 1)
        if "total_units" in use_cols:
            lf = lf.filter(pl.col("total_units") <= 4)

        drop_cols = ["action_taken", "loan_type", "reverse_mortgage", "total_units"]
        cols_to_drop = [c for c in drop_cols if c in use_cols]
        if cols_to_drop:
            lf = lf.drop(cols_to_drop)

        df_list.append(lf)

    if not df_list:
        raise ValueError(f"No HMDA data found for years {min_year}-{max_year}")

    # diagonal: align by name. HMDA file_type vintages order the same columns
    # differently (e.g. the 2025 MLAR 'e' file), which a vertical concat rejects.
    df = pl.concat(df_list, how="diagonal").collect()
    logger.info(f"Combined HMDA data: {len(df)} rows")

    # CT planning-region bridge: FHA endorsement data stays on the traditional CT
    # counties (09001-09015) while HMDA switched its tract/county coding to
    # planning regions (09110-09190) in 2024. Without this, the downstream
    # FIPS == county_code geography filter drops every CT 2024+ FHA<->HMDA pair.
    # Translate HMDA's planning-region tracts back to the 2020 county convention
    # and refresh county_code from the (now county-coded) tract for CT rows. The
    # ZIP<->tract crosswalk carries both conventions, so the ZIP join is
    # unaffected. Idempotent for pre-2024 / non-CT records.
    df = to_county_vintage(df.lazy(), tract_col="census_tract").collect()
    if "county_code" in df.columns:
        df = df.with_columns(
            pl.when(
                pl.col("census_tract").is_not_null()
                & (pl.col("census_tract").str.slice(0, 2) == "09")
            )
            .then(pl.col("census_tract").str.slice(0, 5))
            .otherwise(pl.col("county_code"))
            .alias("county_code")
        )

    columns = df.columns

    # Replace exemption codes with null
    transform_exprs: list[pl.Expr] = []
    if "interest_rate" in columns:
        transform_exprs.append(
            pl.when(pl.col("interest_rate") == -99999)
            .then(None)
            .otherwise(pl.col("interest_rate"))
            .alias("interest_rate")
        )
    if "submission_of_application" in columns:
        transform_exprs.append(
            pl.when(pl.col("submission_of_application") == 1111)
            .then(None)
            .otherwise(pl.col("submission_of_application"))
            .alias("submission_of_application")
        )
    if transform_exprs:
        df = df.with_columns(transform_exprs)

    # Create ARM indicator
    if "intro_rate_period" in columns and "loan_term" in columns:
        df = df.with_columns(
            pl.when(
                (pl.col("intro_rate_period") == -99999) | (pl.col("loan_term") == -99999)
            )
            .then(None)
            .when(
                (pl.col("intro_rate_period") < pl.col("loan_term")) &
                (pl.col("intro_rate_period") >= 0)
            )
            .then(1)
            .otherwise(0)
            .cast(pl.Int8)
            .alias("1(ARM)")
        ).drop(["loan_term", "intro_rate_period"])

    # Create rounded interest rate columns
    if "interest_rate" in columns:
        df = df.with_columns(
            (pl.col("interest_rate") * 1000).round(0).cast(pl.Int32).alias(
                "HMDA Interest Rate (Integer)"
            )
        )
        df = df.with_columns(
            ((pl.col("HMDA Interest Rate (Integer)") / 125).round(0) * 125)
            .cast(pl.Int32)
            .alias("HMDA Interest Rate (Nearest 125)")
        )

    # Merge in ZIP codes from census tract crosswalk
    years_in_data = df["activity_year"].unique().to_list()
    needs_2010 = any(y < 2020 for y in years_in_data)
    needs_2020 = any(y >= 2020 for y in years_in_data)

    if needs_2010 and needs_2020:
        df_pre2020 = df.filter(pl.col("activity_year") < 2020)
        df_post2020 = df.filter(pl.col("activity_year") >= 2020)

        crosswalk_2010 = get_crosswalk_for_year(2018, crosswalk_dir, hud_dir)
        zip_tracts_2010 = pl.read_parquet(crosswalk_2010)
        df_pre2020 = df_pre2020.join(
            zip_tracts_2010, left_on="census_tract", right_on="TRACT", how="left",
        ).drop(["census_tract"], strict=False)

        crosswalk_2020 = get_crosswalk_for_year(2020, crosswalk_dir, hud_dir)
        zip_tracts_2020 = pl.read_parquet(crosswalk_2020)
        df_post2020 = df_post2020.join(
            zip_tracts_2020, left_on="census_tract", right_on="TRACT", how="left",
        ).drop(["census_tract"], strict=False)

        df = pl.concat([df_pre2020, df_post2020], how="diagonal")
    elif needs_2010:
        crosswalk_file = get_crosswalk_for_year(2018, crosswalk_dir, hud_dir)
        zip_tracts = pl.read_parquet(crosswalk_file)
        df = df.join(
            zip_tracts, left_on="census_tract", right_on="TRACT", how="left"
        ).drop(["census_tract"], strict=False)
    else:
        crosswalk_file = get_crosswalk_for_year(2020, crosswalk_dir, hud_dir)
        zip_tracts = pl.read_parquet(crosswalk_file)
        df = df.join(
            zip_tracts, left_on="census_tract", right_on="TRACT", how="left"
        ).drop(["census_tract"], strict=False)

    # Ensure string columns
    string_columns = ["respondent_id", "state_code", "HMDAIndex", "lei"]
    cast_exprs = [pl.col(col).cast(pl.Utf8) for col in string_columns if col in df.columns]
    if cast_exprs:
        df = df.with_columns(cast_exprs)

    match_folder.mkdir(parents=True, exist_ok=True)
    save_file = match_folder / f"hmda_match_data{file_suffix}.parquet"
    df.write_parquet(save_file)
    logger.info(f"Saved HMDA match data to {save_file}")

    return save_file


# =============================================================================
# Core Matching Functions
# =============================================================================


def _apply_common_filters(
    df: pl.DataFrame,
    rate_tolerance: float | None = None,
) -> pl.DataFrame:
    """Apply filters common to both rounds.

    Order matters - this matches the original code exactly:
    1. Loan purpose consistency
    2. Rate tolerance (if provided)
    3. Sponsor consistency
    4. ARM consistency

    Args:
        df: DataFrame with candidate matches from FHA-HMDA join. Must contain columns:
            'loan_purpose', 'Loan Purpose', 'interest_rate', 'Interest Rate',
            'submission_of_application', 'Sponsor Number', 'Product Type', '1(ARM)'.
        rate_tolerance: Maximum allowed interest rate difference in decimal form
            (e.g., 0.005 = 0.5%). If None, rate filtering is skipped.

    Returns:
        Filtered DataFrame with only matches passing all consistency checks
    """
    # Loan Purpose consistency
    df = df.filter(
        ~(
            (pl.col("loan_purpose") == 1) &
            pl.col("Loan Purpose").is_in(["Refi_FHA", "Refi_Conv_Curr"])
        )
    )
    df = df.filter(
        ~(pl.col("loan_purpose").is_in([31, 32]) & (pl.col("Loan Purpose") == "Purchase"))
    )

    # Rate tolerance (applied here to match original order)
    if rate_tolerance is not None:
        df = df.with_columns(
            (pl.col("interest_rate") - pl.col("Interest Rate")).alias("Interest Rate Difference")
        )
        df = df.filter(~(pl.col("Interest Rate Difference").abs() > rate_tolerance))
        df = df.drop("Interest Rate Difference")

    # Sponsor Matches
    df = df.filter(
        ~((pl.col("submission_of_application") == 2) & pl.col("Sponsor Number").is_null())
    )
    df = df.filter(
        ~((pl.col("submission_of_application") == 1) & pl.col("Sponsor Number").is_not_null())
    )

    # ARM consistency
    df = df.filter(~((pl.col("Product Type") == "FRM") & (pl.col("1(ARM)") == 1)))
    df = df.filter(~((pl.col("Product Type") == "ARM") & (pl.col("1(ARM)") == 0)))

    return df


def _apply_lender_filter(df: pl.DataFrame) -> pl.DataFrame:
    """Apply lender quality filtering (two rounds of filtering + shared match check).

    This is the complex lender filtering logic from Round 1.
    NOTE: Does NOT include the "drop small lenders" step - that must be done
    separately via _drop_small_lenders() AFTER _keep_unique_matches().

    Args:
        df: DataFrame with candidate matches. Must contain columns: 'Master Number'
            (created from Originating Mortgagee Number or Sponsor Number), 'lei',
            'FHA_Index', and 'HMDAIndex'.

    Returns:
        Filtered DataFrame retaining only high-quality lender-to-LEI matches based on
        match frequency and fraction thresholds (>= 1% of loans for each entity, top-ranked
        matches, and shared match analysis)
    """
    # === Lender Filtering Round 1 ===
    df = df.with_columns([
        pl.col("FHA_Index").n_unique().over("Master Number").alias("Count Mortgagee"),
        pl.col("HMDAIndex").n_unique().over("lei").alias("Count LEI"),
        pl.col("FHA_Index").n_unique().over(["Master Number", "lei"]).alias("Count Matches (FHA)"),
        pl.col("HMDAIndex").n_unique().over(["Master Number", "lei"]).alias("Count Matches (HMDA)"),
    ])
    df = df.with_columns([
        (pl.col("Count Matches (FHA)") / pl.col("Count Mortgagee")).alias("Fraction Matches (FHA)"),
        (pl.col("Count Matches (HMDA)") / pl.col("Count LEI")).alias("Fraction Matches (HMDA)"),
    ])
    df = df.filter(pl.col("Fraction Matches (FHA)") >= 0.01)
    df = df.filter(pl.col("Fraction Matches (HMDA)") >= 0.01)

    # Keep Good Lender Matches (Round 1)
    lender_matches = df.select([
        "Master Number", "lei", "Count Mortgagee", "Count LEI",
        "Count Matches (FHA)", "Count Matches (HMDA)",
        "Fraction Matches (FHA)", "Fraction Matches (HMDA)",
    ]).unique()

    lender_matches = lender_matches.with_columns([
        pl.col("Count Matches (FHA)").rank("dense", descending=True).over("Master Number").alias("Rank"),
        pl.col("Fraction Matches (FHA)").max().over("Master Number").alias("Max Fraction"),
    ])
    lender_matches = lender_matches.filter(
        (pl.col("Rank") == 1) |
        (pl.col("Count Mortgagee") < 1000) |
        (pl.col("Fraction Matches (FHA)") < 1 - pl.col("Max Fraction"))
    )
    df = df.join(
        lender_matches.select(["Master Number", "lei"]),
        on=["Master Number", "lei"],
        how="inner",
    )

    # === Lender Filtering Round 2 ===
    df = df.drop([
        "Count Mortgagee", "Count LEI", "Count Matches (FHA)", "Count Matches (HMDA)",
        "Fraction Matches (FHA)", "Fraction Matches (HMDA)",
    ])
    df = df.with_columns([
        pl.col("FHA_Index").n_unique().over("Master Number").alias("Count Mortgagee"),
        pl.col("HMDAIndex").n_unique().over("lei").alias("Count LEI"),
        pl.col("FHA_Index").n_unique().over(["Master Number", "lei"]).alias("Count Matches (FHA)"),
        pl.col("HMDAIndex").n_unique().over(["Master Number", "lei"]).alias("Count Matches (HMDA)"),
    ])
    df = df.with_columns([
        (pl.col("Count Matches (FHA)") / pl.col("Count Mortgagee")).alias("Fraction Matches (FHA)"),
        (pl.col("Count Matches (HMDA)") / pl.col("Count LEI")).alias("Fraction Matches (HMDA)"),
    ])

    lender_matches = df.select([
        "Master Number", "lei", "Count Mortgagee", "Count LEI",
        "Count Matches (FHA)", "Count Matches (HMDA)",
        "Fraction Matches (FHA)", "Fraction Matches (HMDA)",
    ]).unique()

    lender_matches = lender_matches.with_columns([
        pl.col("Count Matches (FHA)").rank("dense", descending=True).over("Master Number").alias("Rank"),
        pl.col("Fraction Matches (FHA)").max().over("Master Number").alias("Max Fraction"),
    ])
    lender_matches = lender_matches.filter(pl.col("Max Fraction") >= 0.5)
    lender_matches = lender_matches.filter(
        (pl.col("Rank") == 1) |
        (pl.col("Count Mortgagee") < 1000) |
        (pl.col("Fraction Matches (FHA)") < 1 - pl.col("Max Fraction"))
    )
    df = df.join(
        lender_matches.select(["Master Number", "lei", "Rank"]),
        on=["Master Number", "lei"],
        how="inner",
    )

    # === Matched to Best ===
    df = df.with_columns(
        pl.col("Rank").min().over("FHA_Index").alias("Min Rank")
    )
    df = df.with_columns(
        (pl.col("Min Rank") == 1).alias("1(Matched to Best)")
    )
    df = df.with_columns(
        pl.when(pl.col("1(Matched to Best)"))
        .then(pl.col("FHA_Index").n_unique().over(["Master Number", "lei", "1(Matched to Best)"]))
        .otherwise(None)
        .alias("Count Shared Matches")
    )
    df = df.with_columns(
        pl.col("Count Shared Matches").max().over(["Master Number", "lei"]).alias("Count Shared Matches")
    )
    df = df.with_columns(
        (pl.col("Count Shared Matches") / pl.col("Count Matches (FHA)")).alias("Fraction Shared Matches")
    )
    df = df.filter(~((pl.col("Rank") != 1) & (pl.col("Fraction Shared Matches") > 0.1)))

    # Drop intermediate columns
    df = df.drop([
        "Count Mortgagee", "Count LEI", "Count Matches (FHA)", "Count Matches (HMDA)",
        "Fraction Matches (FHA)", "Fraction Matches (HMDA)", "Rank", "Min Rank",
        "1(Matched to Best)", "Count Shared Matches", "Fraction Shared Matches",
    ])

    return df


def _drop_small_lenders(df: pl.DataFrame, min_matches: int = 25) -> pl.DataFrame:
    """Drop lenders with fewer than min_matches total matches.

    IMPORTANT: This should be called AFTER _keep_unique_matches() to match original behavior.

    Args:
        df: DataFrame with candidate matches. Must contain 'Master Number' column.
        min_matches: Minimum number of total matches required for a lender to be retained.
            Lenders with fewer matches are removed entirely.

    Returns:
        Filtered DataFrame containing only lenders meeting the minimum match threshold
    """
    df = df.with_columns(
        pl.len().over("Master Number").alias("Count Matches (Total)")
    )
    df = df.filter(pl.col("Count Matches (Total)") >= min_matches)
    return df


def _keep_unique_matches(df: pl.DataFrame) -> pl.DataFrame:
    """Keep only one-to-one matches (unique on both FHA_Index and HMDAIndex).

    Args:
        df: DataFrame with candidate matches. Must contain 'FHA_Index' and 'HMDAIndex' columns.

    Returns:
        DataFrame filtered to matches where both FHA_Index and HMDAIndex appear exactly once
        (i.e., no duplicate matches on either side)
    """
    df = df.with_columns([
        pl.len().over("FHA_Index").alias("CountLoanFHA"),
        pl.len().over("HMDAIndex").alias("CountLoanHMDA"),
    ])
    df = df.filter((pl.col("CountLoanFHA") == 1) & (pl.col("CountLoanHMDA") == 1))
    df = df.drop(["CountLoanFHA", "CountLoanHMDA"])
    return df


def match_fha_hmda(
    df_fha: pl.DataFrame,
    df_hmda: pl.DataFrame,
    use_fallback_joins: bool = True,
) -> pl.DataFrame:
    """Join FHA and HMDA data (no filters, just join + FIPS check).

    Args:
        df_fha: FHA data (with ZIP zero-padded)
        df_hmda: HMDA data (with ZIP zero-padded)
        use_fallback_joins: Whether to use fallback strategies for missing ZIP/rate

    Returns:
        DataFrame with joined records (before any filtering)
    """
    # Rename FHA columns for join (polars requires same column names)
    df_fha_renamed = df_fha.rename({
        "Property State": "state_code_fha",
        "Loan Amount Round": "loan_amount_fha",
        "Interest Rate 125": "interest_rate_125_fha",
        "Property Zip": "zip_fha",
    })

    if use_fallback_joins:
        # === Strategy 1: Missing ZIP (use county + rate) ===
        match_hmda_nozip = df_hmda.filter(
            pl.col("ZIP").is_null() & pl.col("interest_rate").is_not_null()
        )
        matches_nozip = match_hmda_nozip.join(
            df_fha_renamed,
            left_on=["state_code", "loan_amount", "HMDA Interest Rate (Nearest 125)"],
            right_on=["state_code_fha", "loan_amount_fha", "interest_rate_125_fha"],
            how="inner",
        ).filter(
            (pl.col("FIPS") == pl.col("county_code")) |
            pl.col("FIPS").is_null() |
            pl.col("county_code").is_null()
        )

        # === Strategy 2: Missing Rate (use ZIP) ===
        match_hmda_norate = df_hmda.filter(
            pl.col("ZIP").is_not_null() & pl.col("interest_rate").is_null()
        )
        matches_norate = match_hmda_norate.join(
            df_fha_renamed,
            left_on=["state_code", "loan_amount", "ZIP"],
            right_on=["state_code_fha", "loan_amount_fha", "zip_fha"],
            how="inner",
        ).filter(
            (pl.col("FIPS") == pl.col("county_code")) |
            pl.col("FIPS").is_null() |
            pl.col("county_code").is_null()
        )

        # === Strategy 3: Full match (both ZIP and rate) ===
        df_hmda_full = df_hmda.filter(
            pl.col("ZIP").is_not_null() & pl.col("interest_rate").is_not_null()
        )
    else:
        # Only use full match strategy
        df_hmda_full = df_hmda
        matches_nozip = pl.DataFrame()
        matches_norate = pl.DataFrame()

    matches_full = df_hmda_full.join(
        df_fha_renamed,
        left_on=["state_code", "loan_amount", "HMDA Interest Rate (Nearest 125)", "ZIP"],
        right_on=["state_code_fha", "loan_amount_fha", "interest_rate_125_fha", "zip_fha"],
        how="inner",
    ).filter(
        (pl.col("FIPS") == pl.col("county_code")) |
        pl.col("FIPS").is_null() |
        pl.col("county_code").is_null()
    )

    # Combine all match strategies
    if use_fallback_joins:
        return pl.concat([matches_full, matches_nozip, matches_norate], how="diagonal")
    return matches_full


# =============================================================================
# Unified Matching Entry Point
# =============================================================================


def run_matching(
    match_folder: Path,
    config: MatchConfig,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    file_suffix: str = "_post2018",
    exclude_crosswalk: Path | None = None,
    round_number: int = 1,
    save_lender_crosswalk: bool = False,
) -> Path | None:
    """Run FHA-HMDA matching with given config.

    Args:
        match_folder: Folder with intermediate match data
        config: Matching configuration
        min_year: Minimum year to match
        max_year: Maximum year to match
        file_suffix: Suffix for input/output file names
        exclude_crosswalk: Path to crosswalk of already-matched records to exclude
        round_number: Round number for output naming (1 or 2)
        save_lender_crosswalk: Whether to save lender crosswalk

    Returns:
        Path to crosswalk file, or None if no matches found
    """
    logger.info(f"Starting Round {round_number} matching (years {min_year}-{max_year})")

    fha_file = match_folder / f"fha_match_data{file_suffix}.parquet"
    hmda_file = match_folder / f"hmda_match_data{file_suffix}.parquet"

    # Load data
    df_fha = (
        pl.scan_parquet(fha_file)
        .filter((pl.col("Year") >= min_year - 1) & (pl.col("Year") <= max_year + 1))
        .collect()
    )
    df_hmda = (
        pl.scan_parquet(hmda_file)
        .filter((pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year))
        .collect()
    )

    logger.info(f"Loaded FHA: {len(df_fha)} rows, HMDA: {len(df_hmda)} rows")

    # Exclude already matched records (Round 2)
    if exclude_crosswalk is not None:
        if not exclude_crosswalk.exists():
            raise FileNotFoundError(f"Exclusion crosswalk not found: {exclude_crosswalk}")
        crosswalk = pl.read_parquet(exclude_crosswalk)
        logger.info(f"Excluding {len(crosswalk)} previously matched records")
        df_fha = df_fha.join(crosswalk.select("FHA_Index"), on="FHA_Index", how="anti")
        df_hmda = df_hmda.join(crosswalk.select("HMDAIndex"), on="HMDAIndex", how="anti")
        logger.info(f"Unmatched FHA: {len(df_fha)}, HMDA: {len(df_hmda)}")

    # Trim FHA on dates (only for year-by-year matching)
    if config.year_by_year:
        df_fha = df_fha.filter((pl.col("Year") > min_year - 1) | (pl.col("Month") >= 12))
        df_fha = df_fha.filter((pl.col("Year") < max_year + 1) | (pl.col("Month") <= 2))

    # Zero-pad ZIPs
    if "Property Zip" in df_fha.columns:
        df_fha = df_fha.with_columns(pl.col("Property Zip").cast(pl.Utf8).str.zfill(5))
    if "ZIP" in df_hmda.columns:
        df_hmda = df_hmda.with_columns(pl.col("ZIP").cast(pl.Utf8).str.zfill(5))

    # === Join Phase ===
    if config.year_by_year:
        # Match year-by-year (join only, no filters yet)
        df_matches: list[pl.DataFrame] = []
        for year in range(min_year, max_year + 1):
            logger.info(f"Conducting FHA-to-HMDA matches for year: {year}")

            df_temp_hmda = df_hmda.filter(pl.col("activity_year") == year)
            df_temp_fha = df_fha.filter(
                (pl.col("Year") == year) |
                ((pl.col("Year") == year - 1) & (pl.col("Month") == 12)) |
                ((pl.col("Year") == year + 1) & (pl.col("Month") <= 2))
            )

            matches = match_fha_hmda(df_temp_fha, df_temp_hmda, config.use_fallback_joins)
            df_matches.append(matches)

        # Concatenate all years
        df = pl.concat(df_matches, how="diagonal")
    else:
        # Bulk matching (all years at once)
        df = match_fha_hmda(df_fha, df_hmda, config.use_fallback_joins)

    logger.info(f"Initial matches: {len(df)} rows")

    if len(df) == 0:
        logger.info(f"No matches found in Round {round_number}")
        return None

    # === Filter Phase ===
    # Apply common filters using _apply_common_filters
    df = _apply_common_filters(df, rate_tolerance=config.rate_tolerance)

    # Apply timing filter (Round 2 only)
    if config.apply_timing_filter:
        df = df.filter(~(pl.col("Year") > pl.col("activity_year") + 1))
        df = df.filter(~(pl.col("activity_year") > pl.col("Year") + 1))
        df = df.filter(~((pl.col("Year") == pl.col("activity_year") + 1) & (pl.col("Month") > 3)))
        df = df.filter(~((pl.col("Year") == pl.col("activity_year") - 1) & (pl.col("Month") < 12)))

    # Apply lender filter
    if config.apply_lender_filter:
        df = df.with_columns(
            pl.coalesce(pl.col("Originating Mortgagee Number"), pl.col("Sponsor Number"))
            .alias("Master Number")
        )
        df = _apply_lender_filter(df)

    # Keep unique matches
    df = _keep_unique_matches(df)

    # Drop small lenders (AFTER unique matches)
    if config.apply_lender_filter and config.min_lender_matches > 0:
        df = _drop_small_lenders(df, config.min_lender_matches)

    logger.info(f"Round {round_number} final matches: {len(df)} rows")

    if len(df) == 0:
        logger.info(f"No unique matches found in Round {round_number}")
        return None

    # Save crosswalk
    save_file = match_folder / f"fha_hmda_crosswalk_round{round_number}{file_suffix}.parquet"
    crosswalk_df = df.select(["FHA_Index", "HMDAIndex"])
    crosswalk_df.write_parquet(save_file)
    logger.info(f"Saved Round {round_number} crosswalk to {save_file}")

    # Save lender crosswalk (Round 1 only)
    if save_lender_crosswalk:
        lender_file = match_folder / f"fha_hmda_lei_lender_crosswalk{file_suffix}.csv.gz"
        lenders = df.select(["lei", "Sponsor Number", "Originating Mortgagee Number"]).unique()
        lenders = lenders.sort(["lei", "Sponsor Number", "Originating Mortgagee Number"])
        csv_content = lenders.write_csv(separator="|")
        with gzip.open(lender_file, "wt") as f:
            f.write(csv_content)
        logger.info(f"Saved lender crosswalk to {lender_file}")

    return save_file


def combine_crosswalks(
    match_folder: Path,
    output_folder: Path,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    file_suffix: str = "_post2018",
) -> Path:
    """Combine Round 1 and Round 2 crosswalks into final output.

    Args:
        match_folder: Folder with intermediate crosswalk files
        output_folder: Folder for final output
        min_year: Minimum year matched
        max_year: Maximum year matched
        file_suffix: Suffix used for intermediate files

    Returns:
        Path to combined crosswalk file
    """
    logger.info("Combining crosswalks into final output")

    round1_file = match_folder / f"fha_hmda_crosswalk_round1{file_suffix}.parquet"
    round2_file = match_folder / f"fha_hmda_crosswalk_round2{file_suffix}.parquet"

    df_r1 = pl.read_parquet(round1_file)
    df_r1 = df_r1.with_columns(pl.lit(1).alias("match_round"))

    if round2_file.exists():
        df_r2 = pl.read_parquet(round2_file)
        df_r2 = df_r2.with_columns(pl.lit(2).alias("match_round"))
        df = pl.concat([df_r1, df_r2], how="diagonal")
    else:
        df = df_r1

    output_folder.mkdir(parents=True, exist_ok=True)
    output_file = output_folder / f"fha_hmda_crosswalk_{min_year}_{max_year}.parquet"
    df.write_parquet(output_file)
    logger.info(f"Saved combined crosswalk ({len(df)} matches) to {output_file}")

    return output_file


# =============================================================================
# Main Pipeline Function
# =============================================================================


def run_fha_hmda_matching(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    fha_folder: Path | None = None,
    hmda_folder: Path | None = None,
    match_folder: Path | None = None,
    output_folder: Path | None = None,
    file_type_preference: list[str] | None = None,
    skip_data_prep: bool = False,
) -> Path:
    """Run the full FHA-HMDA matching pipeline.

    Args:
        min_year: Minimum year to match (default: 2018)
        max_year: Maximum year to match (default: 2024)
        fha_folder: FHA silver data folder
        hmda_folder: HMDA silver data folder
        match_folder: Intermediate match data folder
        output_folder: Final output folder
        file_type_preference: HMDA file type preference order
        skip_data_prep: Skip data preparation if intermediate files exist

    Returns:
        Path to final crosswalk file
    """
    configure_logging(level="INFO")
    logger.info(f"Starting FHA-HMDA matching pipeline (years {min_year}-{max_year})")

    if fha_folder is None:
        fha_folder = FHA_SILVER_DIR
    if hmda_folder is None:
        hmda_folder = HMDA_SILVER_DIR
    if match_folder is None:
        match_folder = INTERMEDIATE_DIR
    if output_folder is None:
        output_folder = CROSSWALK_OUTPUT_DIR

    FHAHMDAMatchingConfig.ensure_directories()

    file_suffix = "_post2018"

    # Step 1: Prepare FHA data
    fha_match_file = match_folder / f"fha_match_data{file_suffix}.parquet"
    if should_process_file(fha_match_file, overwrite=not skip_data_prep):
        prepare_fha_merge_data(fha_folder, match_folder, min_year, max_year, file_suffix)
    else:
        logger.info(f"Skipping FHA prep - file exists: {fha_match_file}")

    # Step 2: Prepare HMDA data
    hmda_match_file = match_folder / f"hmda_match_data{file_suffix}.parquet"
    if should_process_file(hmda_match_file, overwrite=not skip_data_prep):
        prepare_hmda_merge_data(
            hmda_folder, match_folder, min_year, max_year, file_suffix, file_type_preference
        )
    else:
        logger.info(f"Skipping HMDA prep - file exists: {hmda_match_file}")

    # Step 3: Round 1 matching
    r1_file = run_matching(
        match_folder,
        ROUND1_CONFIG,
        min_year,
        max_year,
        file_suffix,
        exclude_crosswalk=None,
        round_number=1,
        save_lender_crosswalk=True,
    )

    # Step 4: Round 2 matching
    run_matching(
        match_folder,
        ROUND2_CONFIG,
        min_year,
        max_year,
        file_suffix,
        exclude_crosswalk=r1_file,
        round_number=2,
        save_lender_crosswalk=False,
    )

    # Step 5: Combine crosswalks
    output_file = combine_crosswalks(
        match_folder, output_folder, min_year, max_year, file_suffix
    )

    logger.info(f"FHA-HMDA matching complete. Output: {output_file}")
    return output_file


# =============================================================================
# Main Block
# =============================================================================


if __name__ == "__main__":
    run_fha_hmda_matching()
