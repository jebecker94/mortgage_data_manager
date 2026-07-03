"""Census tract vintage utilities.

The Census Bureau redrew tract boundaries for the 2020 Decennial Census.
HMDA and FHFA both adopted the 2020 vintage in their 2022 reporting year,
so any cross-year join that spans the 2021/2022 boundary on `census_tract`
will silently lose ~16–27% of matches in redrawn tracts.

This module exposes the Census Bureau's 2010↔2020 tract relationship file
(`tab20_tract20_tract10_natl.txt`) for bridging cross-vintage joins. The
file is stable — the Census Bureau does not reissue — so we cache it once
as parquet.

See `investigations/reports/investigation_fhfa_hmda_tract_vintage_2026-05-14.md`
and `investigation_fhfa_hmda_vintage_bridge_2026-05-15.md` for context.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import DownloadStatus, atomic_download
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

CENSUS_REL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/"
    "tab20_tract20_tract10_natl.txt"
)

VINTAGE_2020_FIRST_YEAR: int = 2022


def _cache_dir() -> Path:
    return MortgageDataConfig.DATA_DIR / "reference" / "census_tract"


def _raw_path() -> Path:
    return _cache_dir() / "tab20_tract20_tract10_natl.txt"


def _parquet_path() -> Path:
    return _cache_dir() / "tract_relationship_2010_2020.parquet"


def _build_parquet_cache() -> Path:
    raw = _raw_path()
    if not raw.exists():
        logger.info(f"Downloading Census 2010↔2020 tract relationship file from {CENSUS_REL_URL}")
        raw.parent.mkdir(parents=True, exist_ok=True)
        result = atomic_download(CENSUS_REL_URL, raw, show_progress=False)
        if result.status != DownloadStatus.SUCCESS:
            raise RuntimeError(
                f"Failed to download Census tract relationship file: {result.error_message}"
            )

    parquet = _parquet_path()
    pl.read_csv(
        raw,
        separator="|",
        schema_overrides={
            "GEOID_TRACT_20": pl.Utf8,
            "GEOID_TRACT_10": pl.Utf8,
            "AREALAND_PART": pl.Int64,
            "AREALAND_TRACT_20": pl.Int64,
            "AREALAND_TRACT_10": pl.Int64,
        },
    ).select(
        "GEOID_TRACT_20",
        "GEOID_TRACT_10",
        "AREALAND_PART",
        "AREALAND_TRACT_20",
        "AREALAND_TRACT_10",
    ).write_parquet(parquet)
    logger.info(f"Cached tract relationship file as parquet: {parquet}")
    return parquet


def load_tract_relationship() -> pl.LazyFrame:
    """Census 2010↔2020 tract relationship as a LazyFrame.

    Returns:
        LazyFrame with columns:
          - GEOID_TRACT_20: 11-char 2020-vintage tract GEOID
          - GEOID_TRACT_10: 11-char 2010-vintage tract GEOID
          - AREALAND_PART: land area shared between the two tracts (m²)
          - AREALAND_TRACT_20: total land area of the 2020 tract (m²)
          - AREALAND_TRACT_10: total land area of the 2010 tract (m²)

    Downloads and parquet-caches on first use. Idempotent thereafter.
    """
    parquet = _parquet_path()
    if not parquet.exists():
        _build_parquet_cache()
    return pl.scan_parquet(parquet)


def needs_vintage_bridge(hmda_year: int, fhfa_year: int) -> bool:
    """Does this (HMDA, FHFA) year pair cross the 2010↔2020 vintage boundary?

    Both HMDA and FHFA switched from the 2010 to the 2020 Census tract
    vintage in their 2022 reporting year. The post-2018 matcher only joins
    forward (FHFA year >= HMDA year), so the boundary is crossed iff
    HMDA < 2022 <= FHFA.

    Args:
        hmda_year: HMDA activity year.
        fhfa_year: FHFA reporting year.

    Returns:
        True if vintage bridging is required for this pair.
    """
    return hmda_year < VINTAGE_2020_FIRST_YEAR <= fhfa_year
