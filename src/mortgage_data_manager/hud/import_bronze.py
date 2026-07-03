"""Build the HUD bronze layer: per-quarter typed parquet from raw JSON.

The raw layer holds each quarter's untouched HUD API response as JSON. Bronze
reads one quarter at a time, enforces the schema (zero-padded geographic codes,
numeric ratios), tags it with year/quarter/type metadata, and writes a
per-quarter parquet — the same shape downstream loaders, validators, and the
longitudinal silver combine all consume. The combine itself lives in
``import_silver``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.hud.config import (
    CROSSWALK_TYPES_BY_NAME,
    GEO_CODE_LENGTHS,
    RATIO_COLUMNS,
    VALID_CROSSWALK_NAMES,
    HUDConfig,
)

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


def _discover_json_files(type_dir: Path, min_year: int, max_year: int) -> list[Path]:
    """Return all raw JSON files in a type directory within the requested year range."""
    if not type_dir.exists():
        return []

    found_files = []
    for file in sorted(type_dir.glob("*.json")):
        match = _FILENAME_RE.search(file.name)
        if match:
            file_year = int(match.group(1))
            if min_year <= file_year <= max_year:
                found_files.append(file)

    return found_files


def _read_quarter_json(json_path: Path, type_name: str) -> pd.DataFrame | None:
    """Read one quarter's raw JSON into an enforced DataFrame with metadata cols.

    Returns None if the file holds no usable results.
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
    min_year: int = 2010,
    max_year: int = 2025,
    overwrite: bool = False,
) -> int:
    """Build per-quarter bronze parquet for one crosswalk type.

    Args:
        type_name: Crosswalk type (e.g. ``"ZIP_TRACT"``).
        raw_dir: Source dir for raw JSON. Defaults to ``HUDConfig.HUD_RAW_DIR``.
        bronze_dir: Output dir. Defaults to ``HUDConfig.HUD_BRONZE_DIR``.
        min_year: First year to include.
        max_year: Last year to include.
        overwrite: If True, rebuild existing per-quarter parquet.

    Returns:
        Number of quarterly parquet files written.
    """
    raw_dir = raw_dir or HUDConfig.HUD_RAW_DIR
    bronze_dir = bronze_dir or HUDConfig.HUD_BRONZE_DIR

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
        df.to_parquet(out_path, index=False)
        logger.info("Wrote %s (%d rows)", out_path.name, len(df))
        written += 1

    return written


def build_bronze(
    datasets: list[str] | None = None,
    min_year: int = 2010,
    max_year: int = 2025,
    overwrite: bool = False,
) -> dict[str, int]:
    """Build per-quarter bronze parquet from raw JSON for the given crosswalk types.

    Args:
        datasets: Crosswalk types to build (default: all available).
        min_year: First year to include.
        max_year: Last year to include.
        overwrite: If True, rebuild existing per-quarter parquet.

    Returns:
        Dict mapping each crosswalk type to the number of quarterly files written.
    """
    if min_year > max_year:
        raise ValueError(
            f"`min_year` must be <= `max_year`. Received min_year={min_year}, max_year={max_year}."
        )

    types = datasets or VALID_CROSSWALK_NAMES
    written: dict[str, int] = {}
    for type_name in types:
        n = build_bronze_type(type_name, min_year=min_year, max_year=max_year, overwrite=overwrite)
        if n:
            written[type_name] = n
    logger.info(
        "HUD bronze: wrote %d quarterly file(s) across %d type(s)",
        sum(written.values()),
        len(written),
    )
    return written
