"""Build FNMA silver layers from the bronze SF Loan-Performance quarterly parquets.

This module builds three silver datasets from the bronze quarterly parquets:

- ``silver/issuances/``: first observation of each loan
- ``silver/terminations/``: all observations carrying a zero-balance (termination)
  event
- ``silver/performance/``: the FULL monthly panel (loan x month), one parquet per
  origination-quarter bronze file (the input to the combined ``loan_performance``
  master)

All three share the ~117-col FNMA SF Loan-Performance schema and apply the same
``FNMA_SF_SILVER`` :class:`~mortgage_data_manager.core.types.SilverSpec` (dates to
``pl.Date``, GSE code sets to ``pl.Enum``, Y/N flags to ``pl.Boolean``, ZIP to
zero-padded ``Utf8``, right-sized ints), conforming the standard/EXCL schema
variants to one target. Uses Polars lazy evaluation for memory efficiency.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema

from .config import FNMA_SF_SILVER

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
    logger.info(f"Processing {parquet_path.name}")

    # Scan the parquet file lazily
    lf = pl.scan_parquet(parquet_path)

    # Count total rows
    row_count = lf.select(pl.len()).collect().item()
    logger.info(f"  Total rows: {row_count:,}")

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
    logger.info(f"  Saved {issuance_count:,} issuances to {output_issuances.name}")

    # Create terminations: every observation carrying a zero-balance (termination)
    # event. Shares the issuances/performance schema, so the same spec applies —
    # this also parses the 16 MMYYYY date columns the prior process left as raw
    # strings (the silver-audit terminations date-string bug).
    logger.info("  Extracting terminations...")
    lf_terminations = lf.filter(
        pl.col("Zero Balance Code").is_not_null() & (pl.col("Zero Balance Code") != "")
    )
    lf_terminations = apply_silver_types(lf_terminations, FNMA_SF_SILVER)
    enforce_schema(lf_terminations.collect_schema(), FNMA_SF_SILVER.target, name="fnma/terminations")

    df_terminations = lf_terminations.collect()
    output_terminations = terminations_dir / parquet_path.name
    df_terminations.write_parquet(output_terminations, compression="snappy")
    logger.info(f"  Saved {len(df_terminations):,} terminations to {output_terminations.name}")

    logger.info(f"  {parquet_path.name} done")


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
    logger.info(f"Found {len(parquet_files)} files to process:")
    for pf in parquet_files:
        logger.info(f"  - {pf.name}")

    for parquet_file in parquet_files:
        try:
            process_file(parquet_file, issuances_dir, terminations_dir)
        except Exception:
            logger.exception(f"Failed to process {parquet_file.name}")
            raise

    logger.info(f"Processing complete: {len(parquet_files)} files")
    logger.info(f"Input: {bronze_dir}")
    logger.info(f"Output: issuances={issuances_dir}, terminations={terminations_dir}")

    total_issuances = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item()
        for f in issuances_dir.glob("*.parquet")
    )
    total_terminations = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item()
        for f in terminations_dir.glob("*.parquet")
    )

    logger.info(f"Total issuances: {total_issuances:,}")
    logger.info(f"Total terminations: {total_terminations:,}")

    return {
        "processed": len(parquet_files),
        "total_issuances": total_issuances,
        "total_terminations": total_terminations,
    }


def build_silver_performance(overwrite: bool = False) -> dict[str, int]:
    """Build the full FNMA SF loan-performance panel (loan x month) silver.

    Unlike :func:`build_silver` (which keeps only the first observation per loan),
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


if __name__ == "__main__":
    build_silver()
    build_silver_performance()
