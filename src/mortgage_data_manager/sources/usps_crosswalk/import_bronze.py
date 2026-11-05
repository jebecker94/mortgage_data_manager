"""Build the USPS crosswalk bronze layer: per-quarter typed parquet from raw JSON.

The raw layer holds each quarter's untouched HUD API response as JSON. Bronze
reads one quarter at a time, enforces the schema (zero-padded geographic codes,
numeric ratios), tags it with year/quarter/type metadata, and writes a
per-quarter parquet — the shape downstream loaders and validators consume.

Bronze is the last layer for this source: there is no silver. Read across
quarters with ``load.load_crosswalk()`` / ``load.load_crosswalk_by_vintage()``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.sources.usps_crosswalk.config import (
    CROSSWALK_TYPES_BY_NAME,
    GEO_CODE_LENGTHS,
    RATIO_COLUMNS,
    USPSCrosswalkConfig,
)
from mortgage_data_manager.sources.usps_crosswalk.download import resolve_datasets

logger = get_logger(__name__)

_FILENAME_RE = re.compile(r"_(\d{4})_Q(\d)\.json$")


def enforce_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce consistent data types across a quarter's crosswalk records.

    Converts geographic identifiers to zero-padded strings and ensures ratio
    columns are proper floats.

    Args:
        df: DataFrame built from one quarter's raw API ``results``.

    Returns:
        DataFrame with enforced types.
    """
    for col, length in GEO_CODE_LENGTHS.items():
        if col in df.columns:
            df[col] = df[col].astype(str).str.zfill(length)

    for field in RATIO_COLUMNS:
        if field in df.columns:
            df[field] = pd.to_numeric(df[field], errors="coerce")

    return df


def _discover_json_files(type_dir: Path, min_year: int | None, max_year: int | None) -> list[Path]:
    """Raw JSON files for one type, optionally clipped to a year range.

    Both bounds default to ``None`` = unbounded, so a plain build picks up every
    quarter present in raw, including newly downloaded years.

    Returns:
        The matching raw JSON paths, sorted, or empty when the directory is absent.
    """
    if not type_dir.exists():
        return []

    found = []
    for file in sorted(type_dir.glob("*.json")):
        match = _FILENAME_RE.search(file.name)
        if not match:
            continue
        year = int(match.group(1))
        if (min_year is None or year >= min_year) and (max_year is None or year <= max_year):
            found.append(file)
    return found


def _read_quarter_json(json_path: Path, type_name: str) -> pd.DataFrame | None:
    """Read one quarter's raw JSON into an enforced DataFrame with metadata cols.

    Returns:
        The quarter's records with year/quarter metadata columns, or None when
        the file holds no usable results.
    """
    match = _FILENAME_RE.search(json_path.name)
    if not match:
        logger.warning("Could not parse year/quarter from %s", json_path.name)
        return None
    year, quarter = int(match.group(1)), int(match.group(2))

    data = json.loads(json_path.read_text(encoding="utf-8"))
    results = data.get("data", {}).get("results") if isinstance(data, dict) else None
    if not results:
        logger.warning("No results in %s", json_path.name)
        return None

    df = pd.DataFrame(results)
    df = enforce_schema(df)
    df["year"] = year
    df["quarter"] = quarter
    df["type_id"] = CROSSWALK_TYPES_BY_NAME[type_name].type_id
    df["type_name"] = type_name
    return df


def build_bronze_type(
    type_name: str,
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
) -> int:
    """Build per-quarter bronze parquet for one crosswalk type.

    Args:
        type_name: Crosswalk type (e.g. ``"ZIP_TRACT"``).
        raw_dir: Source dir for raw JSON. Defaults to ``USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR``.
        bronze_dir: Output dir. Defaults to ``USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR``.
        min_year: First year to include (``None`` = no lower bound).
        max_year: Last year to include (``None`` = no upper bound).
        overwrite: If True, rebuild existing per-quarter parquet.

    Returns:
        Number of quarterly parquet files written.
    """
    raw_dir = raw_dir or USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR
    bronze_dir = bronze_dir or USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR

    files = _discover_json_files(raw_dir / type_name, min_year, max_year)
    out_dir = bronze_dir / type_name
    written = 0

    for json_path in files:
        out_path = out_dir / f"{json_path.stem}.parquet"
        if not should_process_file(out_path, overwrite):
            logger.debug("Skipping existing bronze file: %s", out_path.name)
            continue
        df = _read_quarter_json(json_path, type_name)
        if df is None:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        with atomic_output(out_path) as tmp:
            df.to_parquet(tmp, index=False)
        logger.info("Wrote %s (%d rows)", out_path.name, len(df))
        written += 1

    return written


def build_bronze(
    datasets: list[str] | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Build per-quarter bronze parquet from raw JSON for the given crosswalk types.

    Args:
        datasets: Crosswalk types to build (default: all 12).
        min_year: First year to include (``None`` = every year present in raw).
        max_year: Last year to include (``None`` = every year present in raw).
        overwrite: If True, rebuild existing per-quarter parquet.

    Returns:
        Dict mapping each crosswalk type to the number of quarterly files written.
    """
    if min_year is not None and max_year is not None and min_year > max_year:
        raise ValueError(
            f"`min_year` must be <= `max_year`. Received min_year={min_year}, max_year={max_year}."
        )

    types = resolve_datasets(datasets)
    written: dict[str, int] = {}
    for type_name in types:
        n = build_bronze_type(type_name, min_year=min_year, max_year=max_year, overwrite=overwrite)
        if n:
            written[type_name] = n
    logger.info(
        "USPS crosswalk bronze: wrote %d quarterly file(s) across %d type(s)",
        sum(written.values()),
        len(written),
    )
    return written
