# FHA API Reference

API reference for the FHA (Federal Housing Administration) subpackage, covering the HUD single-family
and HECM portfolio snapshots.

---

## Overview
HUD publishes two monthly loan-level Excel snapshots of the FHA-insured portfolio. This subpackage
downloads them, converts them to parquet, and cleans them into a hive-partitioned silver layer.

| Dataset | Identifier | Contents |
| --- | --- | --- |
| Single family | `single_family` | Forward FHA-insured originations (purchase and refinance) |
| HECM | `hecm` | Home Equity Conversion Mortgages (reverse mortgages) |

A third, separate asset is the **lender list**: a roster of currently FHA-approved mortgagees scraped
from HUD's Lender List Search. It is a point-in-time snapshot of active approvals, not a historical
record, so it cannot reconstruct lender names for older vintages.

### Pipeline

```
raw (.xls/.xlsx/.zip)  ->  bronze (parquet, one file per month)  ->  silver (hive-partitioned by Year/Month)
```

Each monthly workbook holds several sheets; only the loan-level records sheet is read. Bronze applies
minimal typing and adds `FHA_Index`. Silver concatenates the requested year range, normalises county
names, attaches county and state FIPS codes, applies the `SilverSpec` dtypes (including restoring the
zero-padding on identifiers that bronze's integer storage strips), and partitions by `Year` and
`Month`.

---

## Configuration

::: mortgage_data_manager.sources.fha.config

---

## Download

::: mortgage_data_manager.sources.fha.download

::: mortgage_data_manager.sources.fha.lender_list

---

## Bronze layer

::: mortgage_data_manager.sources.fha.import_bronze

---

## Silver layer

::: mortgage_data_manager.sources.fha.import_silver

---

## Pipeline

::: mortgage_data_manager.sources.fha.pipeline

---

## Utilities

::: mortgage_data_manager.sources.fha.utils

---

## CLI
Commands, options and examples for `mortgage-data fha` live in the
[CLI Reference](../cli_reference.md#mortgage-data-fha). `mortgage-data fha --help` is
authoritative.

---

## Data Structure
### Layers

| Layer | Location | Format |
| --- | --- | --- |
| Raw | `{FHA_RAW_DIR}/{dataset}/` | HUD `.xls` / `.xlsx` / `.zip` |
| Bronze | `{FHA_BRONZE_DIR}/{dataset}/` | One parquet per monthly snapshot |
| Silver | `{FHA_SILVER_DIR}/{dataset}/Year=YYYY/Month=M/` | Hive-partitioned parquet |
| Lender list (raw) | `{FHA_RAW_DIR}/lender_list/{snapshot}/` | Per-state HTML from HUD's Lender List Search |
| Lender list (bronze) | `{FHA_BRONZE_DIR}/lender_list/lender_list_{snapshot}.parquet` | One parquet per scrape (`mortgage-data fha lender-list`) |

Partition keys are retained inside each file, so readers can scan with `hive_partitioning=False` and
still filter on `Year` and `Month`.

### `FHA_Index`

A synthetic per-row identifier assigned at bronze time: the snapshot period followed by a
seven-digit row number, prefixed with `H` for HECM.

```
Single family:  20230801_0000001
HECM:          H20230801_0000001
```

It is unique within a snapshot file, not across the corpus, and row numbers are not stable across
rebuilds.

### Silver Schema -- Single Family

| Column | Type |
| --- | --- |
| `Property State` | `String` |
| `Property City` | `String` |
| `Property County` | `String` |
| `Property Zip` | `String` |
| `Originating Mortgagee` | `String` |
| `Originating Mortgagee Number` | `String` |
| `Sponsor Name` | `String` |
| `Sponsor Number` | `String` |
| `Down Payment Source` | `Enum` |
| `Non Profit Number` | `String` |
| `Product Type` | `Enum` |
| `Loan Purpose` | `Enum` |
| `Property Type` | `Enum` |
| `Interest Rate` | `Float32` |
| `Mortgage Amount` | `Int64` |
| `Year` | `Int16` |
| `Month` | `Int16` |
| `FHA_Index` | `String` |
| `FIPS` | `String` |
| `state_fips` | `String` |
| `Date` | `Date` |

### Silver Schema -- HECM

| Column | Type |
| --- | --- |
| `FHA_Index` | `String` |
| `Property State` | `String` |
| `Property County` | `String` |
| `Property City` | `String` |
| `Property Zip` | `String` |
| `Originating Mortgagee` | `String` |
| `Originating Mortgagee Number` | `String` |
| `Sponsor Name` | `String` |
| `Sponsor Number` | `String` |
| `Sponsor Originator` | `String` |
| `Current Servicer ID` | `String` |
| `Previous Servicer ID` | `String` |
| `HECM Type` | `Enum` |
| `Rate Type` | `Enum` |
| `Interest Rate` | `Float32` |
| `Initial Principal Limit` | `Float64` |
| `Maximum Claim Amount` | `Float64` |
| `Year` | `Int16` |
| `Month` | `Int16` |
| `FIPS` | `String` |
| `state_fips` | `String` |
| `Date` | `Date` |
| `NMLS` | `Int32` |
| `RefinanceType` | `Enum` |
| `Standard/Saver` | `Enum` |
| `Purchase/Refinance` | `Enum` |

Identifier-like columns (`Property Zip`, mortgagee and sponsor numbers, servicer IDs) are `String`
so leading zeros survive. Cast them yourself if you need numerics.

#### Zero-padded identifiers

The bronze layer stores these as integers, which strips the leading zeros the published form carries.
Silver restores them to their canonical width:

| Column | Width | Namespace |
| --- | --- | --- |
| `Originating Mortgagee Number` | 5 | HUD mortgagee ID — joins `lender_list`'s `mortgagee_id` |
| `Sponsor Number` | 5 | HUD mortgagee ID |
| `Non Profit Number` (SF) | 9 | **an EIN**, not a mortgagee ID — its own namespace, hence 9 |
| `Property Zip` | 5 | ZIP |
| `FIPS` | 5 | county FIPS |
| `state_fips` | 2 | state FIPS, the first two digits of `FIPS` |

Padding is idempotent, so code that already `zfill`s these is unaffected; and it is a bijection on the
observed data (an integer round-trip cannot leave a padded value and its stripped form coexisting), so
no two identifiers collide. Values wider than the canonical width — one 6-character `Sponsor Number`
— are left alone rather than truncated. HECM's `Current Servicer ID` / `Previous Servicer ID` are
**not** padded, so they run 6–10 characters. Their canonical form is the 10-digit one — a 5-digit HUD
mortgagee number plus a 5-digit branch — and the shorter values are the same integer round-trip with
the leading zeros stripped, so `zfill(10)` recovers it (`6800006` → `0006800006`).

#### Absent values are null

Names and county (`Originating Mortgagee`, `Sponsor Name`, `Property County`) store absence as null,
not as the empty string, so `is_null()` / `null_count()` / `drop_nulls()` and the null-aware
aggregates report coverage correctly. `Property County` and `FIPS` are null together except where a
county name is present but unresolvable, which `add_county_fips` warns about by name.

`state_fips` is derived from `FIPS` rather than looked up separately, so the two can never disagree.
It exists because `Property State` is a postal abbreviation, which will not join a FIPS-keyed source
(pre-2018 HMDA's `state_code`, for one) without a lookup.

### Categorical Values

| Column | Levels |
| --- | --- |
| `Loan Purpose` (SF) | `Purchase`, `Refi_FHA`, `Refi_Conv_Curr` |
| `Property Type` (SF) | `Single Family`, `Condo`, `Rehabilitation`, `H4H`, `Other` |
| `Product Type` (SF) | `Fixed Rate`, `Adjustable Rate` |
| `Down Payment Source` (SF) | `Borrower`, `Relative`, `Gov Asst`, `Non Profit`, `Employer` |
| `HECM Type` | `Hecm`, `Hecm Traditional`, `Hecm for Purchase`, `Hecm for Refinance`, `Standard` |
| `Rate Type` (HECM) | `Adjustable`, `Annual Adjustable`, `Fixed Rate`, `Monthly Adjustable`, `Other` |
| `RefinanceType` (HECM) | `Hecm`, `Purchase`, `Refinance`, `Standard` |
| `Standard/Saver` (HECM) | `Hecm New`, `Saver`, `Standard`, `Other` |
| `Purchase/Refinance` (HECM) | `Purchase`, `Refinance` |

Values outside a level set become null, since the specs cast with `strict=False`.

### Loading Data

```python
import polars as pl
from mortgage_data_manager.sources.fha.config import FHAConfig

lf = pl.scan_parquet(FHAConfig.FHA_SILVER_DIR / "single_family" / "**/*.parquet")

recent = (
    lf.filter(pl.col("Year") >= 2020)
    .group_by("Year", "Property State")
    .agg(pl.len().alias("loans"), pl.col("Mortgage Amount").sum().alias("volume"))
    .collect()
)
```

---

## Coverage and Known Issues
### Temporal Coverage

| Dataset | Range | Completeness |
| --- | --- | --- |
| Single family | 2010-05 onward | Complete, no missing months |
| HECM | 2010-01 onward | 37 missing months (below) |

Single-family silver is over 17 million rows. Scan and filter on `Year` / `Month` rather
than collecting the whole layer.

### HECM Gaps

HECM is **not a continuous series**. Thirty-seven months are absent, and none of them are recoverable
from HUD:

- **2014-09 through 2017-07** -- 35 consecutive months (all of 2015 and 2016, the tail of 2014, the
  first seven months of 2017). HUD no longer publishes these snapshots.
- **2014-01** -- also no longer published.
- **2025-10** -- HUD publishes the file, but its records sheet contains zero rows at source.

Anything spanning 2014-2017 has a three-year hole in the middle. Interpolation and forward-fill will
silently invent data across it.

### Source Quirks Handled Automatically

These are noted because they explain the shape of the bronze converters, not because callers need to
act on them:

- **Banner rows.** The 2009-05 to 2011-07 HECM workbooks open the records sheet with one or two
  advisory rows, so the real header is not the first row. The reader probes the leading rows for a
  known column signature.
- **Misfiled sheet names.** The 2013-12 HECM workbook stores its records under
  `Report Generator December 2013`; other vintages use that same sheet name for a short cover page.
  Sheet selection therefore falls back to matching on content.
- **Column-name drift.** HUD respells columns across vintages -- underscores for spaces, footnote
  asterisks (`NMLS*`, `RefinanceType*`), and typos (`Sponosr Number`). `clean_hecm_sheets` folds
  these onto canonical names.
- **Fabricated identifiers in 2013-12.** That workbook carries `Current Servicer ID`,
  `Previous Servicer ID` and `NMLS` as an Excel linear-trend fill dragged down from the first few
  genuine rows, so most of their values are a function of row position -- they run negative and carry
  a fractional part. Bronze nulls every negative or fractional value in those three columns (13 values
  remain across the three; a no-op on every other vintage), so that month's
  servicer and NMLS coverage is near-empty by design rather than mis-parsed. Its other identifier
  columns and its 4,223 rows are unaffected.

---

## See Also
- [HMDA API Reference](hmda.md) -- companion origination data
- [USPS Crosswalk API Reference](usps_crosswalk.md) -- ZIP/tract crosswalks
- [HMDA-FHA matching](../matching/hmda_fha_matching.md) -- linking FHA snapshots to HMDA records
- [Configuration guide](../configuration.md)
