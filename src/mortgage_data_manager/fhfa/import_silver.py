"""FHFA data transformation functions - silver layer.

Each of the 7 FHFA datasets (sf_a/b/c/d, mf_c, mf_property_b, mf_unit_b) is built
by: scanning bronze, deriving ``Census Year`` from the (pre-rename) census-tract
column names, standardizing column names to snake_case via the rename dictionary
(``unique_column_names_dict.csv``), then applying that dataset's declarative
:class:`~mortgage_data_manager.core.types.SilverSpec` from ``config.py``
(``FHFA_SILVER_SPECS``) — sentinel null-mapping, geo identifiers to zero-padded
``Utf8``, category codes to ``Int8``, continuous fields to ``Float32``, money to
``Int32``/``Float64``, conform to the per-dataset target — and asserting the
schema. The spec layer replaced the former global ``SENTINELS_TO_NULL`` dict,
which never matched the MF datasets and could not distinguish a continuous field
from a same-named bucketed code.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
from mortgage_data_manager.fhfa.config import (
    BRONZE_DIR,
    DATASET_MAP,
    FHFA_SILVER_SPECS,
    SCHEMAS_DIR,
    SILVER_DIR,
)

logger = get_logger(__name__)

# Default columns to drop based on substring matching
DEFAULT_DROP_PATTERNS: list[str] = []

# Census vintages whose tract columns trigger a derived ``Census Year``.
_CENSUS_VINTAGES = (1990, 2000, 2010, 2020, 2030, 2040)


def _load_rename_map() -> dict[str, str]:
    """Load the raw-FHFA -> snake_case column rename dictionary."""
    df = pl.read_csv(SCHEMAS_DIR / "unique_column_names_dict.csv")
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
) -> None:
    """Transform bronze dataframe to silver layer.

    Args:
        dataset: Dataset (e.g., 'sf_c', 'mf_property_b').
        years: Years to load.
        bronze_dir: Bronze data directory. Defaults to ``BRONZE_DIR / dataset``.
        silver_dir: Silver data directory. Defaults to ``SILVER_DIR / dataset``.
        drop_patterns: Substring patterns for dropping columns. If provided,
            replaces ``DEFAULT_DROP_PATTERNS``.
        additional_drop_patterns: Patterns to add to ``DEFAULT_DROP_PATTERNS``.
        overwrite: Whether to replace existing silver files. Default False.
    """
    bronze_dir = bronze_dir if bronze_dir else BRONZE_DIR / dataset
    silver_dir = silver_dir if silver_dir else SILVER_DIR / dataset
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
    spec = FHFA_SILVER_SPECS.get(dataset)

    for year in years:
        silver_path = silver_dir / f"{dataset}_{year}.parquet"
        if not should_process_file(silver_path, overwrite):
            logger.info("Silver file already exists for %s year %d: %s", dataset, year, silver_path)
            continue

        bronze_path = bronze_dir / f"{dataset}_{year}.parquet"
        if not bronze_path.exists():
            logger.info("Bronze file doesn't exist for %s year %d: %s", dataset, year, bronze_path)
            continue

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
            lf = apply_silver_types(lf, spec)
            enforce_schema(lf.collect_schema(), spec.target, name=f"fhfa/{dataset}")
        else:
            logger.warning("No silver spec for dataset %s; writing un-typed", dataset)

        lf.sink_parquet(silver_path)
        logger.info("Saved silver file for %s year %d: %s", dataset, year, silver_path)
