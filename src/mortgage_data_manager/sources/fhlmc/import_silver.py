"""Import functions for FHLMC silver layer data.

Builds two silver datasets from FHLMC bronze, each with its own close-to-final
dtype spec (Freddie splits the SF Loan-Level Dataset into two schemas, so unlike
FNMA there are two `~mortgage_data_manager.core.types.SilverSpec`s — see
``fhlmc/config.py``):

**Origination** (``import_origination_to_silver``) — one row per
``Loan Sequence Number``, hive-partitioned by ``vintage_year``. ``FHLMC_ORIGINATION_SILVER``
null-maps the user-guide sentinels (Credit Score 9999, CLTV/DTI/LTV 999, …), casts
the closed code sets to ``pl.Enum``, Y/N flags to ``pl.Boolean``, right-sizes the
ints, and zero-pads the recovered ZIP3. ``vintage_year`` / ``vintage_quarter`` are
derived here from the Loan Sequence Number (``PYYQnXXXXXXX``); the pre-R47 Postal
Code = ZIP3x100 encoding (e.g. 600 -> ``"006"``) is recovered per file in
`scan_origination_bronze`, since R47 already discloses a 3-digit ZIP.

**Performance** (``import_performance_to_silver``) — the full monthly panel
(loan x month), one parquet per origination-quarter ``historical_data_time``
bronze file. ``FHLMC_PERFORMANCE_SILVER`` parses the new ELTV 999 sentinel, casts
Net Sales Proceeds (numeric strings) to Float64, right-sizes ints, and enums the
flags; the date columns already arrive as ``pl.Date`` from bronze. Streams via
``sink_parquet`` (the ~1.5B-row panel is never fully collected). This is the input
the combined ``loan_performance`` master reads (parallel to the FNMA SF perf silver).

Freddie rounds ``Current Actual UPB`` to the nearest $1,000 in a loan's first
months: at ``Loan Age`` 0-6, about 99% of positive balances are exact multiples of
$1,000, against about 0.05% from age 7 on (measured on the 2010Q1 and 2022Q1
panels). Silver keeps the published values, so an exact-dollar balance comparison
against another source fails for young loans; compare those within $1,000.

Bronze column names (with spaces) are preserved so existing matching code
that reads bronze can be repointed at silver without renaming. Since Freddie's
Release 47 relayout (July 2026) that means the *pre-R47* names: bronze stores
whichever vocabulary its source file used, and `scan_origination_bronze` /
`scan_performance_bronze` normalize R47 files back to the established silver
names. Those two are the canonical way to read FHLMC bronze — code that scans the
parquet directly sees whichever era wrote the file. See the Release 47 notes in
``schemas.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import (
    atomic_output,
    should_process_file,
)
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
from mortgage_data_manager.sources.fhlmc.config import (
    FHLMC_ORIGINATION_SILVER,
    FHLMC_PERFORMANCE_SILVER,
    FHLMCConfig,
)
from mortgage_data_manager.sources.fhlmc.schemas import (
    R47_MOVED_OFF_ORIGINATION,
    R47_ORIGINATION_RENAMES,
    R47_PERFORMANCE_RENAMES,
    is_r47_origination,
    is_r47_performance,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------


def scan_origination_bronze(path: Path) -> pl.LazyFrame:
    """Scan one bronze origination parquet, normalizing R47 to the silver vocabulary.

    Silver keeps the pre-R47 column names so existing consumers keep working
    (see the Release 47 notes in ``schemas.py``). For an R47-layout file this
    renames the ~7 changed attributes and adds typed nulls for the two
    attributes R47 moved into the performance file.

    Postal Code is reconciled to one representation across both eras: Int64
    holding a bare ZIP3. Pre-R47 stores it as ZIP3x100 (``51100``), so the
    ``// 100`` recovery happens here rather than in the caller; R47 stores the
    3-digit code as a *string* (``"511"``, ``"000"`` = Unknown), so it is cast.
    Without that reconciliation the two eras differ in both dtype and scale,
    which silently breaks the ``Postal Code > 0`` geography guards downstream.

    Args:
        path: Bronze origination parquet.

    Returns:
        LazyFrame with pre-R47 column names and ``Postal Code`` as Int64 ZIP3.
    """
    lf = pl.scan_parquet(path)
    names = lf.collect_schema().names()

    if not is_r47_origination(names):
        # Pre-R47: Postal Code is ZIP3x100, recover the ZIP3 the spec zero-pads.
        return lf.with_columns((pl.col("Postal Code") // 100).alias("Postal Code"))

    lf = lf.rename({k: v for k, v in R47_ORIGINATION_RENAMES.items() if k in names})
    lf = lf.with_columns(pl.col("Postal Code").cast(pl.Int64, strict=False))
    # Servicer Name / MI Cancellation Indicator are no longer disclosed at the
    # origination grain — they now vary by month in the performance panel.
    lf = lf.with_columns(
        [pl.lit(None, dtype=pl.String).alias(c) for c in R47_MOVED_OFF_ORIGINATION]
    )
    return lf


def scan_performance_bronze(path: Path) -> pl.LazyFrame:
    """Scan one bronze performance parquet, normalizing R47 to the silver vocabulary.

    Renames the ~12 changed attributes back to the pre-R47 names, and restores
    the established ``Current Loan Delinquency Status`` encoding: R47 zero-pads
    it to two characters (``"00"``, ``"01"``, capped at 99) where the pre-R47
    files — and therefore every existing consumer, including the ``event_class``
    taxonomy — use the unpadded form (``"0"``,
    ``"1"``). Non-numeric levels such as ``"RA"`` are left untouched. The three
    attributes R47 appended (Servicer Name, Mortgage Insurance Cancellation
    Indicator, Bankruptcy Cramdown Costs) keep their R47 names.

    Args:
        path: Bronze performance parquet.

    Returns:
        LazyFrame with pre-R47 column names and delinquency-status encoding.
    """
    lf = pl.scan_parquet(path)
    names = lf.collect_schema().names()

    if not is_r47_performance(names):
        return lf

    lf = lf.rename({k: v for k, v in R47_PERFORMANCE_RENAMES.items() if k in names})
    status = pl.col("Current Loan Delinquency Status")
    return lf.with_columns(
        pl.when(status.str.contains(r"^\d+$"))
        .then(status.str.strip_chars_start("0").replace("", "0"))
        .otherwise(status)
        .alias("Current Loan Delinquency Status")
    )


def merge_duplicate_rows(lf: pl.LazyFrame, key: str, *, exclude_conflicts: bool) -> pl.LazyFrame:
    """Merge rows that share ``key`` into one row per key.

    A field one copy leaves null takes the other copy's value, because absence is
    not a disagreement (the ``excl`` release lacks several columns of the standard
    layout). A field the copies fill with two different values is never resolved by
    picking a copy: the key keeps the row with that field null, or -- for a matcher,
    where a disputed compared field must not be matched on -- the key is dropped.

    Args:
        lf: Frame that may hold several rows per ``key``.
        key: Column identifying the entity.
        exclude_conflicts: Drop a key with any disputed field instead of nulling
            the disputed fields.

    Returns:
        One row per ``key``, in the input's column order.
    """
    names = lf.collect_schema().names()
    fields = [c for c in names if c != key]
    copies = pl.len().over(key)
    values = (
        lf.filter(copies > 1)
        .group_by(key)
        .agg([pl.col(c).drop_nulls().unique().alias(c) for c in fields])
    )
    if exclude_conflicts:
        values = values.filter(pl.all_horizontal([pl.col(c).list.len() <= 1 for c in fields]))
    merged = values.with_columns(
        # tie-safe: a value is taken only when the copies hold exactly one distinct value
        [pl.when(pl.col(c).list.len() == 1).then(pl.col(c).list.first()).alias(c) for c in fields]
    ).select(names)
    return pl.concat([lf.filter(copies == 1), merged], how="vertical")


def clean_origination_lf(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply silver-layer transformations to a bronze origination LazyFrame.

    Derives ``vintage_year`` / ``vintage_quarter`` from the loan sequence number,
    then applies the declarative ``FHLMC_ORIGINATION_SILVER`` spec (sentinel
    null-mapping, enums, booleans, ZIP zero-padding, int right-sizing, conform to
    target). A loan published in both the standard and the non-standard (``excl``)
    release appears twice; `merge_duplicate_rows` fills one copy's gaps from the
    other and nulls any field the two copies fill with different values, keeping
    the loan.

    Args:
        lf: LazyFrame over bronze origination files already normalized by
            `scan_origination_bronze` (pre-R47 column names, Postal Code
            reduced to ZIP3).

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
        ]
    )

    # Sentinel null-mapping, enums, booleans, ZIP zero-pad, int right-sizing,
    # conform to the FHLMC origination target schema.
    lf = apply_silver_types(lf, FHLMC_ORIGINATION_SILVER)

    # One row per Loan Sequence Number. Sort afterwards because the merge does not
    # preserve order -- without a sort, snappy loses ~13% compression on the year
    # file vs the quarterly bronze.
    lf = merge_duplicate_rows(lf, "Loan Sequence Number", exclude_conflicts=False)
    lf = lf.sort("Loan Sequence Number")

    return lf


# ---------------------------------------------------------------------------
# Year-by-year driver
# ---------------------------------------------------------------------------


def _bronze_files_for_year(bronze_dir: Path, year: int) -> list[Path]:
    """Return the quarterly bronze origination files for a given vintage year.

    Includes BOTH the standard set (``historical_data_YYYYQn``) and the non-standard
    ``excl`` set (``historical_data_excl_YYYYQn``). The excl files are not an ARM dump —
    they are ~61% fixed-rate, and they carry the high-LTV population (LTV > 97) that the
    standard SF LLD omits entirely. Both can back a UMBS security, so dropping them
    silently shrank every downstream consumer's universe.

    Returns:
        The quarterly bronze origination files for that vintage year, standard
        and ``excl`` sets together.

    The small overlap between the two sets (123 loans in 2021Q1) is reconciled by
    `merge_duplicate_rows` in `clean_origination_lf`, so file order carries no meaning.

    Note the two sets no longer share a layout: since Release 47 the standard set is the
    R47 layout (which added ``VantageScore 4.0`` and moved ``Servicer Name`` /
    ``Mortgage Insurance Cancellation Indicator`` into the performance file), while the
    non-standard bundle was not reissued and stays pre-R47. So each carries a column the
    other lacks, and `scan_origination_bronze` normalizes both before the concat.
    """
    standard = sorted(bronze_dir.glob(f"historical_data_{year}Q*.parquet"))
    non_standard = sorted(bronze_dir.glob(f"historical_data_excl_{year}Q*.parquet"))
    return standard + non_standard


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
        # Discover years from bronze filenames. Two shapes share this directory:
        # historical_data_YYYYQn and historical_data_excl_YYYYQn — the year is always the
        # LAST underscore-separated part. (Splitting on a fixed index 2 parsed "excl" as
        # the year and silently dropped the whole non-standard set from discovery.)
        discovered = set()
        for f in bronze_dir.glob("historical_data_*Q*.parquet"):
            try:
                discovered.add(int(f.stem.rsplit("_", 1)[-1][:4]))
            except (IndexError, ValueError):
                continue
        years = sorted(discovered)

    silver_dir.mkdir(parents=True, exist_ok=True)

    counts = {"processed": 0, "skipped": 0, "failed": 0}

    for year in sorted(set(years)):
        partition_dir = silver_dir / f"vintage_year={year}"
        output_file = partition_dir / "data.parquet"

        if not should_process_file(output_file, overwrite=overwrite):
            logger.info("Skipping %s (silver exists at %s)", year, output_file)
            counts["skipped"] += 1
            continue

        bronze_files = _bronze_files_for_year(bronze_dir, year)
        if not bronze_files:
            logger.warning("No bronze files found for %s in %s", year, bronze_dir)
            counts["failed"] += 1
            continue

        logger.info(
            "Processing %s (%s bronze quarterly file(s)) -> %s",
            year,
            len(bronze_files),
            output_file,
        )

        try:
            # Scan per file: the R47 standard layout and the pre-R47 excl layout need
            # different normalization (renames, moved-off columns, and Postal Code
            # encoding), so each is conformed before the diagonal concat null-fills
            # whichever columns the other era lacks.
            lf = pl.concat(
                [scan_origination_bronze(f) for f in bronze_files],
                how="diagonal_relaxed",
            )
            lf = clean_origination_lf(lf)
            enforce_schema(
                lf.collect_schema(), FHLMC_ORIGINATION_SILVER.target, name="fhlmc/origination"
            )
            partition_dir.mkdir(parents=True, exist_ok=True)
            with atomic_output(output_file) as tmp:
                lf.sink_parquet(
                    tmp,
                    compression=FHLMCConfig.PARQUET_COMPRESSION,
                    statistics=FHLMCConfig.PARQUET_STATISTICS,
                    row_group_size=FHLMCConfig.PARQUET_ROW_GROUP_SIZE,
                )
            counts["processed"] += 1
            logger.info("  Saved %s", output_file)
        except Exception as e:
            logger.exception("Failed to process %s: %s", year, e)
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
        logger.info("Processing %s -> %s", bronze_file.name, out_file.name)
        lf = apply_silver_types(scan_performance_bronze(bronze_file), FHLMC_PERFORMANCE_SILVER)
        enforce_schema(
            lf.collect_schema(), FHLMC_PERFORMANCE_SILVER.target, name="fhlmc/performance"
        )
        with atomic_output(out_file) as tmp:
            lf.sink_parquet(
                tmp,
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
        "FHLMC performance silver: processed=%s skipped=%s total_rows=%s",
        counts["processed"],
        counts["skipped"],
        f"{counts['total_rows']:,}",
    )
    return counts
