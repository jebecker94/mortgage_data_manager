# Core Utilities API Reference

`core` is the kernel every subpackage shares: configuration, the medallion-layer
helpers, downloads and the raw download ledger, logging, type and date helpers,
reference tables used by more than one source, and geocoding.

## Configuration

::: mortgage_data_manager.core.config

## Medallion architecture

::: mortgage_data_manager.core.medallion

## Logging

::: mortgage_data_manager.core.logging

## Downloads

::: mortgage_data_manager.core.download

## Raw download ledger

::: mortgage_data_manager.core.download_ledger

## Types and silver specs

::: mortgage_data_manager.core.types

## Dates

::: mortgage_data_manager.core.dates

## Harmonized code spaces

::: mortgage_data_manager.core.harmonized

## Scalars

::: mortgage_data_manager.core.scalars

## Amortization

::: mortgage_data_manager.core.amortization

## Reference tables

::: mortgage_data_manager.core.reference.tract_zip3

::: mortgage_data_manager.core.reference.census_tract

::: mortgage_data_manager.core.reference.ct_planning_region

::: mortgage_data_manager.core.reference.states

::: mortgage_data_manager.core.reference.gnma_pool_types

::: mortgage_data_manager.core.reference.avery

## Geocoding

::: mortgage_data_manager.core.geocoding.api

::: mortgage_data_manager.core.geocoding.providers

::: mortgage_data_manager.core.geocoding.cache

## Example

A build step in the shape every subpackage uses: skip work already on disk, stay lazy,
and write hive-partitioned parquet.

```python
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.medallion import should_process_file, write_hive_partitioned
from mortgage_data_manager.sources.fha.config import FHAConfig

configure_logging(level="INFO")
logger = get_logger(__name__)

out_dir = MortgageDataConfig.OUTPUT_DIR / "fha_lender_counts"
if should_process_file(out_dir, overwrite=False):
    lf = (
        pl.scan_parquet(FHAConfig.FHA_SILVER_DIR / "single_family" / "**" / "*.parquet")
        .group_by("Year", "Month", "Originating Mortgagee Number")
        .agg(pl.len().alias("loans"))
    )
    write_hive_partitioned(lf, out_dir, partition_cols=["Year"])
    logger.info("Wrote %s", out_dir)
```

## See Also

- [Configuration Guide](../configuration.md): every setting and its default
- [Architecture](../architecture.md): the medallion layers and package layout
