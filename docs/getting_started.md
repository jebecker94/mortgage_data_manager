# Getting Started

Install the package, download a first dataset, and read it from Python.

## Installation

The base install has the shared utilities and the CLI. Each data source's own
dependencies are an extra, so install the ones you need:

```bash
uv pip install -e ".[hmda]"            # HMDA only
uv pip install -e ".[hmda,fha,mbs]"    # several sources
uv pip install -e ".[all,dev]"         # everything, plus the dev toolchain
```

Plain `pip install -e ...` works too. The extras are `hmda`, `fha`, `mbs` (GNMA, FHFA,
FNMA, FHLMC and UMBS), `matching`, `hud` and `analytics`; `all` installs every one,
`dev` adds ruff, pytest, mypy, ipython and jupyter, and `docs` adds the documentation
build. `pyproject.toml` has the full lists.

## Where data goes

By default everything is written to `data/` in the project root, which is created on
first use and is not tracked in git. To keep data somewhere else, such as an external
drive, set `MORTGAGE_DATA_DIR` in a `.env` file in the project root:

```bash
# .env
MORTGAGE_DATA_DIR=/Volumes/ExternalDrive/mortgage_data
```

A symlink works too: `ln -s /Volumes/ExternalDrive/mortgage_data data`. The
[configuration guide](configuration.md) covers per-source overrides.

Each source is processed in stages: the downloaded files (**raw**), a parquet copy
(**bronze**), and a cleaned, partitioned version for analysis (**silver**). You will
almost always read silver. [Architecture](architecture.md#medallion-layers) describes the
layers.

## Quick start

### HMDA

```bash
mortgage-data hmda download --dry-run                       # list what would be fetched
mortgage-data hmda download --min-year 2020 --max-year 2024
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024
```

HMDA silver keeps every CFPB release of a year side by side, so read it through
`hmda.load`, which picks one release per year. Globbing the directory would stack
several releases of the same year on top of each other.

```python
import polars as pl
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

df = scan_hmda_years("post2018", 2020, 2024)

# California records in 2024 (state_code is the postal abbreviation)
ca_2024 = df.filter(
    (pl.col("activity_year") == 2024) & (pl.col("state_code") == "CA")
).collect()
print(f"{len(ca_2024):,} records in CA for 2024")
```

### FHA

```bash
mortgage-data fha pipeline single-family --min-year 2020    # download, bronze and silver
```

```python
import polars as pl
from mortgage_data_manager.sources.fha.config import FHAConfig

fha = pl.scan_parquet(FHAConfig.FHA_SILVER_DIR / "single_family" / "**" / "*.parquet")

# Endorsements and dollar volume by lender
lender_stats = (
    fha.group_by("Originating Mortgagee Number", "Originating Mortgagee")
    .agg(
        pl.len().alias("loan_count"),
        pl.col("Mortgage Amount").sum().alias("total_volume"),
    )
    .sort("loan_count", descending=True)
    .collect()
)
print(lender_stats.head(10))
```

### GNMA

GNMA downloads need a free Ginnie Mae profile; put the credentials in `.env` first (see
[Manual Downloads](manual_downloads.md#ginnie-mae-disclosure-data)).

```bash
mortgage-data gnma pipeline monthly      # or run download, bronze and silver separately
```

GNMA silver is split by file prefix and then by record type
(`{prefix}/{record_type}/`). Each record type has its own columns, so read one at a
time:

```python
import polars as pl
from mortgage_data_manager.sources.gnma.config import GNMAConfig

pools = pl.scan_parquet(
    GNMAConfig.GNMA_SILVER_DIR / "monthly" / "P" / "*.parquet", extra_columns="ignore"
)
print(pools.group_by("pool_type").len().collect())
```

### Freddie Mac

Freddie Mac's loan-level files have to be downloaded by hand from its Clarity portal
(`mortgage-data fhlmc download` prints the steps, and [Manual
Downloads](manual_downloads.md#freddie-mac-single-family-loan-level-dataset) has them in
full). Once the files are in the raw directory:

```bash
mortgage-data fhlmc pipeline --min-year 2020 --max-year 2024
```

```python
import polars as pl
from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig

orig = pl.scan_parquet(FHLMCConfig.FHLMC_SILVER_DIR / "origination" / "**" / "*.parquet")
print(orig.head().collect())
```

### Matching

The matching workflows link records across sources. For example, the seller-purchaser
workflow pairs an HMDA origination with the HMDA record of the institution that bought
the loan:

```bash
mortgage-data match info                                      # list the workflows
mortgage-data match hmda-sellers-purchasers run --min-year 2020 --max-year 2024
```

The crosswalk is written under
`crosswalk/hmda_sellers_purchasers/hmda_sellers_purchasers_crosswalk_post2018/`,
partitioned by match round and seller activity year:

```python
import polars as pl
from mortgage_data_manager.matching.match_hmda_sellers_purchasers import (
    HMDASellersPurchasersMatchingConfig,
)

crosswalk_dir = HMDASellersPurchasersMatchingConfig.get_crosswalk_dir("post2018")
pairs = pl.scan_parquet(crosswalk_dir / "**" / "*.parquet", hive_partitioning=True)
print(pairs.group_by("match_round").len().sort("match_round").collect())
```

Each workflow's method, output schema and match rates are in the
[matching docs](matching/README.md).

## Examples

### HMDA denial rates by loan type

```python
import polars as pl
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

df = scan_hmda_years("post2018", 2018, 2024)

denial_rates = (
    df.filter(pl.col("action_taken").is_in([1, 3]))  # originated or denied
    .group_by("activity_year", "loan_type")
    .agg(
        pl.len().alias("applications"),
        (pl.col("action_taken") == 3).mean().alias("denial_rate"),
    )
    .sort("activity_year", "loan_type")
    .collect()
)
print(denial_rates)
```

### FHA lender market share

```python
import polars as pl
from mortgage_data_manager.sources.fha.config import FHAConfig

fha = pl.scan_parquet(FHAConfig.FHA_SILVER_DIR / "single_family" / "**" / "*.parquet")

market_share = (
    fha.group_by("Year", "Originating Mortgagee")
    .agg(pl.col("Mortgage Amount").sum().alias("volume"))
    .with_columns((pl.col("volume") / pl.col("volume").sum().over("Year")).alias("share"))
    .filter(pl.col("Year") == 2024)
    .sort("share", descending=True)
    .collect()
)
print(market_share.head(20))
```

### Who buys matched loans

Join the seller side of the seller-purchaser crosswalk back to HMDA to see what kind of
institution bought each loan. `scan_hmda_for_index` reads exactly the HMDA releases the
crosswalk was built from.

```python
import polars as pl
from mortgage_data_manager.sources.hmda.load import scan_hmda_for_index
from mortgage_data_manager.matching.match_hmda_sellers_purchasers import (
    HMDASellersPurchasersMatchingConfig,
)

crosswalk_dir = HMDASellersPurchasersMatchingConfig.get_crosswalk_dir("post2018")
pairs = pl.scan_parquet(crosswalk_dir / "**" / "*.parquet", hive_partitioning=True)
sellers = scan_hmda_for_index(pairs.select(pl.col("HMDAIndex_s").alias("HMDAIndex")))

by_purchaser = (
    pairs.join(sellers, left_on="HMDAIndex_s", right_on="HMDAIndex")
    .group_by("purchaser_type")
    .len()
    .sort("len", descending=True)
    .collect()
)
print(by_purchaser)
```

The GSEs don't file HMDA, so sales to them never appear in this crosswalk. Other
workflows link HMDA to agency data, such as the
[HMDA-FHA crosswalk](matching/hmda_fha_matching.md).

## Getting help

Every command has `--help`, and `mortgage-data info` shows the resolved project and data
directories:

```bash
mortgage-data --help
mortgage-data fha pipeline --help
mortgage-data info
```

The [CLI reference](cli_reference.md) lists every command, and the API pages document
each subpackage's Python functions.

## Troubleshooting

**`ModuleNotFoundError` for a package such as `pymupdf` or `fastexcel`.** The
subpackage's extra isn't installed. Install it, for example `uv pip install -e ".[mbs]"`.

**`PermissionError` when downloading.** The data directory isn't writable. Check who
owns it with `ls -ld data/` (or the path in `MORTGAGE_DATA_DIR`).

**Running out of memory.** Stay lazy until the end: scan, filter and aggregate first,
then `collect()` only the result. `collect()` on a whole multi-year scan loads all of it.
