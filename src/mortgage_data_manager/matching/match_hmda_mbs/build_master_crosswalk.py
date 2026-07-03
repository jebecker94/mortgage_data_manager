"""Build Master Crosswalk: HMDA → FHFA → MBS → UMBS.

Creates consolidated crosswalks for FNMA and FHLMC linking HMDA loans
through FHFA, MBS, and UMBS data. Uses extended MBS matching (each year
matched against all subsequent years' MBS data).

Output files:
- data/matching/hmda_mbs/output/master_crosswalk_fnma.parquet
- data/matching/hmda_mbs/output/master_crosswalk_fhlmc.parquet

This module provides the core pipeline functions. Use the CLI for command-line access:
    mortgage-data match hmda-mbs run --agency fnma
    mortgage-data match hmda-mbs run --enrich
"""

from __future__ import annotations

import re
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSConfig

logger = get_logger(__name__)


def load_mbs_fhfa_extended(
    agency: Literal["fnma", "fhlmc"], min_year: int, max_year: int
) -> pl.LazyFrame:
    """Load and combine MBS-FHFA crosswalks from min_year through max_year.

    Each file is tagged with its year so that the join to FHFA-HMDA can match
    on both fhfa_record_id AND fhfa_year. This is critical because the same
    fhfa_record_id can map to different MBS loans in different years.
    """
    dfs = []
    for year in range(min_year, max_year + 1):
        path = Config.get_mbs_fhfa_path(agency, year)
        if path.exists():
            # Add year column to enable year-matched joins
            df = pl.scan_parquet(path).with_columns(pl.lit(year).alias("mbs_fhfa_year"))
            dfs.append(df)

    if not dfs:
        raise ValueError(f"No MBS-FHFA crosswalks found for {agency} years {min_year}-{max_year}")

    # Combine all years - no deduplication needed since we'll join on (fhfa_record_id, year)
    combined = pl.concat(dfs, how="diagonal")
    return combined


def build_crosswalk_for_year(
    agency: Literal["fnma", "fhlmc"], year: int, max_year: int
) -> pl.LazyFrame:
    """Build crosswalk for a single year using extended MBS matching."""
    config = Config.AGENCY_CONFIG[agency]
    loan_id_col = config["loan_id_col"]

    # Load FHFA-HMDA crosswalk for this agency and year
    fhfa_hmda = pl.scan_parquet(Config.FHFA_HMDA_CROSSWALK).filter(
        (pl.col("enterprise_flag") == config["enterprise_flag"]) & (pl.col("activity_year") == year)
    )

    # Load MBS-FHFA (extended: this year through max year)
    mbs_fhfa = load_mbs_fhfa_extended(agency, year, max_year)

    # Load MBS-UMBS
    mbs_umbs = pl.scan_parquet(Config.get_mbs_umbs_path(agency))

    # Join: FHFA-HMDA → MBS-FHFA (match on both record_number AND fhfa_year)
    # Critical: The same fhfa_record_id can map to different MBS loans in different years
    step1 = fhfa_hmda.join(
        mbs_fhfa,
        left_on=["record_number", "fhfa_year"],
        right_on=["fhfa_record_id", "mbs_fhfa_year"],
        how="inner",
    )

    # Join: → MBS-UMBS
    step2 = step1.join(
        mbs_umbs, left_on=loan_id_col, right_on="Loan Sequence Number (MBS)", how="inner"
    )

    # Select final columns
    crosswalk = step2.select(
        [
            "HMDAIndex",
            "activity_year",
            "record_number",
            pl.col(loan_id_col).alias("mbs_loan_id"),
            pl.col("Loan Sequence Number (UMBS)").alias("umbs_loan_id"),
            "match_round",
        ]
    )

    return crosswalk


def build_master_crosswalk(
    agency: Literal["fnma", "fhlmc"],
    min_year: int | None = None,
    max_year: int | None = None,
) -> pl.DataFrame:
    """Build master crosswalk for all years.

    Args:
        agency: Which agency to process ("fnma" or "fhlmc")
        min_year: Minimum year to process (defaults to Config.MIN_YEAR)
        max_year: Maximum year to process (defaults to Config.MAX_YEAR)

    Returns:
        DataFrame with the master crosswalk
    """
    if min_year is None:
        min_year = Config.MIN_YEAR
    if max_year is None:
        max_year = Config.MAX_YEAR

    logger.info(f"Building {agency.upper()} Master Crosswalk ({min_year}-{max_year})")

    years = list(range(min_year, max_year + 1))
    all_years = []

    for year in years:
        try:
            year_crosswalk = build_crosswalk_for_year(agency, year, max_year)
            count = year_crosswalk.select(pl.len()).collect().item()
            logger.info(f"  Processing {year}: {count:,} records")
            all_years.append(year_crosswalk)
        except Exception as e:
            logger.error(f"  Processing {year}: error: {e}")

    # Combine all years
    logger.info("  Combining all years...")
    combined = pl.concat(all_years, how="diagonal")
    df = combined.collect()

    logger.info(f"  Total records (pre-dedup): {len(df):,}")

    # Enforce strict 1:1 matching: drop ALL candidates for any ID that isn't unique
    pre_dedup = len(df)
    for col in ["HMDAIndex", "mbs_loan_id", "umbs_loan_id"]:
        dup_ids = df.group_by(col).agg(pl.len().alias("n")).filter(pl.col("n") > 1)[col]
        n_dup = len(dup_ids)
        if n_dup > 0:
            n_rows = df.filter(pl.col(col).is_in(dup_ids)).height
            logger.info(f"  Dropping {n_rows:,} rows ({n_dup:,} non-unique {col} values)")
            df = df.filter(~pl.col(col).is_in(dup_ids))

    dropped = pre_dedup - len(df)
    logger.info(f"  Total records (post-dedup): {len(df):,} ({dropped:,} dropped)")

    # Summary by year
    logger.info("  By year:")
    year_counts = df.group_by("activity_year").agg(pl.len().alias("count")).sort("activity_year")
    for row in year_counts.iter_rows(named=True):
        logger.info(f"    {row['activity_year']}: {row['count']:,}")

    return df


def normalize_name(name: str | None) -> str:
    """Normalize company name for comparison."""
    if name is None:
        return ""
    name = name.lower().strip()
    if name.startswith("the "):
        name = name[4:]
    name = re.sub(r'[,.\'"\-()&]', " ", name)
    name = re.sub(r"\bco\b", "company", name)
    name = re.sub(r"\bcorp\b", "corporation", name)
    name = re.sub(r"\binc\b", "incorporated", name)
    name = re.sub(r"\bltd\b", "limited", name)
    name = re.sub(r"\bnatl\b", "national", name)
    name = re.sub(r"\bassn\b", "association", name)
    name = re.sub(r"\bassoc\b", "association", name)
    name = re.sub(r"\bn a\b", "", name)
    name = re.sub(r"\bna\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    suffixes = [
        "llc",
        "incorporated",
        "corporation",
        "company",
        "limited",
        "bank",
        "mortgage",
        "lending",
        "financial",
        "home loans",
        "home loan",
        "services",
        "group",
        "national association",
        "association",
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if name.endswith(" " + suffix):
                name = name[: -len(suffix) - 1].strip()
                changed = True
            elif name == suffix:
                break
    return name


def enrich_crosswalk(agency: Literal["fnma", "fhlmc"], crosswalk: pl.DataFrame) -> pl.DataFrame:
    """Enrich crosswalk with HMDA loan data, LEI names, and UMBS seller info.

    Args:
        agency: Which agency ("fnma" or "fhlmc")
        crosswalk: The base crosswalk DataFrame to enrich

    Returns:
        Enriched DataFrame with additional HMDA and UMBS fields
    """
    logger.info(f"Enriching {agency.upper()} Crosswalk")

    # Load UMBS seller info from BOTH ILLD (post-2019 issuance disclosure) and the first monthly
    # snapshot (FNM_MLLD/FU) for pre-2019 loans. The two sources are split on First Payment Date
    # (ILLD >= UMBS_ISSUANCE_START, snapshot < it) so they never overlap — mirroring the combined
    # mbs_umbs matcher. Without the snapshot, pre-2019 chained loans would get NULL seller names.
    logger.info("  Loading UMBS seller info (ILLD + snapshot)...")
    illd_dir = Config.get_umbs_illd_dir(agency)
    dfs = []
    for f in sorted(illd_dir.glob("*.parquet")):
        try:
            dfs.append(pl.read_parquet(f, columns=["Loan Identifier", "Seller Name", "Channel"]))
        except Exception:
            pass
    umbs_data = (
        pl.concat(dfs, how="diagonal")
        .with_columns(pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"))
        .select(["umbs_loan_id", "Seller Name", "Channel"])
        .unique(subset=["umbs_loan_id"])
    )

    snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file(agency)
    if snapshot_file is not None and snapshot_file.exists():
        issuance_start = pl.lit(MBSUMBSConfig.UMBS_ISSUANCE_START).str.to_date()
        snap = (
            pl.scan_parquet(snapshot_file, missing_columns="insert", extra_columns="ignore")
            .with_columns(
                pl.col("First Payment Date")
                .cast(pl.String)
                .str.zfill(6)
                .str.to_date(format="%m%Y")
                .alias("_fpd")
            )
            .filter(pl.col("_fpd") < issuance_start)  # pre-ILLD loans only
            .with_columns(pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"))
            .select(["umbs_loan_id", "Seller Name", "Channel"])
            .unique(subset=["umbs_loan_id"])
            .collect()
        )
        # ILLD wins any rare collision (the two are disjoint by First Payment Date).
        umbs_data = pl.concat([umbs_data, snap], how="diagonal").unique(
            subset=["umbs_loan_id"], keep="first"
        )
        logger.info(f"    Snapshot (pre-2019) UMBS loans added: {len(snap):,}")
    logger.info(f"    Unique UMBS loans: {len(umbs_data):,}")

    # Get years present in the crosswalk
    years_in_crosswalk = sorted(crosswalk["activity_year"].unique().to_list())
    enriched_years = []

    for year in years_in_crosswalk:
        year_data = crosswalk.filter(pl.col("activity_year") == year)
        if len(year_data) == 0:
            logger.info(f"  Enriching {year}: no data")
            continue

        # Load year-matched panel
        panel = pl.read_parquet(Config.get_panel_path(year)).select(["lei", "respondent_name"])

        # Load HMDA data for this year
        file_type = Config.FILE_TYPE_BY_YEAR.get(year, "c")  # Default to 'c' for future years
        hmda = (
            pl.scan_parquet(Config.get_hmda_path(year))
            .filter(pl.col("file_type") == file_type)
            .select(["HMDAIndex", "lei", "purchaser_type"])
            .collect()
        )

        # Join
        enriched = year_data.join(hmda, on="HMDAIndex", how="left")
        enriched = enriched.join(panel, on="lei", how="left")
        enriched = enriched.join(umbs_data, on="umbs_loan_id", how="left")

        enriched_years.append(enriched)
        logger.info(f"  Enriching {year}: {len(enriched):,} records")

    # Combine
    result = pl.concat(enriched_years, how="diagonal")
    logger.info(f"  Total enriched records: {len(result):,}")

    return result


def log_summary(agency: str, df: pl.DataFrame, enriched: bool = False) -> None:
    """Log summary statistics for the crosswalk.

    Args:
        agency: Agency name for display
        df: DataFrame to summarize
        enriched: Whether the DataFrame is enriched (has additional columns)
    """
    logger.info(f"{agency.upper()} Summary")

    total = len(df)
    logger.info(f"Total records: {total:,}")

    # By year
    logger.info("Records by Year:")
    year_counts = df.group_by("activity_year").agg(pl.len().alias("count")).sort("activity_year")
    for row in year_counts.iter_rows(named=True):
        logger.info(f"  {row['activity_year']}: {row['count']:,}")

    if enriched and "Channel" in df.columns:
        # By channel
        logger.info("Records by Channel:")
        channel_counts = (
            df.group_by("Channel").agg(pl.len().alias("count")).sort("count", descending=True)
        )
        for row in channel_counts.iter_rows(named=True):
            ch = row["Channel"] or "Unknown"
            ch_name = {"R": "Retail", "C": "Correspondent", "B": "Broker"}.get(ch, ch)
            pct = row["count"] / total * 100
            logger.info(f"  {ch} ({ch_name}): {row['count']:,} ({pct:.1f}%)")

        # By purchaser_type
        if "purchaser_type" in df.columns:
            logger.info("Top Purchaser Types:")
            pt_counts = (
                df.group_by("purchaser_type")
                .agg(pl.len().alias("count"))
                .sort("count", descending=True)
            )
            for row in pt_counts.head(5).iter_rows(named=True):
                pct = row["count"] / total * 100
                logger.info(f"  {row['purchaser_type']}: {row['count']:,} ({pct:.1f}%)")
