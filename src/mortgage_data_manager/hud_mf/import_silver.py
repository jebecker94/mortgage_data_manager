"""Silver-layer import for HUD multifamily: cleaned, key-normalized tables.

Reads each bronze parquet and produces an analysis-ready flat parquet per
logical table (one file each — the tables are small, ≤60k rows, so hive
partitioning buys nothing here). The silver step:

- adds normalized join keys without dropping the raw columns:
  ``property_id_norm`` (the universal REMS-id spine) and ``fha_number_norm``
  (strip + uppercase + left-pad to 8 chars — bridges the insured subset; the
  alphanumeric Section 202 numbers are already 8 chars and pass through);
- filters the ``soa_codes`` reference table down to real Section-of-the-Act
  codes, dropping the category-banner and instruction rows interleaved in
  HUD's lookup sheet.

``overwrite`` is inherently partition-scoped: each table is a single file, so a
rebuild only replaces that table's parquet — never a directory wipe.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.hud_mf.config import (
    DATASET_MAP,
    HudMfConfig,
    resolve_datasets,
)

logger = get_logger(__name__)

#: Candidate FHA-project-number columns, in priority order. The first present
#: in a table sources its ``fha_number_norm`` key.
_FHA_NUMBER_SOURCES: tuple[str, ...] = (
    "fha_number",
    "hud_project_number",
    "project_fha_number",
)


def _add_normalized_keys(df: pl.DataFrame) -> pl.DataFrame:
    """Append ``property_id_norm`` / ``fha_number_norm`` where their sources exist."""
    exprs: list[pl.Expr] = []
    if "property_id" in df.columns:
        exprs.append(
            pl.col("property_id").cast(pl.Utf8).str.strip_chars().alias("property_id_norm")
        )
    for source in _FHA_NUMBER_SOURCES:
        if source in df.columns:
            exprs.append(
                pl.col(source)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_uppercase()
                .str.zfill(8)
                .alias("fha_number_norm")
            )
            break
    return df.with_columns(exprs) if exprs else df


def _clean_soa_codes(df: pl.DataFrame) -> pl.DataFrame:
    """Keep only real SoA codes; drop category-banner / instruction rows.

    HUD's lookup sheet interleaves category markers (``A.``, ``B.``) and prose
    with the actual 2–4 char alphanumeric codes (``ZPE``, ``ZPD`` …).
    """
    if "soa" not in df.columns:
        return df
    code = pl.col("soa")
    is_real_code = (
        code.is_not_null() & ~code.str.contains(r"\.") & code.str.contains(r"^[A-Za-z0-9]{2,4}$")
    )
    return df.filter(is_real_code)


def build_silver(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Build cleaned, key-normalized silver parquets from bronze.

    Args:
        datasets: Subset of :data:`VALID_DATASETS`. ``None`` = all.
        overwrite: If True, rebuild existing silver parquets. Each table is a
            single file, so a rebuild only replaces that table.

    Returns:
        Paths of silver parquets written (skipped tables excluded).

    Raises:
        FileNotFoundError: If a table's bronze parquet is missing.
    """
    HudMfConfig.ensure_directories()
    written: list[Path] = []
    for dataset in resolve_datasets(datasets):
        silver_path = HudMfConfig.silver_path(dataset)
        if not should_process_file(silver_path, overwrite=overwrite):
            logger.info(f"Silver already exists, skipping: {silver_path.name}")
            continue

        bronze_path = HudMfConfig.bronze_path(dataset)
        if not bronze_path.exists():
            raise FileNotFoundError(
                f"Bronze HUD-MF parquet missing for {dataset}: {bronze_path}\n"
                "Run bronze first (mortgage-data hud-mf bronze)."
            )

        df = pl.read_parquet(bronze_path)
        if DATASET_MAP[dataset]["family"] == "reference":
            df = _clean_soa_codes(df)
        df = _add_normalized_keys(df)

        silver_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = silver_path.with_suffix(silver_path.suffix + ".tmp")
        try:
            df.write_parquet(temp_path, compression="zstd")
            temp_path.rename(silver_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        logger.info(
            f"HUD-MF {dataset}: silver → {silver_path.name} ({df.height:,} rows × {df.width} cols)"
        )
        written.append(silver_path)
    return written


def scan_silver(dataset: str) -> pl.LazyFrame:
    """Lazy-scan a logical table's silver parquet.

    Args:
        dataset: One of :data:`VALID_DATASETS`.

    Returns:
        LazyFrame over the table's silver parquet.

    Raises:
        FileNotFoundError: If the silver parquet is missing.
    """
    resolve_datasets([dataset])
    path = HudMfConfig.silver_path(dataset)
    if not path.exists():
        raise FileNotFoundError(
            f"HUD-MF silver missing for {dataset} at {path}; "
            "run `mortgage-data hud-mf silver` first."
        )
    return pl.scan_parquet(path)


__all__ = ["build_silver", "scan_silver"]
