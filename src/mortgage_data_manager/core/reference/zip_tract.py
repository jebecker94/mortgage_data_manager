"""ZIP <-> census tract crosswalks per census vintage (HUD USPS crosswalk).

A tract code names different areas under different censuses, so a ZIP-tract join must
use the crosswalk of the census a record's tracts come from. HUD publishes each
census's tracts over a fixed run of quarters (2000-census tracts 2010Q1-2011Q4,
2010-census 2012Q1-2022Q4, 2020-census from 2023Q1), while HMDA and FHFA switched
vintages one reporting year earlier, in 2012 and 2022; `tract_vintage` maps a
reporting year to its census.

Two assets are built from the usps_crosswalk subpackage's per-quarter bronze (run
``mortgage-data usps-crosswalk bronze`` first):

- `load_zip_tract`: every ZIP-tract pair HUD published for one census, cached as
  parquet under ``REFERENCE_DIR/zip_tract/``.
- `tract_zip_residential_ratio`: each tract's share of residential addresses in each
  ZIP, averaged over the census's quarters; small enough to compute on demand.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference.census_tract import (
    VINTAGE_2010_FIRST_YEAR,
    VINTAGE_2020_FIRST_YEAR,
)

if TYPE_CHECKING:
    from mortgage_data_manager.sources.usps_crosswalk.config import CensusVintage

logger = get_logger(__name__)


def tract_vintage(year: int) -> CensusVintage:
    """The census whose tracts HMDA and FHFA report for a reporting year.

    Args:
        year: Reporting (HMDA activity or FHFA acquisition) year.

    Returns:
        2000 before `VINTAGE_2010_FIRST_YEAR`, 2010 before `VINTAGE_2020_FIRST_YEAR`,
        2020 from then on.
    """
    if year < VINTAGE_2010_FIRST_YEAR:
        return 2000
    return 2010 if year < VINTAGE_2020_FIRST_YEAR else 2020


def zip_tract_path(census_vintage: CensusVintage, cache_dir: Path | None = None) -> Path:
    """The cached ZIP-tract crosswalk file for one census.

    Args:
        census_vintage: Census tract vintage (2000, 2010 or 2020).
        cache_dir: Directory holding the cache. Defaults to ``REFERENCE_DIR/zip_tract``.

    Returns:
        ``{cache_dir}/zip_tract_{census_vintage}_census.parquet``.
    """
    directory = cache_dir or MortgageDataConfig.REFERENCE_DIR / "zip_tract"
    return directory / f"zip_tract_{census_vintage}_census.parquet"


def build_zip_tract(
    census_vintage: CensusVintage,
    destination: Path,
    data_dir: Path | None = None,
) -> Path:
    """Write every ZIP-tract pair HUD published for one census, deduplicated.

    Args:
        census_vintage: Census tract vintage (2000, 2010 or 2020).
        destination: Parquet file to write, with columns ``ZIP`` and ``TRACT``.
        data_dir: USPS crosswalk bronze root. Defaults to the usps_crosswalk config.

    Returns:
        ``destination``.

    Raises:
        ValueError: If ``census_vintage`` is not 2000, 2010 or 2020.
    """
    from mortgage_data_manager.sources.usps_crosswalk.load import load_crosswalk_by_vintage

    if census_vintage not in (2000, 2010, 2020):
        raise ValueError(f"Invalid census_vintage: {census_vintage}. Must be 2000, 2010 or 2020")

    pairs = (
        load_crosswalk_by_vintage("ZIP_TRACT", census_vintage, data_dir=data_dir)
        .select(pl.col("zip").alias("ZIP"), pl.col("geoid").alias("TRACT"))
        .unique()
        .collect()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(destination) as tmp:
        pairs.write_parquet(tmp)
    logger.info(
        "Built %s-census ZIP-tract crosswalk: %s pairs -> %s",
        census_vintage,
        f"{pairs.height:,}",
        destination,
    )
    return destination


def zip_tract_file(
    census_vintage: CensusVintage,
    *,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """The cached ZIP-tract crosswalk for one census, built first if absent.

    Args:
        census_vintage: Census tract vintage (2000, 2010 or 2020).
        cache_dir: Directory holding the cache. Defaults to ``REFERENCE_DIR/zip_tract``.
        data_dir: USPS crosswalk bronze root. Defaults to the usps_crosswalk config.
        overwrite: Rebuild the cache from bronze even if it exists.

    Returns:
        The parquet path (columns ``ZIP``, ``TRACT``).
    """
    path = zip_tract_path(census_vintage, cache_dir)
    if should_process_file(path, overwrite):
        build_zip_tract(census_vintage, path, data_dir)
    return path


def load_zip_tract(
    census_vintage: CensusVintage,
    *,
    cache_dir: Path | None = None,
    data_dir: Path | None = None,
    overwrite: bool = False,
) -> pl.LazyFrame:
    """Every ZIP-tract pair HUD published for one census.

    Args:
        census_vintage: Census tract vintage (2000, 2010 or 2020).
        cache_dir: Directory holding the cache. Defaults to ``REFERENCE_DIR/zip_tract``.
        data_dir: USPS crosswalk bronze root. Defaults to the usps_crosswalk config.
        overwrite: Rebuild the cache from bronze even if it exists.

    Returns:
        LazyFrame with ``ZIP`` (5-char) and ``TRACT`` (11-char GEOID), one row per pair.
    """
    return pl.scan_parquet(
        zip_tract_file(census_vintage, cache_dir=cache_dir, data_dir=data_dir, overwrite=overwrite)
    )


def tract_zip_residential_ratio(
    vintages: Sequence[CensusVintage] = (2000, 2010),
    *,
    last_year: int | None = None,
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Share of each tract's residential addresses in each ZIP, per census.

    HUD's TRACT_ZIP ``res_ratio``, averaged over the census's quarters.

    Args:
        vintages: Censuses to include.
        last_year: Last HUD crosswalk year averaged in. ``None`` = every published
            quarter. Pin it when a result must not move as HUD publishes new quarters.
        data_dir: USPS crosswalk bronze root. Defaults to the usps_crosswalk config.

    Returns:
        ``census_tract``, ``ZIP``, the mean ``res_ratio`` and ``tract_vintage`` (Int16).
    """
    from mortgage_data_manager.sources.usps_crosswalk.load import load_crosswalk_by_vintage

    frames = []
    for vintage in vintages:
        lf = load_crosswalk_by_vintage("TRACT_ZIP", vintage, data_dir=data_dir)
        if last_year is not None:
            lf = lf.filter(pl.col("year") <= last_year)
        frames.append(
            lf.group_by(pl.col("tract").alias("census_tract"), pl.col("geoid").alias("ZIP"))
            .agg(pl.col("res_ratio").mean())
            .with_columns(pl.lit(vintage, dtype=pl.Int16).alias("tract_vintage"))
            .collect()
        )
    return pl.concat(frames)
