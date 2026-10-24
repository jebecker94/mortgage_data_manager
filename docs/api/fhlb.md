# FHLB API Reference

## Overview
The FHLB subpackage downloads and processes Federal Home Loan Bank data published by FHFA:

| Dataset | Description | Raw format | Years | Layers |
|---------|-------------|------------|-------|--------|
| `ama` | Acquired Member Assets loan-level data (FHLBank public use database) | CSV | 2009-2025 | raw, bronze, silver |
| `members` | Quarterly FHLB member-institution lists | Excel (XLS/XLSX) | 2009Q4-present | raw, bronze |
| `dictionaries` | AMA data definitions | PDF / XLS | one per AMA year | raw only (documentation) |

Members has no silver builder; dictionaries are downloaded for reference and never built.

## Source documentation
FHFA publishes the AMA data files and one data-definitions document per year (XLS for the
earliest years, PDF since) on its
[Public Use Database page](https://www.fhfa.gov/data/pudb), under the FHLBank tables.
`mortgage-data fhlb download -t dictionaries` fetches every definitions file linked there into
`{FHLB_DATA_DIR}/dictionaries/`. Member lists are published on FHFA's
[FHLB Membership Data page](https://www.fhfa.gov/data/fhlb-membership).

## Configuration

::: mortgage_data_manager.sources.fhlb.config

## Download

::: mortgage_data_manager.sources.fhlb.download

## Bronze layer

::: mortgage_data_manager.sources.fhlb.import_bronze

## Silver layer

::: mortgage_data_manager.sources.fhlb.import_silver

## Canonical schemas

::: mortgage_data_manager.sources.fhlb.canonical

## Pipeline

::: mortgage_data_manager.sources.fhlb.pipeline

## Data Structure
### Raw Layer

**AMA:**
- **Location**: `{FHLB_RAW_AMA_DIR}/` (`{FHLB_RAW_DIR}/ama/`)
- **Format**: one CSV per acquisition year, under the names FHFA publishes
  (`ama_pudb_export_1231YY.csv` through 2014, `YYYY_pudb_export*.csv` 2015-2022,
  `YYYY-pudb.csv` from 2023)

**Members:**
- **Location**: `{FHLB_RAW_MEMBERS_DIR}/` (`{FHLB_RAW_DIR}/members/`)
- **Format**: one Excel workbook per quarter; the quarter is parsed from the filename,
  which FHFA has published in three patterns (`FHLB_Members_Q12020.xlsx`,
  `FHLB-Members-09302018.xlsx`, `FHLB_Members_033114.xls`)

**Dictionaries:**
- **Location**: `{FHLB_DATA_DIR}/dictionaries/`

### Bronze Layer
Both datasets are parsed through canonical schemas (`schemas/fhlb/canonical/`), which map
each vintage's headers to one typed layout and fail loudly on an unrecognized vintage.

**AMA:**
- **Format**: `fhlb_ama_{year}.parquet`, one per year
- **Location**: `{FHLB_BRONZE_DIR}/ama/`

**Members:**
- **Format**: `fhlb_members_{year}_q{quarter}.parquet`, one per quarter
- **Location**: `{FHLB_BRONZE_DIR}/members/`

### Silver Layer

**AMA:**
- **Format**: `fhlb_ama_{year}.parquet`, one per year
- **Location**: `{FHLB_SILVER_DIR}/ama/`
- **Transformations**: Percentage normalization, income unit fixes, indicator standardization, census tract cleaning, null handling

## Known Data Issues
- **`Borrower1CreditScoreValue` / `Borrower2CreditScoreValue` are banded codes, not
  credit scores.** Every value in every vintage (2009-2025) is a code from 1 to 9 — the
  FHLB PUDB publishes a banded credit indicator, not FICO. On `Borrower2`, code `9`
  carries 38-45% of rows in every year from 2014 on and almost certainly means "no
  co-borrower / not available". Do not treat these columns as scores; the band
  definitions are in each year's data-definitions document on FHFA's
  [Public Use Database page](https://www.fhfa.gov/data/pudb).
- **`NoteDate` and `LoanAcquisitionDate` carry bare 4-digit years**, not full dates,
  in every vintage — only the names suggest otherwise.
- **`LoanCharacteristicsID` restarts annually before 2015** (a per-year row sequence
  `1..N`); it is a global sequence from 2015 on. Any join or window on the ID for
  early years must include `data_year`.

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FHLB Data](https://www.fhlbanks.com/) - Federal Home Loan Banks
