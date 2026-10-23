"""Census-tract to 3-digit-ZIP mapping (HUD USPS crosswalk).

Some sources report property geography as a census tract (FHFA PUDB, HMDA)
while others report a 3-digit ZIP (the legacy GSE loan-level files). The two
do not join natively; this module bridges them using HUD's USPS ZIP-tract
crosswalk, weighting by residential address share (``res_ratio``) so each
tract maps to the ZIP3(s) where its housing stock actually sits.

Two vintages are cached, matching the census-tract eras of the consuming
sources: HUD 2019Q4 for 2010-census tracts and 2023Q4 for 2020-census tracts.
Use `census_year_to_vintage_expr` to pick the vintage from a source's
per-record census year (e.g. FHFA sf_c ``Census Year``).

Built from the usps_crosswalk subpackage's per-quarter ``TRACT_ZIP`` bronze
(run ``mortgage-data usps-crosswalk bronze`` first); cached once as parquet.
Validated against known true pairs: the dominant ZIP3 agrees with the
loan-level disclosed ZIP3 on 97.8% of them.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

# HUD crosswalk vintages cached: HUD year -> quarter. 2019Q4 carries
# 2010-census tracts; 2023Q4 carries 2020-census tracts.
TRACT_ZIP3_VINTAGES: dict[int, int] = {2019: 4, 2023: 4}

# A tract's second-best ZIP3 is kept only if it holds at least this share of
# the tract's residential addresses (mirrors the validated matcher asset).
SECOND_ZIP3_MIN_SHARE: float = 0.15


def census_year_to_vintage_expr(census_year_col: str) -> pl.Expr:
    """Map a per-record census-year column (2010/2020) to the cached HUD vintage.

    Args:
        census_year_col: Column holding the record's census year.

    Returns:
        An expression yielding the matching HUD crosswalk vintage.
    """
    return (
        pl.when(pl.col(census_year_col) == 2010)
        .then(pl.lit(2019))
        .otherwise(pl.lit(2023))
        .alias("vintage")
    )


def _hud_source_path(year: int, quarter: int) -> Path:
    """Per-quarter TRACT_ZIP bronze parquet for one crosswalk vintage.

    Addressed by quarter rather than through a combined file: the bronze
    filename is stable, whereas a combined name would encode a year range that
    shifts every time the source is extended.

    Returns:
        The bronze parquet path for that crosswalk quarter.
    """
    return (
        MortgageDataConfig.DATA_DIR
        / "usps_crosswalk"
        / "bronze"
        / "TRACT_ZIP"
        / f"TRACT_ZIP_{year}_Q{quarter}.parquet"
    )


def _parquet_path() -> Path:
    return MortgageDataConfig.REFERENCE_DIR / "tract_zip3" / "tract_zip3.parquet"


def _build_parquet_cache() -> Path:
    outs = []
    for year, quarter in TRACT_ZIP3_VINTAGES.items():
        source = _hud_source_path(year, quarter)
        if not source.exists():
            raise FileNotFoundError(
                f"TRACT_ZIP bronze not found at {source} — build the usps_crosswalk "
                "subpackage bronze first (mortgage-data usps-crosswalk bronze)."
            )
        shares = (
            pl.scan_parquet(source)
            .select(
                pl.col("tract").alias("census_tract"),
                pl.col("geoid").str.slice(0, 3).alias("zip3"),
                pl.col("res_ratio"),
            )
            .group_by("census_tract", "zip3")
            .agg(pl.col("res_ratio").sum().alias("w"))
            .with_columns((pl.col("w") / pl.col("w").sum().over("census_tract")).alias("share"))
            .collect(engine="streaming")
            # all-zero-res tracts produce null/NaN shares: no residential
            # addresses, so no loans should geocode there — drop.
            .filter(pl.col("share").is_not_nan() & pl.col("share").is_not_null())
        )
        # deterministic ranking: share desc, zip3 asc breaks exact-share ties
        ranked = shares.sort(
            ["census_tract", "share", "zip3"], descending=[False, True, False]
        ).with_columns(pl.int_range(pl.len()).over("census_tract").alias("_rk"))
        kept = ranked.filter(
            (pl.col("_rk") == 0)
            | ((pl.col("_rk") == 1) & (pl.col("share") >= SECOND_ZIP3_MIN_SHARE))
        ).drop("_rk", "w")
        outs.append(kept.with_columns(pl.lit(year, dtype=pl.Int32).alias("vintage")))
        logger.info(
            "tract_zip3 vintage %dQ%d: %s tracts cached",
            year,
            quarter,
            f"{kept['census_tract'].n_unique():,}",
        )
    parquet = _parquet_path()
    parquet.parent.mkdir(parents=True, exist_ok=True)
    pl.concat(outs, how="diagonal_relaxed").select(
        "census_tract", "vintage", "zip3", "share"
    ).write_parquet(parquet)
    logger.info("Cached tract->zip3 mapping: %s", parquet)
    return parquet


def load_tract_zip3(overwrite: bool = False) -> pl.LazyFrame:
    """Tract -> candidate ZIP3s with residential shares, per vintage.

    Args:
        overwrite: Rebuild the cache from the per-quarter TRACT_ZIP bronze.

    Returns:
        LazyFrame with columns ``census_tract`` (11-char GEOID), ``vintage``
        (2019 = 2010-census tracts, 2023 = 2020-census tracts), ``zip3``,
        ``share`` (residential-address share; up to two rows per tract).

    Builds and caches on first use.
    """
    parquet = _parquet_path()
    if overwrite or not parquet.exists():
        _build_parquet_cache()
    return pl.scan_parquet(parquet)


def load_dominant_tract_zip3(overwrite: bool = False) -> pl.LazyFrame:
    """One ZIP3 per (tract, vintage): the residential-share-dominant candidate.

    A tract whose top share is held by two ZIP3s (an exact 0.5/0.5 split) has no
    dominant candidate, so its ``zip3`` is null rather than either one.

    Args:
        overwrite: Rebuild the underlying cache before reducing.

    Returns:
        One row per ``(census_tract, vintage)``, carrying the dominant ``zip3``.
    """
    keys = ["census_tract", "vintage"]
    return (
        load_tract_zip3(overwrite=overwrite)
        .filter(pl.col("share") == pl.col("share").max().over(keys))
        .select(*keys, pl.when(pl.len().over(keys) == 1).then(pl.col("zip3")).alias("zip3"))
        .unique()
    )
