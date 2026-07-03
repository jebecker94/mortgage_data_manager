"""Import functions for FHLMC silver layer data.

Builds two silver datasets from FHLMC bronze, each with its own close-to-final
dtype spec (Freddie splits the SF Loan-Level Dataset into two schemas, so unlike
FNMA there are two :class:`~mortgage_data_manager.core.types.SilverSpec`s — see
``fhlmc/config.py``):

**Origination** (``import_origination_to_silver``) — one row per
``Loan Sequence Number``, hive-partitioned by ``vintage_year``. ``FHLMC_ORIGINATION_SILVER``
null-maps the user-guide sentinels (Credit Score 9999, CLTV/DTI/LTV 999, …), casts
the closed code sets to ``pl.Enum``, Y/N flags to ``pl.Boolean``, right-sizes the
ints, and zero-pads the recovered ZIP3. Two derivations stay here (source-specific
business logic): ``vintage_year`` / ``vintage_quarter`` from the Loan Sequence
Number (``PYYQnXXXXXXX``), and Postal Code = ZIP3x100 (e.g. 600 -> ``"006"``) via
``// 100`` before the spec zero-pads it.

**Performance** (``import_performance_to_silver``) — the full monthly panel
(loan x month), one parquet per origination-quarter ``historical_data_time``
bronze file. ``FHLMC_PERFORMANCE_SILVER`` parses the new ELTV 999 sentinel, casts
Net Sales Proceeds (numeric strings) to Float64, right-sizes ints, and enums the
flags; the date columns already arrive as ``pl.Date`` from bronze. Streams via
``sink_parquet`` (the ~1.5B-row panel is never fully collected). This is the input
the combined ``loan_performance`` master reads (parallel to the FNMA SF perf silver).

Bronze column names (with spaces) are preserved so existing matching code
that reads bronze can be repointed at silver without renaming.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
from mortgage_data_manager.fhlmc.config import (
    FHLMC_ORIGINATION_SILVER,
    FHLMC_PERFORMANCE_SILVER,
    FHLMCConfig,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------


def clean_origination_lf(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply silver-layer transformations to a bronze origination LazyFrame.

    Derives the two source-specific columns (``vintage_year`` / ``vintage_quarter``
    from the loan sequence number, and the ZIP3 from Freddie's ZIP3x100 Postal
    Code), then applies the declarative ``FHLMC_ORIGINATION_SILVER`` spec (sentinel
    null-mapping, enums, booleans, ZIP zero-padding, int right-sizing, conform to
    target). Duplicate Loan Sequence Numbers (which should not exist in origination
    files but may appear if bronze files overlap) are deduplicated keeping the
    first row.

    Args:
        lf: LazyFrame scanning one or more bronze origination parquet files.

    Returns:
        LazyFrame conformed to the ``FHLMC_ORIGINATION_SILVER`` target schema.
    """
    # vintage_year / vintage_quarter from Loan Sequence Number (PYYQnXXXXXXX).
    # Dataset starts in 1999; YY in [80..99] -> 19YY, else 20YY.
    yy_expr = pl.col("Loan Sequence Number").str.slice(1, 2).cast(pl.Int64)
    lf = lf.with_columns(
        [
            pl.when(yy_expr >= 80)
            .then(1900 + yy_expr)
            .otherwise(2000 + yy_expr)
            .cast(pl.Int32)
            .alias("vintage_year"),
            pl.col("Loan Sequence Number").str.slice(4, 1).cast(pl.Int8).alias("vintage_quarter"),
            # Postal Code is the first 3 ZIP digits stored as ZIP3 x 100 (e.g.
            # 600 = "006", 99900 = "999"); recover ZIP3 here, the spec zero-pads
            # it to 3 chars (recovering the leading zeros an int storage drops).
            (pl.col("Postal Code") // 100).alias("Postal Code"),
        ]
    )

    # Sentinel null-mapping, enums, booleans, ZIP zero-pad, int right-sizing,
    # conform to the FHLMC origination target schema.
    lf = apply_silver_types(lf, FHLMC_ORIGINATION_SILVER)

    # Deduplicate on Loan Sequence Number (origination files should already
    # be unique, but guard against bronze file overlap). Sort afterwards
    # because unique() does not preserve order — without a sort, snappy
    # loses ~13% compression on the year file vs the quarterly bronze.
    lf = lf.unique(subset=["Loan Sequence Number"], keep="first")
    lf = lf.sort("Loan Sequence Number")

    return lf


# ---------------------------------------------------------------------------
# Year-by-year driver
# ---------------------------------------------------------------------------


def _bronze_files_for_year(bronze_dir: Path, year: int) -> list[Path]:
    """Return the quarterly bronze origination files for a given vintage year."""
    return sorted(bronze_dir.glob(f"historical_data_{year}Q*.parquet"))


def import_origination_to_silver(
    years: Iterable[int] | None = None,
    bronze_dir: Path | None = None,
    silver_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Build the FHLMC silver origination layer year-by-year.

    For each requested vintage year, scans the corresponding bronze
    quarterly files, applies sentinel cleanup, adds categorical label
    columns, derives ``vintage_year`` / ``vintage_quarter``, deduplicates
    by Loan Sequence Number, and writes a single parquet under
    ``silver/origination/vintage_year=YYYY/data.parquet``.

    Args:
        years: Vintage years to process. Defaults to every year that has
            at least one bronze quarterly file.
        bronze_dir: Bronze origination directory. Defaults to
            ``FHLMCConfig.FHLMC_BRONZE_ORIGINATION``.
        silver_dir: Silver origination directory (parent of the
            ``vintage_year=YYYY`` partitions). Defaults to
            ``FHLMCConfig.FHLMC_SILVER_DIR / 'origination'``.
        overwrite: If True, rebuild partitions even when the output file
            already exists. If False (default), skip already-built years.

    Returns:
        Dict with counts: ``processed``, ``skipped``, ``failed``.
    """
    bronze_dir = bronze_dir or FHLMCConfig.FHLMC_BRONZE_ORIGINATION
    silver_dir = silver_dir or (FHLMCConfig.FHLMC_SILVER_DIR / "origination")

    if years is None:
        # Discover years from bronze filenames: historical_data_YYYYQn.parquet
        discovered = set()
        for f in bronze_dir.glob("historical_data_*Q*.parquet"):
            try:
                discovered.add(int(f.stem.split("_")[2][:4]))
            except (IndexError, ValueError):
                continue
        years = sorted(discovered)

    silver_dir.mkdir(parents=True, exist_ok=True)

    counts = {"processed": 0, "skipped": 0, "failed": 0}

    for year in sorted(set(years)):
        partition_dir = silver_dir / f"vintage_year={year}"
        output_file = partition_dir / "data.parquet"

        if not should_process_file(output_file, overwrite=overwrite):
            logger.info(f"Skipping {year} (silver exists at {output_file})")
            counts["skipped"] += 1
            continue

        bronze_files = _bronze_files_for_year(bronze_dir, year)
        if not bronze_files:
            logger.warning(f"No bronze files found for {year} in {bronze_dir}")
            counts["failed"] += 1
            continue

        logger.info(
            f"Processing {year} ({len(bronze_files)} bronze quarterly file(s)) -> {output_file}"
        )

        try:
            lf = pl.scan_parquet(bronze_files)
            lf = clean_origination_lf(lf)
            enforce_schema(
                lf.collect_schema(), FHLMC_ORIGINATION_SILVER.target, name="fhlmc/origination"
            )
            partition_dir.mkdir(parents=True, exist_ok=True)
            lf.sink_parquet(
                output_file,
                compression=FHLMCConfig.PARQUET_COMPRESSION,
                statistics=FHLMCConfig.PARQUET_STATISTICS,
                row_group_size=FHLMCConfig.PARQUET_ROW_GROUP_SIZE,
            )
            counts["processed"] += 1
            logger.info(f"  Saved {output_file}")
        except Exception as e:
            logger.exception(f"Failed to process {year}: {e}")
            counts["failed"] += 1

    return counts


def import_performance_to_silver(
    bronze_dir: Path | None = None,
    silver_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Build the FHLMC SF loan-performance panel (loan x month) silver.

    Applies ``FHLMC_PERFORMANCE_SILVER`` to each ``historical_data_time_YYYYQn``
    bronze quarterly file and writes a matching silver parquet (one file per
    origination quarter): the ELTV 999 sentinel is null-mapped, Net Sales Proceeds
    (numeric strings) is cast to Float64, ints are right-sized, and the flags are
    enumerated/booled. The bronze import already types the date columns (Monthly
    Reporting Period, Zero Balance Effective Date, DDLPI, Defect Settlement Date)
    as ``pl.Date``. Streams via ``sink_parquet`` — the ~1.5B-row panel is never
    repartitioned or fully collected. This is the silver the combined
    ``loan_performance`` builder reads.

    Args:
        bronze_dir: Bronze performance directory. Defaults to
            ``FHLMCConfig.FHLMC_BRONZE_PERFORMANCE``.
        silver_dir: Silver performance directory. Defaults to
            ``FHLMCConfig.FHLMC_SILVER_DIR / 'performance'``.
        overwrite: If True, rebuild existing quarters; else (default) skip them.

    Returns:
        Dict with counts: ``processed``, ``skipped``, ``total_rows``.
    """
    bronze_dir = bronze_dir or FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
    silver_dir = silver_dir or (FHLMCConfig.FHLMC_SILVER_DIR / "performance")
    silver_dir.mkdir(parents=True, exist_ok=True)

    counts = {"processed": 0, "skipped": 0, "total_rows": 0}
    for bronze_file in sorted(bronze_dir.glob("historical_data_time_*.parquet")):
        out_file = silver_dir / bronze_file.name
        if not should_process_file(out_file, overwrite=overwrite):
            counts["skipped"] += 1
            continue
        logger.info(f"Processing {bronze_file.name} -> {out_file.name}")
        lf = apply_silver_types(pl.scan_parquet(bronze_file), FHLMC_PERFORMANCE_SILVER)
        enforce_schema(
            lf.collect_schema(), FHLMC_PERFORMANCE_SILVER.target, name="fhlmc/performance"
        )
        lf.sink_parquet(
            out_file,
            compression=FHLMCConfig.PARQUET_COMPRESSION,
            statistics=FHLMCConfig.PARQUET_STATISTICS,
            row_group_size=FHLMCConfig.PARQUET_ROW_GROUP_SIZE,
        )
        counts["processed"] += 1

    counts["total_rows"] = sum(
        pl.scan_parquet(f).select(pl.len()).collect().item()
        for f in silver_dir.glob("historical_data_time_*.parquet")
    )
    logger.info(
        f"FHLMC performance silver: processed={counts['processed']} "
        f"skipped={counts['skipped']} total_rows={counts['total_rows']:,}"
    )
    return counts
