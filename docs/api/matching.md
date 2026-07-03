# Matching API Reference

API reference for the Matching subpackage - workflows for linking records across mortgage datasets.

## Table of Contents

- [Overview](#overview)
- [Configuration](#configuration)
- [Available Workflows](#available-workflows)
- [FHA-HMDA Matching](#fha-hmda-matching)
- [HMDA Sellers and Purchasers](#hmda-sellers-and-purchasers)
- [FHFA-HMDA Matching](#fhfa-hmda-matching)
- [Other Workflows](#other-workflows)
- [CLI](#cli)

---

## Overview

The Matching subpackage provides tools for linking records across different mortgage datasets. These workflows enable researchers to track loans across data sources and understand relationships between lenders, servicers, and GSEs.

### Available Matching Workflows

1. **FHA ↔ HMDA**: Match FHA loans to HMDA loan applications
2. **HMDA ↔ FHFA**: Match HMDA loans to FHFA data
3. **HMDA ↔ MBS**: Match HMDA loans to MBS pool data
4. **HMDA ↔ FHLB**: Match HMDA loans to FHLB advance data
5. **MBS ↔ FHFA**: Match MBS pools to FHFA data
6. **MBS ↔ FHLB**: Match MBS pools to FHLB data
7. **HMDA Sellers/Purchasers**: Match loan seller and purchaser data
8. **HMDA Lenders**: Match lender identifiers across datasets

---

## Configuration

### `MatchingConfig`

Configuration class for matching workflows.

**Module:** `mortgage_data_manager.matching.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `MATCHING_DATA_DIR` | `Path` | Root directory for matching data | `MATCHING_DATA_DIR` |
| `MATCHING_OUTPUT_DIR` | `Path` | Output directory for match results | `MATCHING_OUTPUT_DIR` |
| `MATCHING_CACHE_DIR` | `Path` | Cache directory for intermediate files | `MATCHING_CACHE_DIR` |

#### Methods

##### `get_matching_type_dir(matching_type: str) -> Path`

Get data directory for a specific matching workflow.

**Parameters:**
- `matching_type` (str): Workflow name (e.g., 'fha_hmda', 'hmda_sellers_and_purchasers')

**Returns:**
- `Path`: Path to matching workflow directory

**Example:**
```python
from mortgage_data_manager.matching.config import MatchingConfig

fha_hmda_dir = MatchingConfig.get_matching_type_dir('fha_hmda')
print(fha_hmda_dir)  # /data/matching/fha_hmda
```

---

## FHA-HMDA Matching

Match FHA insured loans to HMDA loan applications.

**Module:** `mortgage_data_manager.matching.match_fha_hmda`

### `run_fha_hmda_matching()`

Run the full FHA-HMDA matching pipeline.

**Parameters:**
- `min_year` (int): First year to match (default: 2018)
- `max_year` (int): Last year to match (default: 2024)
- `skip_data_prep` (bool): Skip data preparation if intermediate files exist

**Example:**
```python
from mortgage_data_manager.matching.match_fha_hmda import run_fha_hmda_matching

output_file = run_fha_hmda_matching(min_year=2020, max_year=2024)
```

### `MatchConfig`

Configuration dataclass for customizing matching behavior.

**Fields:**
- `rate_tolerance` (float): Max interest rate difference (default: 0.005)
- `use_fallback_joins` (bool): Use fallback strategies for missing ZIP/rate
- `apply_lender_filter` (bool): Apply lender quality filtering
- `min_lender_matches` (int): Minimum matches per lender (default: 25)
- `apply_timing_filter` (bool): Apply year/month timing filters
- `year_by_year` (bool): Match year-by-year vs all at once

**Preset Configs:**
- `ROUND1_CONFIG`: Strict matching (lender validation, 0.5 bps tolerance)
- `ROUND2_CONFIG`: Relaxed matching (no lender filter, 2.5 bps tolerance)

### Outputs

- **Crosswalk file**: `data/matching/fha_hmda/output/fha_hmda_crosswalk_{min}_{max}.parquet`
- **Lender crosswalk**: LEI ↔ FHA Mortgagee Number mapping

---

## HMDA Sellers and Purchasers

Match HMDA loan seller and purchaser data to track secondary market transactions.

### Post-2018 Matching

```python
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.src.hmda_matching.workflows.post2018 import run_matching

# Run post-2018 matching workflow
run_matching(
    start_year=2018,
    end_year=2024,
    output_dir=None  # Uses default output directory
)
```

### 2007-2017 Matching

```python
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.src.hmda_matching.workflows.period_2007_2017 import run_matching

# Run 2007-2017 matching workflow
run_matching(
    start_year=2007,
    end_year=2017,
    output_dir=None
)
```

### Match Statistics

```python
import polars as pl

# Count matches by round
matches = pl.scan_parquet("data/matches/post2018/**/*.parquet")
by_round = (
    matches
    .group_by("MatchRound")
    .agg(pl.len().alias("count"))
    .sort("MatchRound")
    .collect()
)
print(by_round)
print(f"Total matches: {by_round['count'].sum():,}")
```

### Network Visualization

```python
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.examples import create_network_graph

# Create lender network visualization
import polars as pl

matched_data = pl.read_parquet("output/matched_loans.parquet")
create_network_graph(
    matched_data,
    output_file="figures/lender_network.html"
)
```

---

## FHFA-HMDA Matching

Match HMDA loans to FHFA (Federal Housing Finance Agency) data.

### Post-2018 Workflow

```python
from mortgage_data_manager.matching.match_fhfa_hmda.src.workflows.fhfa_post2018 import run_workflow

# Run FHFA-HMDA matching (post-2018)
run_workflow(
    start_year=2018,
    end_year=2024,
    output_dir=None
)
```

### Pre-2018 Workflow

```python
from mortgage_data_manager.matching.match_fhfa_hmda.src.workflows.fhfa_pre2018 import run_workflow

# Run FHFA-HMDA matching (pre-2018)
run_workflow(
    start_year=2010,
    end_year=2017,
    output_dir=None
)
```

### FHLB Workflow

```python
from mortgage_data_manager.matching.match_fhfa_hmda.src.workflows.fhlb import run_workflow

# Match HMDA to FHLB (Federal Home Loan Bank) data
run_workflow(
    start_year=2015,
    end_year=2024,
    output_dir=None
)
```

---

## Other Workflows

### HMDA-MBS Matching

Match HMDA loans to MBS (Mortgage-Backed Securities) pool data.

```python
# GNMA matching
from mortgage_data_manager.matching.match_hmda_mbs.match_hmda_mbs import match_gnma

matched_gnma = match_gnma(
    hmda_data=hmda_df,
    gnma_data=gnma_df,
    year=2020
)
```

### MBS-FHFA Matching

Match MBS pools to FHFA data.

```python
# FNMA matching (skeleton - not yet implemented)
from mortgage_data_manager.matching.match_mbs_fhfa import match_fhfa_fnma

# match_fhfa_fnma(file_fnma, file_fhfa, save_folder, file_suffix='')
```

### MBS-FHLB Matching

Match MBS pools to FHLB data.

```python
# GNMA-FHLB matching
from mortgage_data_manager.matching.match_mbs_fhlb.src.matching_gnma import match_gnma_fhlb

matched = match_gnma_fhlb(
    gnma_data=gnma_df,
    fhlb_data=fhlb_df,
    year=2020
)
```

---

## CLI

### Commands

**Module:** `mortgage_data_manager.matching.cli.main`

#### `mortgage-data match info`

Show information about available matching workflows.

```bash
mortgage-data match info
```

Displays a table of all available matching workflows with descriptions.

#### `mortgage-data match list-workflows`

List all available matching workflow modules.

```bash
mortgage-data match list-workflows
```

Shows the full Python module path for each workflow.

---

## Complete Example

End-to-end matching workflow:

```python
import polars as pl
from pathlib import Path
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.config import MatchingConfig

# Setup
configure_logging(level="INFO")
logger = get_logger(__name__)
config = MatchingConfig()

# Example 1: FHA-HMDA Matching
logger.info("Running FHA-HMDA matching...")
from mortgage_data_manager.matching.match_fha_hmda import run_fha_hmda_matching

run_fha_hmda_matching(min_year=2020, max_year=2024)

# Read results
fha_hmda_dir = config.get_matching_type_dir('fha_hmda')
matched = pl.read_parquet(fha_hmda_dir / "output" / "matched.parquet")
print(f"Matched {len(matched):,} FHA-HMDA records")

# Example 2: HMDA Sellers and Purchasers
logger.info("Running HMDA sellers/purchasers matching...")
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.src.hmda_matching.workflows.post2018 import run_matching as run_sp_matching

run_sp_matching(start_year=2018, end_year=2024)

# Read results
sp_dir = config.get_matching_type_dir('hmda_sellers_and_purchasers')
sellers = pl.read_parquet(sp_dir / "output" / "sellers_matched.parquet")
purchasers = pl.read_parquet(sp_dir / "output" / "purchasers_matched.parquet")

# Analyze secondary market activity
secondary_market_summary = (
    sellers
    .group_by("seller_lei")
    .agg([
        pl.count().alias("num_loans_sold"),
        pl.col("loan_amount").sum().alias("total_amount_sold"),
    ])
    .sort("total_amount_sold", descending=True)
    .head(10)
)

print("\nTop 10 Sellers by Volume:")
print(secondary_market_summary)
```

---

## Best Practices

1. **Data Quality**: Ensure source data is clean before matching
2. **Match Criteria**: Adjust tolerance levels based on data quality
3. **Validation**: Always validate match results and check match rates
4. **Documentation**: Document matching criteria and parameters
5. **Incremental Matching**: Match one year at a time for large datasets
6. **Quality Metrics**: Track match quality scores to assess confidence
7. **Unmatched Analysis**: Investigate why records don't match

## Common Matching Criteria

Typical fields used for matching across workflows:

| Field | Tolerance | Notes |
|-------|-----------|-------|
| Loan Amount | $100-$1,000 | Varies by workflow |
| Interest Rate | 0.1-0.25% | Floating point precision issues |
| Origination Date | 30-90 days | Reporting delays |
| Property State | Exact | Geographic matching |
| Property County | Exact | Geographic matching |
| Loan Purpose | Exact | Categorical |
| Occupancy Type | Exact | Categorical |

## See Also

- [Core API](core.md) - Shared utilities
- [HMDA API](hmda.md) - HMDA data access
- [FHA API](fha.md) - FHA data access
- [Configuration Guide](../configuration.md) - Environment variables
