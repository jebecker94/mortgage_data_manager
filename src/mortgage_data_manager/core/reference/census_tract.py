"""Census tract vintage utilities.

The Census Bureau redrew tract boundaries for the 2020 Decennial Census.
HMDA and FHFA both adopted the 2020 vintage in their 2022 reporting year,
so any cross-year join that spans the 2021/2022 boundary on `census_tract`
will silently lose ~16–27% of matches in redrawn tracts.

This module exposes the Census Bureau's 2010↔2020 tract relationship file
(`tab20_tract20_tract10_natl.txt`) for bridging cross-vintage joins. The
file is stable — the Census Bureau does not reissue — so we cache it once
as parquet.

The earlier redraw has the same shape: both sources adopted the 2010 vintage
in their 2012 reporting year, so a join of HMDA 2011 onto FHFA 2012 keeps only
~71% of tract codes. The 2000↔2010 relationship file (`us2010trf.txt`) is
exposed the same way.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import DownloadStatus, atomic_download
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

CENSUS_REL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/tab20_tract20_tract10_natl.txt"
)

VINTAGE_2020_FIRST_YEAR: int = 2022


_REFERENCE_DIR = MortgageDataConfig.REFERENCE_DIR


def _cache_dir() -> Path:
    return _REFERENCE_DIR / "census_tract"


def _raw_path() -> Path:
    return _cache_dir() / "tab20_tract20_tract10_natl.txt"


def _parquet_path() -> Path:
    return _cache_dir() / "tract_relationship_2010_2020.parquet"


def _build_parquet_cache() -> Path:
    raw = _raw_path()
    if not raw.exists():
        logger.info("Downloading Census 2010↔2020 tract relationship file from %s", CENSUS_REL_URL)
        raw.parent.mkdir(parents=True, exist_ok=True)
        result = atomic_download(
            CENSUS_REL_URL, raw, show_progress=False, source="reference", raw_dir=_REFERENCE_DIR
        )
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
    logger.info("Cached tract relationship file as parquet: %s", parquet)
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
    """Report whether a (HMDA, FHFA) year pair crosses the 2010↔2020 vintage boundary.

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


# ---------------------------------------------------------------------------
# 2000↔2010 vintage bridge (2011/2012 boundary)
# ---------------------------------------------------------------------------

CENSUS_REL_2000_2010_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel/trf_txt/us2010trf.txt"
)

VINTAGE_2010_FIRST_YEAR: int = 2012

# Comma-delimited, no header; 30 fields per the 2010 Census Tract Relationship
# File record layout. Only GEOID00 / GEOID10 / POPPCT10 are kept.
_REL_2000_2010_COLUMNS = [
    "STATE00",
    "COUNTY00",
    "TRACT00",
    "GEOID00",
    "POP00",
    "HU00",
    "PART00",
    "AREA00",
    "AREALAND00",
    "STATE10",
    "COUNTY10",
    "TRACT10",
    "GEOID10",
    "POP10",
    "HU10",
    "PART10",
    "AREA10",
    "AREALAND10",
    "AREAPT",
    "AREALANDPT",
    "AREAPCT00PT",
    "AREALANDPCT00PT",
    "AREAPCT10PT",
    "AREALANDPCT10PT",
    "POP10PT",
    "POPPCT00",
    "POPPCT10",
    "HU10PT",
    "HUPCT00",
    "HUPCT10",
]


def _raw_path_2000_2010() -> Path:
    return _cache_dir() / "us2010trf.txt"


def _parquet_path_2000_2010() -> Path:
    return _cache_dir() / "tract_relationship_2000_2010.parquet"


def _build_parquet_cache_2000_2010() -> Path:
    raw = _raw_path_2000_2010()
    if not raw.exists():
        logger.info(
            "Downloading Census 2000↔2010 tract relationship file from %s", CENSUS_REL_2000_2010_URL
        )
        raw.parent.mkdir(parents=True, exist_ok=True)
        result = atomic_download(
            CENSUS_REL_2000_2010_URL,
            raw,
            show_progress=False,
            source="reference",
            raw_dir=_REFERENCE_DIR,
        )
        if result.status != DownloadStatus.SUCCESS:
            raise RuntimeError(
                f"Failed to download Census 2000↔2010 tract relationship file: "
                f"{result.error_message}"
            )

    parquet = _parquet_path_2000_2010()
    pl.read_csv(
        raw,
        has_header=False,
        new_columns=_REL_2000_2010_COLUMNS,
        schema_overrides={"GEOID00": pl.Utf8, "GEOID10": pl.Utf8, "POPPCT10": pl.Float64},
    ).select("GEOID00", "GEOID10", "POPPCT10").write_parquet(parquet)
    logger.info("Cached 2000↔2010 tract relationship file as parquet: %s", parquet)
    return parquet


def load_tract_relationship_2000_2010(min_poppct10: float = 5.0) -> pl.LazyFrame:
    """Census 2000↔2010 tract relationship as a LazyFrame.

    Args:
        min_poppct10: Drop 2000-vintage candidates that hold less than this share
            of the 2010 tract's population. Slivers below the default 5% add
            join fan-out and 1:1 collisions for negligible recall: on the
            pre-2018 HMDA ↔ FHFA Round 2 at 2012 the pruned bridge recovers 65,369
            of the 68,242 unpruned pairs and disturbs none of the identity-join
            pairs, with ~1 pp higher held-out sex / borrower-count agreement.

    Returns:
        LazyFrame with columns:
            - GEOID00: 11-char 2000-vintage tract GEOID
            - GEOID10: 11-char 2010-vintage tract GEOID
            - POPPCT10: share of the 2010 tract's population in this intersection

    Downloads and parquet-caches on first use. Idempotent thereafter.
    """
    parquet = _parquet_path_2000_2010()
    if not parquet.exists():
        _build_parquet_cache_2000_2010()
    return pl.scan_parquet(parquet).filter(pl.col("POPPCT10") >= min_poppct10)


def needs_vintage_bridge_2000_2010(hmda_year: int, fhfa_year: int) -> bool:
    """Report whether a (HMDA, FHFA) year pair crosses the 2000↔2010 vintage boundary.

    Both HMDA and FHFA switched from the 2000 to the 2010 Census tract vintage
    in their 2012 reporting year, so the boundary is crossed iff HMDA < 2012 <= FHFA.

    Args:
        hmda_year: HMDA activity year.
        fhfa_year: FHFA reporting year.

    Returns:
        True if vintage bridging is required for this pair.
    """
    return hmda_year < VINTAGE_2010_FIRST_YEAR <= fhfa_year
