"""Bronze-layer import for HUD multifamily: raw Excel → typed parquet.

One typed parquet per logical table in :data:`DATASET_MAP`. The builder:

- reads the workbook with the registry's ``header_row`` (several HUD files
  carry a title/count or instruction block above the real header), unioning
  multiple sheets where a table is split across them (rent/utility);
- normalizes column names to snake_case and drops the blank/unnamed spacer
  columns HUD's exports leave behind;
- strips the pervasive fixed-width trailing whitespace from every string
  column (empty strings become nulls) and keeps identifier-like columns
  (FHA numbers, property ids, zips, SoA codes) as strings so leading zeros
  survive;
- stamps each row with the source file's ``snapshot_date`` (from the download
  manifest's ``Last-Modified``) and ``source_file`` so archived vintages can be
  stacked into a panel later.

Deeper key normalization (zero-padding, ``*_norm`` join keys, reference-code
filtering) is deferred to the silver layer.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.hud_mf.config import (
    DATASET_MAP,
    IDENTIFIER_COLUMNS,
    MANUAL_HEADER_TABLES,
    HudMfConfig,
    TableSpec,
    resolve_datasets,
    to_snake,
)
from mortgage_data_manager.hud_mf.download import load_manifest

logger = get_logger(__name__)


def _read_manual_header(source_path: Path, sheet: str | None, header_row: int) -> pl.DataFrame:
    """Read a sheet headerless and promote row ``header_row`` to column names.

    For HUD's irregular lookup sheets where calamine's ``header_row`` width
    detection latches onto a sparse title/instruction row above the real header.
    Empty header cells become ``unnamed_<i>`` (dropped downstream); duplicate
    names are de-duplicated with a numeric suffix.
    """
    raw = pl.read_excel(source_path, sheet_name=sheet, engine="calamine", has_header=False)
    header = raw.row(header_row)
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, value in enumerate(header):
        base = to_snake(value) if value not in (None, "") else f"unnamed_{i}"
        base = base or f"unnamed_{i}"
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
        names.append(base)
    body = raw.slice(header_row + 1)
    body.columns = names
    return body


def _read_workbook(spec: TableSpec, dataset: str, source_path: Path) -> pl.DataFrame:
    """Read a table's sheet(s) at its header row and vertically union them."""
    sheets = spec["sheets"]
    if dataset in MANUAL_HEADER_TABLES:
        sheet_list: tuple[str | None, ...] = sheets if sheets is not None else (None,)
        frames = [_read_manual_header(source_path, s, spec["header_row"]) for s in sheet_list]
    elif sheets is None:
        return pl.read_excel(
            source_path, engine="calamine", read_options={"header_row": spec["header_row"]}
        )
    else:
        frames = [
            pl.read_excel(
                source_path,
                sheet_name=sheet,
                engine="calamine",
                read_options={"header_row": spec["header_row"]},
            )
            for sheet in sheets
        ]
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")


def _normalize_columns(df: pl.DataFrame) -> pl.DataFrame:
    """snake_case headers and drop blank/unnamed spacer columns."""
    df = df.rename({col: to_snake(col) for col in df.columns})
    drop = [
        col
        for col in df.columns
        if col == "" or col.startswith("unnamed") or col.startswith("_unnamed")
    ]
    return df.drop(drop) if drop else df


def _clean_values(df: pl.DataFrame) -> pl.DataFrame:
    """Strip whitespace on strings (empty → null); keep identifiers as strings."""
    exprs: list[pl.Expr] = []
    for col, dtype in df.schema.items():
        if col in IDENTIFIER_COLUMNS:
            # Render numeric-looking identifiers without a ".0" tail, then strip.
            base = (
                pl.col(col).cast(pl.Int64, strict=False).cast(pl.Utf8)
                if dtype.is_numeric()
                else pl.col(col).cast(pl.Utf8)
            )
            cleaned = base.str.strip_chars()
            exprs.append(
                pl.when(cleaned.str.len_chars() == 0).then(None).otherwise(cleaned).alias(col)
            )
        elif dtype in (pl.Utf8, pl.String):
            cleaned = pl.col(col).str.strip_chars()
            exprs.append(
                pl.when(cleaned.str.len_chars() == 0).then(None).otherwise(cleaned).alias(col)
            )
    return df.with_columns(exprs) if exprs else df


def _snapshot_date(source_filename: str) -> date | None:
    """Resolve the source file's snapshot date from the download manifest."""
    raw = load_manifest().get(source_filename, {}).get("last_modified")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def build_bronze(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Convert raw HUD-MF workbooks into typed bronze parquets.

    Args:
        datasets: Subset of :data:`VALID_DATASETS`. ``None`` = all.
        overwrite: If True, rebuild existing bronze parquets.

    Returns:
        Paths of bronze parquets written (skipped tables excluded).

    Raises:
        FileNotFoundError: If a table's source workbook is missing from raw/.
    """
    HudMfConfig.ensure_directories()
    written: list[Path] = []
    for dataset in resolve_datasets(datasets):
        bronze_path = HudMfConfig.bronze_path(dataset)
        if not should_process_file(bronze_path, overwrite=overwrite):
            logger.info(f"Bronze already exists, skipping: {bronze_path.name}")
            continue

        spec = DATASET_MAP[dataset]
        source_path = HudMfConfig.raw_path(spec["source"])
        if not source_path.exists():
            raise FileNotFoundError(
                f"Raw HUD-MF workbook missing for {dataset}: {source_path}\n"
                "Run download first (mortgage-data hud-mf download)."
            )

        df = _read_workbook(spec, dataset, source_path)
        df = _normalize_columns(df)
        df = _clean_values(df)
        df = df.with_columns(
            pl.lit(spec["source"]).alias("source_file"),
            pl.lit(_snapshot_date(spec["source"])).cast(pl.Date).alias("snapshot_date"),
        )

        bronze_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = bronze_path.with_suffix(bronze_path.suffix + ".tmp")
        try:
            df.write_parquet(temp_path, compression="zstd")
            temp_path.rename(bronze_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        logger.info(
            f"HUD-MF {dataset}: bronze → {bronze_path.name} ({df.height:,} rows × {df.width} cols)"
        )
        written.append(bronze_path)
    return written


__all__ = ["build_bronze"]
