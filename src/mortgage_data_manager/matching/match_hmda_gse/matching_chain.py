"""Build Master Crosswalk: HMDA → FHFA → MBS → UMBS.

Creates consolidated crosswalks for FNMA and FHLMC linking HMDA loans
through FHFA, MBS, and UMBS data. Uses extended MBS matching (each year
matched against all subsequent years' MBS data).

Output files (written by the CLI to ``HMDAGSEMatchingConfig.HMDA_GSE_CROSSWALK_DIR``,
i.e. ``crosswalk/hmda_gse/``):
- hmda_gse_crosswalk_chain_fnma.parquet
- hmda_gse_crosswalk_chain_fhlmc.parquet

This module provides the core pipeline functions. Use the CLI for command-line access:
    mortgage-data match hmda-gse run --route chain --agency fnma
    mortgage-data match hmda-gse run --route chain --enrich
"""

from __future__ import annotations

from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_gse.config import HMDAGSEMatchingConfig as Config
from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig
from mortgage_data_manager.sources.hmda.load import scan_hmda_for_index

logger = get_logger(__name__)


def load_fhfa_mbs_extended(
    agency: Literal["fnma", "fhlmc"], min_year: int, max_year: int
) -> pl.LazyFrame:
    """Load and combine FHFA-MBS crosswalks from min_year through max_year.

    Each file is tagged with its year so that the join to HMDA-FHFA can match
    on both fhfa_record_id AND fhfa_year. This is critical because the same
    fhfa_record_id can map to different MBS loans in different years.

    Args:
        agency: Which GSE to load.
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.

    Returns:
        The combined crosswalks, each row tagged with its source year.
    """
    dfs = []
    for year in range(min_year, max_year + 1):
        path = Config.get_fhfa_mbs_path(agency, year)
        if path.exists():
            # Add year column to enable year-matched joins
            df = pl.scan_parquet(path).with_columns(pl.lit(year).alias("mbs_fhfa_year"))
            dfs.append(df)

    if not dfs:
        raise ValueError(f"No FHFA-MBS crosswalks found for {agency} years {min_year}-{max_year}")

    # Combine all years - no deduplication needed since we'll join on (fhfa_record_id, year)
    combined = pl.concat(dfs, how="diagonal")
    return combined


def build_crosswalk_for_year(
    agency: Literal["fnma", "fhlmc"], year: int, max_year: int
) -> pl.LazyFrame:
    """Build crosswalk for a single year using extended MBS matching.

    Args:
        agency: Which GSE to load.
        year: Activity year to build.
        max_year: Last activity year to include, inclusive.

    Returns:
        That year's crosswalk rows.
    """
    config = Config.AGENCY_CONFIG[agency]
    loan_id_col = config["loan_id_col"]

    # Load HMDA-FHFA crosswalk for this agency and year
    hmda_fhfa = pl.scan_parquet(Config.HMDA_FHFA_CROSSWALK).filter(
        (pl.col("enterprise_flag") == config["enterprise_flag"]) & (pl.col("activity_year") == year)
    )

    # Load FHFA-MBS (extended: this year through max year)
    fhfa_mbs = load_fhfa_mbs_extended(agency, year, max_year)

    # Load MBS-UMBS
    mbs_umbs = pl.scan_parquet(Config.get_mbs_umbs_path(agency)).select(
        "Loan Sequence Number (MBS)", "Loan Sequence Number (UMBS)"
    )

    # Join: HMDA-FHFA → FHFA-MBS (match on both record_number AND fhfa_year)
    # Critical: The same fhfa_record_id can map to different MBS loans in different years
    step1 = hmda_fhfa.join(
        fhfa_mbs,
        left_on=["record_number", "fhfa_year"],
        right_on=["fhfa_record_id", "mbs_fhfa_year"],
        how="inner",
    )

    # Join: → MBS-UMBS. LEFT join so a loan-level (SF LLD) pin survives even
    # when the UMBS hop is unavailable — pre-2019-06 terminations never appear
    # in the UMBS files (2018-vintage survivorship), and downstream in_mbs /
    # in_umbs must decompose independently (an inner join would make
    # in_mbs ⇔ in_umbs in master_combined).
    step2 = step1.join(
        mbs_umbs, left_on=loan_id_col, right_on="Loan Sequence Number (MBS)", how="left"
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


def _require_year_coverage(agency: Literal["fnma", "fhlmc"], years: list[int]) -> None:
    """Raise unless both input crosswalks cover every requested HMDA year."""
    flag = Config.AGENCY_CONFIG[agency]["enterprise_flag"]
    fhfa_years = set(
        pl.scan_parquet(Config.HMDA_FHFA_CROSSWALK)
        .filter(pl.col("enterprise_flag") == flag)
        .select(pl.col("activity_year").unique())
        .collect()
        .to_series()
        .to_list()
    )
    gaps = {
        "hmda_fhfa": [y for y in years if y not in fhfa_years],
        "fhfa_mbs": [y for y in years if not Config.get_fhfa_mbs_path(agency, y).exists()],
    }
    missing = {name: ys for name, ys in gaps.items() if ys}
    if missing:
        raise ValueError(
            f"{agency} master: input crosswalks lack HMDA years {missing}; "
            "build them or narrow the year range"
        )


def run_matching_chain(
    agency: Literal["fnma", "fhlmc"],
    min_year: int | None = None,
    max_year: int | None = None,
) -> pl.DataFrame:
    """Build master crosswalk for all years.

    Every year in the range must be present: a year missing from either input
    crosswalk, or one that yields no rows, stops the build rather than writing a
    master without it.

    Args:
        agency: Which agency to process ("fnma" or "fhlmc")
        min_year: Minimum year to process (defaults to Config.MIN_YEAR)
        max_year: Maximum year to process (defaults to Config.MAX_YEAR)

    Returns:
        DataFrame with the master crosswalk

    Raises:
        ValueError: If any requested HMDA year is missing or empty.
    """
    if min_year is None:
        min_year = Config.MIN_YEAR
    if max_year is None:
        max_year = Config.MAX_YEAR

    logger.info("Building %s Master Crosswalk (%s-%s)", agency.upper(), min_year, max_year)

    years = list(range(min_year, max_year + 1))
    _require_year_coverage(agency, years)
    all_years = []

    for year in years:
        year_crosswalk = build_crosswalk_for_year(agency, year, max_year)
        count = year_crosswalk.select(pl.len()).collect().item()
        logger.info("  Processing %s: %s records", year, f"{count:,}")
        if count == 0:
            raise ValueError(f"{agency} master: HMDA year {year} produced no rows")
        all_years.append(year_crosswalk)

    # Combine all years
    logger.info("  Combining all years...")
    combined = pl.concat(all_years, how="diagonal")
    df = combined.collect()

    logger.info("  Total records (pre-dedup): %s", f"{len(df):,}")

    # Enforce strict 1:1 matching: drop ALL candidates for any ID that isn't
    # unique. Null umbs_loan_id (loan-level pin without a UMBS hop) is not an
    # identity — exclude nulls from dup detection AND keep null rows when
    # filtering (`is_in` is null-propagating, so a bare ~is_in would silently
    # drop every null row).
    pre_dedup = len(df)
    for col in ["HMDAIndex", "mbs_loan_id", "umbs_loan_id"]:
        dup_ids = (
            df.drop_nulls(col).group_by(col).agg(pl.len().alias("n")).filter(pl.col("n") > 1)[col]
        )
        n_dup = len(dup_ids)
        if n_dup > 0:
            n_rows = df.filter(pl.col(col).is_in(dup_ids).fill_null(value=False)).height
            logger.info(
                "  Dropping %s rows (%s non-unique %s values)", f"{n_rows:,}", f"{n_dup:,}", col
            )
            df = df.filter(~pl.col(col).is_in(dup_ids).fill_null(value=False))

    dropped = pre_dedup - len(df)
    logger.info("  Total records (post-dedup): %s (%s dropped)", f"{len(df):,}", f"{dropped:,}")

    # Summary by year
    logger.info("  By year:")
    year_counts = df.group_by("activity_year").agg(pl.len().alias("count")).sort("activity_year")
    for row in year_counts.iter_rows(named=True):
        logger.info("    %s: %s", row["activity_year"], f"{row['count']:,}")

    return df


def _agreed(col: str) -> pl.Expr:
    """``col``'s value when a loan's rows carry exactly one distinct non-null value, else null."""
    values = pl.col(col).drop_nulls()
    # tie-safe: taken only when the loan's rows carry one distinct non-null value
    return pl.when(values.n_unique() == 1).then(values.first()).otherwise(None).alias(col)


def _one_enrichment_row(df: pl.DataFrame) -> pl.DataFrame:
    """One ``(Seller Name, Channel)`` row per ``umbs_loan_id``; a disagreed attribute is null."""
    return df.group_by("umbs_loan_id", maintain_order=True).agg(
        _agreed("Seller Name"), _agreed("Channel")
    )


def enrich_crosswalk(agency: Literal["fnma", "fhlmc"], crosswalk: pl.DataFrame) -> pl.DataFrame:
    """Enrich crosswalk with HMDA loan data, LEI names, and UMBS seller info.

    Args:
        agency: Which agency ("fnma" or "fhlmc")
        crosswalk: The base crosswalk DataFrame to enrich

    Returns:
        Enriched DataFrame with additional HMDA and UMBS fields
    """
    logger.info("Enriching %s Crosswalk", agency.upper())

    # Load UMBS seller info from BOTH ILLD (post-2019 issuance disclosure) and the first monthly
    # snapshot (FNM_MLLD/FU). The snapshot contributes every loan NOT already in ILLD (anti-join,
    # ILLD preferred) — the pre-2019 population plus the 2019-H2 seam cohort (First Payment Date
    # >= 2019-06 in pools issued before June 2019) — mirroring the mbs_umbs matcher's universe.
    # Without the snapshot those chained loans would get NULL seller names.
    logger.info("  Loading UMBS seller info (ILLD + snapshot)...")
    illd_dir = Config.get_umbs_illd_dir(agency)
    dfs = [
        pl.read_parquet(f, columns=["Loan Identifier", "Seller Name", "Channel"])
        for f in sorted(illd_dir.glob("*.parquet"))
    ]
    # A loan recurs across ILLD months; an attribute its rows disagree on is left null
    # rather than taken from whichever month-row comes first.
    umbs_data = _one_enrichment_row(
        pl.concat(dfs, how="diagonal")
        .with_columns(pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"))
        .select(["umbs_loan_id", "Seller Name", "Channel"])
    )

    snapshot_file = MBSUMBSMatchingConfig.get_umbs_snapshot_file(agency)
    if snapshot_file is not None and snapshot_file.exists():
        snap = _one_enrichment_row(
            pl.scan_parquet(snapshot_file, missing_columns="insert", extra_columns="ignore")
            # Snapshot silver types Channel as an Enum; ILLD bronze keeps it a string.
            .with_columns(
                pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"),
                pl.col("Channel").cast(pl.Utf8),
            )
            .select(["umbs_loan_id", "Seller Name", "Channel"])
            .join(umbs_data.lazy().select("umbs_loan_id"), on="umbs_loan_id", how="anti")
            .collect()
        )
        umbs_data = pl.concat([umbs_data, snap], how="diagonal")
        logger.info("    Snapshot-only UMBS loans added: %s", f"{len(snap):,}")
    logger.info("    Unique UMBS loans: %s", f"{len(umbs_data):,}")

    # Get years present in the crosswalk
    years_in_crosswalk = sorted(crosswalk["activity_year"].unique().to_list())
    enriched_years = []

    for year in years_in_crosswalk:
        year_data = crosswalk.filter(pl.col("activity_year") == year)
        if len(year_data) == 0:
            logger.info("  Enriching %s: no data", year)
            continue

        # Load year-matched panel
        panel = pl.read_parquet(Config.get_panel_path(year)).select(["lei", "respondent_name"])

        # Load HMDA for this year from the release the crosswalk's indices name
        hmda = (
            scan_hmda_for_index(year_data, "post2018", root=Config.HMDA_SILVER_DIR)
            .select(["HMDAIndex", "lei", "purchaser_type"])
            .collect()
        )

        # Join
        enriched = year_data.join(hmda, on="HMDAIndex", how="left")
        enriched = enriched.join(panel, on="lei", how="left")
        enriched = enriched.join(umbs_data, on="umbs_loan_id", how="left")

        enriched_years.append(enriched)
        logger.info("  Enriching %s: %s records", year, f"{len(enriched):,}")

    # Combine
    result = pl.concat(enriched_years, how="diagonal")
    logger.info("  Total enriched records: %s", f"{len(result):,}")

    return result
