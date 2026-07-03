"""Connecticut planning-region tract bridge.

Connecticut replaced its 8 county-based county-equivalents (FIPS 09001-09015)
with 9 planning regions (FIPS 09110-09190). OMB issued the change June 2022;
HMDA adopted the new tract GEOIDs as a **hard cut at the 2023->2024 reporting
boundary** (0% planning-region in 2023, 100% in 2024 -- see
`investigation_ct_geography_conventions_2026-05-21.md`).

Any cross-year join on `census_tract` that pairs a pre-2024 (county-coded) CT
record with a 2024+ (planning-region-coded) CT record silently loses the match:
the GEOID strings never agree (HMDA's CT tract sets for 2023 and 2024 have a
*zero* intersection). This breaks, in particular, the HMDA seller-purchaser
matcher, whose cross-year rounds require an exact `census_tract` join (see
`investigation_ct_planning_region_seller_purchaser_2026-06-10.md`).

The national 2010<->2020 tract relationship file
(`core.reference.census_tract`) does NOT bridge this: it carries Connecticut
under the old county codes on *both* its sides, so the planning-region GEOIDs
never appear in it.

This module exposes the CT-Data-Collaborative 2022 tract crosswalk, which maps
each CT tract between its 2020 county-based GEOID (`tract_fips_2020`) and its
2022 planning-region GEOID (`tract_fips_2022`). The mapping is a clean 1:1
bijection across all 879 CT tracts -- the 6-digit tract suffix is preserved and
only the 5-digit county-equivalent prefix changes (e.g. 09013528100 ->
09110528100). The file is stable (not reissued), so it is parquet-cached once.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import DownloadStatus, atomic_download
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

CT_CROSSWALK_URL = (
    "https://raw.githubusercontent.com/CT-Data-Collaborative/"
    "2022-tract-crosswalk/main/2022tractcrosswalk.csv"
)

# HMDA reporting year in which CT tract GEOIDs first appear under planning-region
# codes (a hard cut: pre-2024 = county-coded, 2024+ = planning-region-coded).
PLANREG_FIRST_YEAR: int = 2024


def _cache_dir() -> Path:
    return MortgageDataConfig.DATA_DIR / "reference" / "ct_planning_region"


def _raw_path() -> Path:
    return _cache_dir() / "2022tractcrosswalk.csv"


def _parquet_path() -> Path:
    return _cache_dir() / "ct_planning_region_crosswalk.parquet"


def _build_parquet_cache() -> Path:
    raw = _raw_path()
    if not raw.exists():
        logger.info(f"Downloading CT planning-region tract crosswalk from {CT_CROSSWALK_URL}")
        raw.parent.mkdir(parents=True, exist_ok=True)
        result = atomic_download(CT_CROSSWALK_URL, raw, show_progress=False)
        if result.status != DownloadStatus.SUCCESS:
            raise RuntimeError(
                f"Failed to download CT planning-region crosswalk: {result.error_message}"
            )

    parquet = _parquet_path()
    pl.read_csv(
        raw,
        schema_overrides={"tract_fips_2020": pl.Utf8, "Tract_fips_2022": pl.Utf8},
    ).select(
        tract_fips_2020="tract_fips_2020",
        tract_fips_2022="Tract_fips_2022",
        county_fips_2020="county_fips_2020",
        ce_fips_2022="ce_fips_2022",
    ).write_parquet(parquet)
    logger.info(f"Cached CT planning-region crosswalk as parquet: {parquet}")
    return parquet


def load_ct_planreg_relationship() -> pl.LazyFrame:
    """Connecticut 2020-county <-> 2022-planning-region tract crosswalk.

    Returns:
        LazyFrame with columns:
          - tract_fips_2020: 11-char county-based tract GEOID (09001-09015 prefix)
          - tract_fips_2022: 11-char planning-region tract GEOID (09110-09190 prefix)
          - county_fips_2020: 5-char 2020 county FIPS
          - ce_fips_2022: 5-char 2022 planning-region county-equivalent FIPS

    Downloads and parquet-caches on first use. Idempotent thereafter.
    """
    parquet = _parquet_path()
    if not parquet.exists():
        _build_parquet_cache()
    return pl.scan_parquet(parquet)


def needs_ct_planreg_bridge(early_year: int, late_year: int) -> bool:
    """Does this year pair straddle the 2023/2024 CT planning-region cut?

    HMDA switched CT tract GEOIDs from county-based to planning-region-based in
    its 2024 reporting year. A join that pairs a pre-2024 (county-coded) CT
    record with a 2024+ (planning-region-coded) CT record needs bridging; one
    where both sides fall on the same side of the cut does not.

    Args:
        early_year: The earlier (or seller) reporting year.
        late_year: The later (or purchaser) reporting year.

    Returns:
        True if the pair crosses the cut (``early_year < 2024 <= late_year``).
    """
    return early_year < PLANREG_FIRST_YEAR <= late_year


def to_planning_region(lf: pl.LazyFrame, tract_col: str = "census_tract") -> pl.LazyFrame:
    """Normalize CT census tracts to the 2022 planning-region convention.

    County-coded CT tracts (2020 vintage) are translated to their
    planning-region GEOID; planning-region tracts and all non-CT tracts have no
    match in the crosswalk and pass through unchanged. The operation is
    idempotent, so it is safe to apply to a mixed-year frame: after it, every CT
    record uses the planning-region convention and cross-year `census_tract`
    joins across the 2023/2024 boundary work uniformly.

    Args:
        lf: LazyFrame containing a census-tract column.
        tract_col: Name of the 11-char tract GEOID column. Defaults to
            "census_tract".

    Returns:
        The LazyFrame with `tract_col` rewritten to planning-region convention
        for CT records. Column order and all other columns are preserved.
    """
    # .unique() guards against row multiplication: this is a left join, so a
    # duplicated tract_fips_2020 in the crosswalk would fan one input row into
    # several. The source is a documented 1:1 bijection (879 tracts), so this is
    # defensive only — but it makes the transform safe even if the file is ever
    # reissued with dup rows.
    xw = (
        load_ct_planreg_relationship()
        .select("tract_fips_2020", "tract_fips_2022")
        .unique(subset="tract_fips_2020")
    )
    return (
        lf.join(xw, left_on=tract_col, right_on="tract_fips_2020", how="left")
        .with_columns(pl.coalesce("tract_fips_2022", tract_col).alias(tract_col))
        .drop("tract_fips_2022")
    )


def to_county_vintage(lf: pl.LazyFrame, tract_col: str = "census_tract") -> pl.LazyFrame:
    """Translate CT planning-region tracts back to the 2020 county convention.

    The inverse of `to_planning_region`: planning-region CT tracts (09110-09190)
    are rewritten to their county-coded 2020 GEOID (09001-09015); county-coded
    and non-CT tracts pass through unchanged. Use this to join HMDA (which
    switched to planning regions in 2024) against a source that stayed on the
    traditional counties — e.g. FHA single-family, still county-coded through
    2025. The first 5 chars of the result are then the traditional county FIPS.

    Args:
        lf: LazyFrame containing a census-tract column.
        tract_col: Name of the 11-char tract GEOID column. Defaults to
            "census_tract".

    Returns:
        The LazyFrame with `tract_col` rewritten to county convention for CT
        planning-region records. Other columns are preserved.
    """
    # .unique() guards against row multiplication on the left join (see the note
    # in to_planning_region); the crosswalk is a 1:1 bijection, so this is
    # defensive only.
    xw = (
        load_ct_planreg_relationship()
        .select("tract_fips_2020", "tract_fips_2022")
        .unique(subset="tract_fips_2022")
    )
    return (
        lf.join(xw, left_on=tract_col, right_on="tract_fips_2022", how="left")
        .with_columns(pl.coalesce("tract_fips_2020", tract_col).alias(tract_col))
        .drop("tract_fips_2020")
    )
