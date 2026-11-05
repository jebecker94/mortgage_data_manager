"""FHFA data transformation functions - silver layer.

Each of the 7 FHFA datasets (sf_a/b/c/d, mf_c, mf_property_b, mf_unit_b) is built
by: scanning bronze, deriving ``Census Year`` from the (pre-rename) census-tract
column names, standardizing column names to snake_case via the rename dictionary
(``unique_column_names_dict.csv``), then applying that dataset's declarative
`~mortgage_data_manager.core.types.SilverSpec` from ``config.py``
(``FHFA_SILVER_SPECS``) — sentinel null-mapping, geo identifiers to zero-padded
``Utf8``, category codes to ``Int8``, continuous fields to ``Float32``, money to
``Int32``/``Float64``, conform to the per-dataset target — and asserting the
schema. Each dataset gets its own spec rather than one global sentinel map,
because a global map does not fit the MF datasets and cannot distinguish a
continuous field from a same-named bucketed code.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference.census_tract import tract_geoid
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
from mortgage_data_manager.sources.fhfa.config import (
    DATASET_MAP,
    FHFA_SILVER_SPECS,
    FHFAConfig,
)

logger = get_logger(__name__)

# Default columns to drop based on substring matching
DEFAULT_DROP_PATTERNS: list[str] = []

# Census vintages whose tract columns trigger a derived ``Census Year``.
_CENSUS_VINTAGES = (1990, 2000, 2010, 2020, 2030, 2040)

#: Columns derived from typed silver columns, per dataset. Each is declared in the
#: dataset's spec, which places it; the expression fills it after typing.
_DERIVED: dict[str, list[pl.Expr]] = {
    # The 11-character GEOID of the row's own census vintage (``Census Year``);
    # null when the state, county or tract is missing.
    "sf_c": [tract_geoid("state_code", "county", "census_tract").alias("census_tract_geoid")],
}


def _load_rename_map() -> dict[str, str]:
    """Load the raw-FHFA -> snake_case column rename dictionary."""
    df = pl.read_csv(FHFAConfig.FHFA_SCHEMAS_DIR / "unique_column_names_dict.csv")
    return dict(zip(df["Field Name"].to_list(), df["Standardized Name"].to_list()))


def save_to_silver(
    dataset: str,
    years: list[int],
    bronze_dir: Path | None = None,
    silver_dir: Path | None = None,
    *,
    drop_patterns: list[str] | None = None,
    additional_drop_patterns: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Transform bronze dataframe to silver layer.

    A year that fails is logged and counted, and the remaining years still run.

    Args:
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b').
        years: Years to load.
        bronze_dir: Bronze data directory. Defaults to ``FHFAConfig.FHFA_BRONZE_DIR / dataset``.
        silver_dir: Silver data directory. Defaults to ``FHFAConfig.FHFA_SILVER_DIR / dataset``.
        drop_patterns: Substring patterns for dropping columns. If provided,
            replaces ``DEFAULT_DROP_PATTERNS``.
        additional_drop_patterns: Patterns to add to ``DEFAULT_DROP_PATTERNS``.
        overwrite: Whether to replace existing silver files. Default False.

    Returns:
        Year counts: ``written``, ``skipped`` (silver already exists), ``missing``
        (no bronze file) and ``failed``.

    Raises:
        ValueError: If ``dataset`` is unknown.
    """
    bronze_dir = bronze_dir if bronze_dir else FHFAConfig.FHFA_BRONZE_DIR / dataset
    silver_dir = silver_dir if silver_dir else FHFAConfig.FHFA_SILVER_DIR / dataset
    silver_dir.mkdir(parents=True, exist_ok=True)

    if dataset not in DATASET_MAP:
        raise ValueError(f"Unknown dataset: {dataset}. Valid types: {list(DATASET_MAP.keys())}")

    if drop_patterns is not None:
        patterns = drop_patterns
    else:
        patterns = DEFAULT_DROP_PATTERNS.copy()
        if additional_drop_patterns:
            patterns.extend(additional_drop_patterns)

    rename_map = _load_rename_map()

    counts = {"written": 0, "skipped": 0, "missing": 0, "failed": 0}
    for year in years:
        silver_path = silver_dir / f"{dataset}_{year}.parquet"
        if not should_process_file(silver_path, overwrite):
            logger.info("Silver file already exists for %s year %d: %s", dataset, year, silver_path)
            counts["skipped"] += 1
            continue

        bronze_path = bronze_dir / f"{dataset}_{year}.parquet"
        if not bronze_path.exists():
            logger.info("Bronze file doesn't exist for %s year %d: %s", dataset, year, bronze_path)
            counts["missing"] += 1
            continue

        try:
            _build_silver_year(bronze_path, silver_path, dataset, rename_map, patterns)
        except Exception as e:
            logger.error("Failed to build silver for %s year %d: %s", dataset, year, e)
            counts["failed"] += 1
            continue
        logger.info("Saved silver file for %s year %d: %s", dataset, year, silver_path)
        counts["written"] += 1

    return counts


def _build_silver_year(
    bronze_path: Path,
    silver_path: Path,
    dataset: str,
    rename_map: dict[str, str],
    patterns: list[str],
) -> None:
    """Build one dataset-year's silver parquet from its bronze file."""
    spec = FHFA_SILVER_SPECS.get(dataset)
    lf = pl.scan_parquet(bronze_path)
    raw_cols = lf.collect_schema().names()

    # Derive Census Year from the original (pre-rename) census-tract column
    # names (e.g. "2020 Census Tract - Median Income" -> 2020).
    for census_year in _CENSUS_VINTAGES:
        if any(f"{census_year} census" in c.lower() for c in raw_cols):
            lf = lf.with_columns(pl.lit(census_year).alias("Census Year"))

    # Standardize column names raw FHFA -> snake_case (one clean rename, so the
    # spec sentinels/casts key uniformly across SF and MF).
    present = {raw: std for raw, std in rename_map.items() if raw in raw_cols}
    if present:
        lf = lf.rename(present)

    # Optional substring-based column drops (default none).
    if patterns:
        cols = lf.collect_schema().names()
        to_drop = [c for c in cols if any(p in c for p in patterns)]
        if to_drop:
            logger.info("Dropping %d columns based on patterns", len(to_drop))
            lf = lf.drop(to_drop)

    # Apply close-to-final dtypes + sentinel masking + geo zero-padding and
    # conform to the dataset target; then assert the output schema.
    if spec is not None:
        lf = apply_silver_types(lf, spec).with_columns(_DERIVED.get(dataset, []))
        enforce_schema(lf.collect_schema(), spec.target, name=f"fhfa/{dataset}")
    else:
        logger.warning("No silver spec for dataset %s; writing un-typed", dataset)

    with atomic_output(silver_path) as tmp:
        lf.sink_parquet(tmp)
