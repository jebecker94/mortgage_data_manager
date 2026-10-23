"""Build FNMA silver layers from the bronze SF Loan-Performance quarterly parquets.

This module builds three silver datasets from the bronze quarterly parquets:

- ``silver/issuances/``: first observation of each loan
- ``silver/terminations/``: all observations carrying a zero-balance (termination)
  event
- ``silver/performance/``: the FULL monthly panel (loan x month), one parquet per
  origination-quarter bronze file (the input to the combined ``loan_performance``
  master)

All three share the ~117-col FNMA SF Loan-Performance schema and apply the same
``FNMA_SF_SILVER`` `~mortgage_data_manager.core.types.SilverSpec` (dates to
``pl.Date``, GSE code sets to ``pl.Enum``, Y/N flags to ``pl.Boolean``, ZIP to
zero-padded ``Utf8``, right-sized ints), conforming the standard/EXCL schema
variants to one target. Uses Polars lazy evaluation for memory efficiency.

`build_silver_harp` builds the same performance/issuances/terminations
decomposition for the HARP refinance population (plus its pre-HARP loan bridge)
from ``bronze/harp/``; the multifamily analog (`build_silver_multifamily`)
builds acquisitions/performance/terminations plus the annual current-DSCR
supplement from the latest ``bronze/multifamily/`` release.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema

from .config import (
    FNMA_HARP_MAPPING_SILVER,
    FNMA_MF_DSCR_SILVER,
    FNMA_MF_SILVER,
    FNMA_SF_SILVER,
)

logger = get_logger(__name__)


def process_file(
    parquet_path: Path,
    issuances_dir: Path,
    terminations_dir: Path,
) -> None:
    """Extract issuances and terminations from a single bronze parquet file.

    Args:
        parquet_path: Path to input bronze parquet file.
        issuances_dir: Directory to save issuance records.
        terminations_dir: Directory to save termination records.
    """
    logger.info("Processing %s", parquet_path.name)

    # Scan the parquet file lazily
    lf = pl.scan_parquet(parquet_path)

    # Count total rows
    row_count = lf.select(pl.len()).collect().item()
    logger.info("  Total rows: %s", f"{row_count:,}")

    # Create issuances: first row for each loan (earliest reporting period)
    # Need to convert MMYYYY to YYYYMM for proper chronological sorting
    logger.info("  Extracting issuances...")
    lf_issuances = (
        lf.select(["Loan Identifier", "Monthly Reporting Period"])
        .with_columns(
            [pl.col("Monthly Reporting Period").str.to_date(format="%m%Y").alias("_sort_date")]
        )
        .sort(by=["Loan Identifier", "_sort_date"])
        .drop("_sort_date")
        # tie-safe: selects only each loan's earliest period, which every tied row shares
        .unique(subset=["Loan Identifier"], keep="first")
    )
    lf_issuances = lf_issuances.join(
        lf, on=["Loan Identifier", "Monthly Reporting Period"], how="inner"
    )

    # Apply close-to-final silver dtypes (dates -> pl.Date, ZIP3 -> zero-padded
    # Utf8, GSE code sets -> pl.Enum, Y/N flags -> Boolean, right-sized ints) and
    # conform to the shared FNMA SF schema; then assert the output schema.
    lf_issuances = apply_silver_types(lf_issuances, FNMA_SF_SILVER)
    enforce_schema(lf_issuances.collect_schema(), FNMA_SF_SILVER.target, name="fnma/issuances")

    # Collect and save issuances
    df_issuances = lf_issuances.collect()
    issuance_count = len(df_issuances)
    output_issuances = issuances_dir / parquet_path.name
    df_issuances.write_parquet(output_issuances, compression="snappy")
    logger.info("  Saved %s issuances to %s", f"{issuance_count:,}", output_issuances.name)

    # Create terminations: every observation carrying a zero-balance (termination)
    # event. Shares the issuances/performance schema, so the same spec applies —
    # this also parses the 16 MMYYYY date columns the prior process left as raw
    # strings (the silver-audit terminations date-string bug).
    logger.info("  Extracting terminations...")
    lf_terminations = lf.filter(
        pl.col("Zero Balance Code").is_not_null() & (pl.col("Zero Balance Code") != "")
    )
    lf_terminations = apply_silver_types(lf_terminations, FNMA_SF_SILVER)
    enforce_schema(
        lf_terminations.collect_schema(), FNMA_SF_SILVER.target, name="fnma/terminations"
    )

    df_terminations = lf_terminations.collect()
    output_terminations = terminations_dir / parquet_path.name
    df_terminations.write_parquet(output_terminations, compression="snappy")
    logger.info(
        "  Saved %s terminations to %s", f"{len(df_terminations):,}", output_terminations.name
    )

    logger.info("  %s done", parquet_path.name)


def build_silver(overwrite: bool = False) -> dict[str, int]:
    """Build the silver issuances/terminations tables from bronze parquet.

    Scans every quarterly parquet in the bronze directory and writes one silver
    parquet per quarter: issuances (first observation per loan) and terminations
    (observations carrying a zero-balance event). The skip guard keys on the
    issuances output, so both tables are rebuilt together.

    Args:
        overwrite: If False, skip quarters whose issuances parquet already
            exists. If True, rebuild every quarter. Defaults to False.

    Returns:
        Counts dict with keys ``processed``, ``total_issuances``, and
        ``total_terminations``.
    """
    fnma_data = MortgageDataConfig.get_subpackage_data_dir("fnma")

    bronze_dir = fnma_data / "bronze"
    silver_dir = fnma_data / "silver"
    issuances_dir = silver_dir / "issuances"
    terminations_dir = silver_dir / "terminations"

    issuances_dir.mkdir(parents=True, exist_ok=True)
    terminations_dir.mkdir(parents=True, exist_ok=True)

    # Select bronze files whose issuances output needs (re)building.
    all_bronze_files = sorted(bronze_dir.glob("*.parquet"))
    parquet_files = [
        f for f in all_bronze_files if should_process_file(issuances_dir / f.name, overwrite)
    ]

    if not parquet_files:
        logger.warning("No parquet files found!")
        return {"processed": 0, "total_issuances": 0, "total_terminations": 0}

    logger.info("Extracting issuances and terminations from bronze files")
    logger.info("Found %s files to process:", len(parquet_files))
    for pf in parquet_files:
        logger.info("  - %s", pf.name)

    for parquet_file in parquet_files:
        try:
            process_file(parquet_file, issuances_dir, terminations_dir)
        except Exception:
            logger.exception("Failed to process %s", parquet_file.name)
            raise

    logger.info("Processing complete: %s files", len(parquet_files))
    logger.info("Input: %s", bronze_dir)
    logger.info("Output: issuances=%s, terminations=%s", issuances_dir, terminations_dir)

    total_issuances = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item()
        for f in issuances_dir.glob("*.parquet")
    )
    total_terminations = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item()
        for f in terminations_dir.glob("*.parquet")
    )

    logger.info("Total issuances: %s", f"{total_issuances:,}")
    logger.info("Total terminations: %s", f"{total_terminations:,}")

    return {
        "processed": len(parquet_files),
        "total_issuances": total_issuances,
        "total_terminations": total_terminations,
    }


def build_silver_performance(overwrite: bool = False) -> dict[str, int]:
    """Build the full FNMA SF loan-performance panel (loan x month) silver.

    Unlike `build_silver` (which keeps only the first observation per loan),
    this passes the ENTIRE monthly panel through to silver, parsing the ``MMYYYY``
    date columns to ``pl.Date``. Writes one parquet per origination-quarter bronze
    file (mirroring the bronze / issuances layout) and streams via ``sink_parquet``
    — the ~1.4B-row panel is never repartitioned or fully collected. This is the
    GSE SF-credit input to the combined ``loan_performance`` master.

    Args:
        overwrite: If False, skip quarters whose performance parquet already
            exists. If True, rebuild every quarter. Defaults to False.

    Returns:
        Counts dict with keys ``processed`` and ``total_rows``.
    """
    fnma_data = MortgageDataConfig.get_subpackage_data_dir("fnma")
    bronze_dir = fnma_data / "bronze"
    perf_dir = fnma_data / "silver" / "performance"
    perf_dir.mkdir(parents=True, exist_ok=True)

    to_process = [
        f
        for f in sorted(bronze_dir.glob("*.parquet"))
        if should_process_file(perf_dir / f.name, overwrite)
    ]
    if not to_process:
        logger.info("FNMA SF performance silver: nothing to build (all exist).")
        return {"processed": 0, "total_rows": 0}

    logger.info("Building FNMA SF performance silver from %d bronze file(s)", len(to_process))
    for f in to_process:
        lf = apply_silver_types(pl.scan_parquet(f), FNMA_SF_SILVER)
        enforce_schema(lf.collect_schema(), FNMA_SF_SILVER.target, name="fnma/performance")
        out_path = perf_dir / f.name
        lf.sink_parquet(out_path, compression="zstd")
        logger.info("  wrote %s", out_path.name)

    total_rows = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item() for f in perf_dir.glob("*.parquet")
    )
    logger.info("FNMA SF performance silver: %d file(s), %d rows", len(to_process), total_rows)
    return {"processed": len(to_process), "total_rows": total_rows}


def build_silver_harp(overwrite: bool = False) -> dict[str, int]:
    """Build the FNMA HARP silver datasets from the bronze HARP tables.

    HARP is one un-partitioned population (~92M loan-months over ~1.04M loans),
    so unlike the quarterly SF datasets these are single files in
    ``silver/harp/``:

    - ``performance.parquet``: the full loan x month panel
    - ``issuances.parquet``: first observation per loan
    - ``terminations.parquet``: observations carrying a zero-balance event
    - ``loan_mapping.parquet``: the 1:1 pre-HARP <-> HARP loan bridge

    Fannie publishes the HARP panel in the same 113-field Primary layout, so it
    applies the same ``FNMA_SF_SILVER`` spec and lands schema-identical to the
    quarterly silver. The two populations are DISJOINT (verified 2026-07-31: zero
    shared ``Loan Identifier`` values), so nothing double-counts — but equally,
    a consumer that wants HARP included has to union it in explicitly.

    Args:
        overwrite: If False, skip outputs whose parquet already exists. If True,
            rebuild everything from bronze.

    Returns:
        Counts dict with keys ``processed``, ``performance_rows``,
        ``issuances_rows``, ``terminations_rows``, and ``mapping_rows``.
    """
    fnma_data = MortgageDataConfig.get_subpackage_data_dir("fnma")
    bronze_harp = fnma_data / "bronze" / "harp"
    silver_harp = fnma_data / "silver" / "harp"

    perf_bronze = bronze_harp / "loan_performance.parquet"
    mapping_bronze = bronze_harp / "loan_mapping.parquet"
    counts = {
        "processed": 0,
        "performance_rows": 0,
        "issuances_rows": 0,
        "terminations_rows": 0,
        "mapping_rows": 0,
    }
    if not perf_bronze.exists() and not mapping_bronze.exists():
        logger.warning("No HARP bronze parquet in %s — run the bronze import first.", bronze_harp)
        return counts

    silver_harp.mkdir(parents=True, exist_ok=True)

    if perf_bronze.exists():
        logger.info("HARP silver from %s", perf_bronze.name)
        lf = apply_silver_types(pl.scan_parquet(perf_bronze), FNMA_SF_SILVER)
        enforce_schema(lf.collect_schema(), FNMA_SF_SILVER.target, name="fnma/harp")

        perf_out = silver_harp / "performance.parquet"
        if should_process_file(perf_out, overwrite):
            lf.sink_parquet(perf_out, compression="zstd")
            counts["performance_rows"] = pl.scan_parquet(perf_out).select(pl.len()).collect().item()
            counts["processed"] += 1
            logger.info("  wrote performance.parquet (%s rows)", f"{counts['performance_rows']:,}")
        else:
            logger.info("  Skipping performance.parquet (exists; --overwrite to rebuild)")

        iss_out = silver_harp / "issuances.parquet"
        if should_process_file(iss_out, overwrite):
            # group_by + semi-join rather than a window over the 92M-row panel:
            # the aggregate collapses to one row per loan before anything joins.
            first_period = lf.group_by("Loan Identifier").agg(
                pl.col("Monthly Reporting Period").min()
            )
            df_iss = (
                lf.join(
                    first_period, on=["Loan Identifier", "Monthly Reporting Period"], how="semi"
                )
                # identical first-period rows collapse; a loan whose rows disagree
                # there is excluded rather than represented by either
                .unique()
                .unique(subset=["Loan Identifier"], keep="none")
                .sort("Loan Identifier")
                .collect()
            )
            df_iss.write_parquet(iss_out, compression="snappy")
            counts["issuances_rows"] = len(df_iss)
            counts["processed"] += 1
            logger.info("  wrote issuances.parquet (%s rows)", f"{counts['issuances_rows']:,}")
        else:
            logger.info("  Skipping issuances.parquet (exists; --overwrite to rebuild)")

        term_out = silver_harp / "terminations.parquet"
        if should_process_file(term_out, overwrite):
            df_term = (
                lf.filter(
                    pl.col("Zero Balance Code").is_not_null() & (pl.col("Zero Balance Code") != "")
                )
                .sort("Loan Identifier", "Monthly Reporting Period")
                .collect()
            )
            df_term.write_parquet(term_out, compression="snappy")
            counts["terminations_rows"] = len(df_term)
            counts["processed"] += 1
            logger.info(
                "  wrote terminations.parquet (%s rows)", f"{counts['terminations_rows']:,}"
            )
        else:
            logger.info("  Skipping terminations.parquet (exists; --overwrite to rebuild)")

    if mapping_bronze.exists():
        mapping_out = silver_harp / "loan_mapping.parquet"
        if should_process_file(mapping_out, overwrite):
            lf_map = apply_silver_types(pl.scan_parquet(mapping_bronze), FNMA_HARP_MAPPING_SILVER)
            enforce_schema(
                lf_map.collect_schema(), FNMA_HARP_MAPPING_SILVER.target, name="fnma/harp_mapping"
            )
            df_map = lf_map.sort("Loan Identifier").collect()
            df_map.write_parquet(mapping_out, compression="snappy")
            counts["mapping_rows"] = len(df_map)
            counts["processed"] += 1
            logger.info("  wrote loan_mapping.parquet (%s rows)", f"{counts['mapping_rows']:,}")
        else:
            logger.info("  Skipping loan_mapping.parquet (exists; --overwrite to rebuild)")

    return counts


def _latest_mf_bronze(bronze_mf_dir: Path, prefix: str) -> Path | None:
    """Return the newest release-stamped ``{prefix}_{yyyymm}.parquet``, or None."""
    candidates = sorted(bronze_mf_dir.glob(f"{prefix}_*.parquet"))
    return candidates[-1] if candidates else None


def build_silver_multifamily(overwrite: bool = False) -> dict[str, int]:
    """Build the FNMA multifamily silver datasets from the latest bronze release.

    Reads the newest release-stamped parquets in ``bronze/multifamily/`` (each
    quarterly release restates the FULL history, so only the latest matters) and
    writes four datasets to ``silver/multifamily/``, applying ``FNMA_MF_SILVER``
    / ``FNMA_MF_DSCR_SILVER``:

    - ``performance.parquet``: the full loan x month panel
    - ``acquisitions.parquet``: first observation per loan (the MF analog of the
      SF ``issuances`` dataset — Fannie *acquires* MF loans)
    - ``terminations.parquet``: observations carrying a Liquidation/Prepayment
      Code (the MF analog of the SF zero-balance terminations)
    - ``current_dscr.parquet``: the annual loan x year DSCR supplement

    Outputs are unstamped — one canonical latest view. After dropping a new
    quarterly release and running bronze, rebuild with ``overwrite=True``. A
    handful of duplicated (loan, period) rows exist upstream (loan 1696078999);
    the panel is passed through faithfully.

    Args:
        overwrite: If False, skip datasets whose output parquet already exists.
            If True, rebuild everything from the latest bronze release.

    Returns:
        Counts dict with keys ``processed``, ``performance_rows``,
        ``acquisitions_rows``, ``terminations_rows``, and ``dscr_rows``.
    """
    fnma_data = MortgageDataConfig.get_subpackage_data_dir("fnma")
    bronze_mf = fnma_data / "bronze" / "multifamily"
    silver_mf = fnma_data / "silver" / "multifamily"

    perf_bronze = _latest_mf_bronze(bronze_mf, "loan_performance")
    dscr_bronze = _latest_mf_bronze(bronze_mf, "current_dscr")
    counts = {
        "processed": 0,
        "performance_rows": 0,
        "acquisitions_rows": 0,
        "terminations_rows": 0,
        "dscr_rows": 0,
    }
    if perf_bronze is None and dscr_bronze is None:
        logger.warning(
            "No multifamily bronze parquet in %s — run the bronze import first.", bronze_mf
        )
        return counts

    silver_mf.mkdir(parents=True, exist_ok=True)

    if perf_bronze is not None:
        logger.info("Multifamily silver from %s", perf_bronze.name)
        lf = apply_silver_types(pl.scan_parquet(perf_bronze), FNMA_MF_SILVER)
        enforce_schema(lf.collect_schema(), FNMA_MF_SILVER.target, name="fnma/multifamily")

        perf_out = silver_mf / "performance.parquet"
        if should_process_file(perf_out, overwrite):
            lf.sink_parquet(perf_out, compression="zstd")
            counts["performance_rows"] = pl.scan_parquet(perf_out).select(pl.len()).collect().item()
            counts["processed"] += 1
            logger.info("  wrote performance.parquet (%s rows)", f"{counts['performance_rows']:,}")
        else:
            logger.info("  Skipping performance.parquet (exists; --overwrite to rebuild)")

        acq_out = silver_mf / "acquisitions.parquet"
        if should_process_file(acq_out, overwrite):
            df_acq = (
                lf.filter(
                    pl.col("Reporting Period Date")
                    == pl.col("Reporting Period Date").min().over("Loan Number")
                )
                # identical first-period rows collapse; a loan whose rows disagree
                # there is excluded rather than represented by either
                .unique()
                .unique(subset=["Loan Number"], keep="none")
                .sort("Loan Number")
                .collect()
            )
            df_acq.write_parquet(acq_out, compression="snappy")
            counts["acquisitions_rows"] = len(df_acq)
            counts["processed"] += 1
            logger.info(
                "  wrote acquisitions.parquet (%s rows)", f"{counts['acquisitions_rows']:,}"
            )
        else:
            logger.info("  Skipping acquisitions.parquet (exists; --overwrite to rebuild)")

        term_out = silver_mf / "terminations.parquet"
        if should_process_file(term_out, overwrite):
            df_term = (
                lf.filter(pl.col("Liquidation/Prepayment Code").is_not_null())
                .sort("Loan Number", "Reporting Period Date")
                .collect()
            )
            df_term.write_parquet(term_out, compression="snappy")
            counts["terminations_rows"] = len(df_term)
            counts["processed"] += 1
            logger.info(
                "  wrote terminations.parquet (%s rows)", f"{counts['terminations_rows']:,}"
            )
        else:
            logger.info("  Skipping terminations.parquet (exists; --overwrite to rebuild)")

    if dscr_bronze is not None:
        dscr_out = silver_mf / "current_dscr.parquet"
        if should_process_file(dscr_out, overwrite):
            lf_dscr = apply_silver_types(pl.scan_parquet(dscr_bronze), FNMA_MF_DSCR_SILVER)
            enforce_schema(
                lf_dscr.collect_schema(), FNMA_MF_DSCR_SILVER.target, name="fnma/multifamily_dscr"
            )
            df_dscr = lf_dscr.sort("Loan Number", "Year").collect()
            df_dscr.write_parquet(dscr_out, compression="snappy")
            counts["dscr_rows"] = len(df_dscr)
            counts["processed"] += 1
            logger.info("  wrote current_dscr.parquet (%s rows)", f"{counts['dscr_rows']:,}")
        else:
            logger.info("  Skipping current_dscr.parquet (exists; --overwrite to rebuild)")

    return counts


if __name__ == "__main__":
    build_silver()
    build_silver_performance()
    build_silver_multifamily()
