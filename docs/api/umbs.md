# UMBS API Reference

## Configuration

::: mortgage_data_manager.sources.umbs.config

## Schemas

::: mortgage_data_manager.sources.umbs.schemas

## Download

::: mortgage_data_manager.sources.umbs.download

## Bronze layer

::: mortgage_data_manager.sources.umbs.import_bronze

## Silver layer

::: mortgage_data_manager.sources.umbs.import_silver

## Freddie pool acquisition type

::: mortgage_data_manager.sources.umbs.prefix_library

## Pipeline

::: mortgage_data_manager.sources.umbs.pipeline

## Complete Example
```python
from mortgage_data_manager.sources.umbs.pipeline import run_pipeline

counts = run_pipeline(overwrite=False)
```

```bash
mortgage-data umbs pipeline
```

Raw files are downloaded by hand from Fannie Mae's and Freddie Mac's disclosure portals.
`mortgage-data umbs download` lists the feeds A reads, their raw folders, file names and
first months, and the look-alike files to avoid. `mortgage-data umbs info` adds a coverage
table per feed: first and last month on disk, missing months, and months holding more
than one file (an original beside a `_REV`, `_2` or second `fd` republication, which must
be kept). Bronze is scoped by GSE and raw folder (`--gse`, `--folder`), silver by GSE and
kind (`--gse`, `--kind`).

## Data Structure
### Directory Layout
Raw files sit in one folder per GSE and disclosure file type, one zip per publication.
Bronze mirrors that layout with one parquet per zip; the headerless multi-record-type
files are split by record type instead.

```
{UMBS_DATA_DIR}/
├── raw/
│   ├── FNMA/
│   │   ├── FNM_IS/FNM_IS_YYYYMM.zip
│   │   ├── FNM_ILLD/
│   │   └── ...
│   └── FHLMC/
│       ├── FRE_IS/
│       ├── FU/fuYYMMDD.zip
│       └── ...
├── bronze/
│   ├── FNMA/
│   │   ├── FNM_IS/FNM_IS_YYYYMM.parquet
│   │   ├── FNM_ISS/record_type={rt}/FNM_ISS_YYYYMM.parquet
│   │   └── ...
│   └── FHLMC/
│       └── ...
└── silver/
    ├── FNMA/{ILLD,IS,MLLD,MF,GN_MEGA}/{kind}_YYYYMM.parquet
    └── FHLMC/{ILLD,IS,FU}/{kind}_YYYYMM.parquet
```

### Bronze import paths
Fannie Mae and Freddie Mac publish the UMBS disclosures in harmonized formats (the Single
Security Initiative, June 2019), so one set of importers serves both GSEs. Bronze dtypes are
pinned per (GSE, folder, column) in `umbs/schemas.yaml`, generated from Fannie Mae's UMBS
disclosure glossary (`schemas/umbs/mbsglossary.xlsx`), so a column's type cannot drift
between months. Parquet is written with zstd compression.

| Path | Format | Folders |
|------|--------|---------|
| Headered (`run_bronze_import`) | Pipe-delimited with a header row (`FRE_Multilender` is comma-delimited) | FNMA: `FNM_ILLD`, `FNM_RILLD`, `FNM_MLLD`, `FNM_IS`, `FNM_RIS`, `FNM_MF`, `FNM_GN_MEGA`, `FNM_DPR_FCTR`, `FNM_ESF_MS`, `FNM_REMIC`, `FNM_REMIC_COMPONENT`, `FNM_REMIC_SHORT`, `FNM_SMBS`, `FNM_RESET_D`, `FNM_RESET_D_RATES`, `FNM_RESET_ND`, `FNM_RESET_ND_RATES`. FHLMC: `FRE_ILLD`, `FRE_RILLD`, `FRE_IS`, `FRE_RIS`, `FRE_DPR_Fctr`, `FRE_Multilender` and the two-letter folders listed below |
| Multi-record-type (`run_multi_rt_bronze_import`) | Pipe-delimited, no header; field 0 is a record-type code and the field count depends on it. Written per record type under `record_type={rt}/` | FNMA: `FNM_ISS`, `FNM_RISS`, `FNM_MS`, `FNM_ESF_MSS`. FHLMC: `FRE_ISS`, `FRE_RISS`, `FQ`, `GE`, `MJ`, `XS`, `MZ` |
| Not imported | Fixed-width or formatted reports, Excel, PDF, or multi-CSV reference zips | FNMA: `SIFMA`, `REMICSUP`, `RC`, `NODELAY`, `WLPSEUDO`, `FNM_DEALERREPORT`, `FNM_MICA`. FHLMC: `WR`, `CFILE`, `FRE_MICA` |

Freddie Mac's two-letter folders map to the glossary's file types as follows (from
`SHEET_TO_FOLDERS` in `umbs/schemas.py`). `ML`, `PF` and `FRE_Multilender` have no pinned
schema; bronze falls back to polars dtype inference for them and logs a warning.

| File type (glossary sheet) | FNMA folder | FHLMC folders |
|----------------------------|-------------|---------------|
| Loan level | `FNM_ILLD`, `FNM_RILLD`, `FNM_MLLD` | `FRE_ILLD`, `FRE_RILLD`, `FU`, `AC`, `AU` |
| Security level | `FNM_IS`, `FNM_RIS`, `FNM_MF` | `FRE_IS`, `FRE_RIS`, `FD`, `AR`, `XF`, `MI`, `MW` |
| Daily prepayment report | `FNM_DPR_FCTR` | `FRE_DPR_Fctr` |
| Ginnie Mae-backed Megas | `FNM_GN_MEGA` | |
| REMIC factor | `FNM_REMIC` | `MT`, `GT`, `MD`, `TB`, `TQ`, `TV`, `FT`, `WJ` |
| SMBS factor | `FNM_SMBS` | `SF`, `SX` |
| Component factor | `FNM_REMIC_COMPONENT` | `WL`, `CG` |
| REMIC shortfall | `FNM_REMIC_SHORT` | |
| ESF security | `FNM_ESF_MS` | |
| Floater reset indices | `FNM_RESET_D`, `FNM_RESET_ND` | `ID`, `IR`, `IS`, `DR`, `MR`, `OP` |
| Floater reset rates | `FNM_RESET_D_RATES`, `FNM_RESET_ND_RATES` | `RD`, `DC` |

### Silver
Silver is built for the loan- and security-level kinds, one file per (GSE, kind, month),
with the kind's `SilverSpec` dtypes applied (dates parsed, closed code sets as enums,
credit-score and LTV/DTI sentinels null-mapped, and code `9` = Not Available null-mapped
in First Time Home Buyer Indicator, Loan Purpose, Occupancy Status, Channel and Pricing
Indicator). A month whose build fails is counted as `failed`, nothing is published under
its name, and `umbs silver` exits 1.

- `ILLD` and `IS` (both GSEs) merge each original with its reissue file; see
  [Reissue (R-) files](#reissue-files) below.
- `MLLD` (FNMA) and `FU` (FHLMC), the monthly loan-level performance feeds, and FNMA `MF`
  and `GN_MEGA` have no reissue twin and pass straight through, with `_record_source`
  set to `'original'`.

From the 2026-10 files, ILLD/RILLD add `Pricing Indicator` (C = Classic FICO, V = VS4),
`Fannie Score` and `Freddie Score`, and IS/RIS add `WA Fannie Score`, `WA Freddie Score`,
`WA Classic FICO Used In Pricing` and `WA VS4 Used In Pricing`, for both GSEs. Earlier
months carry them as nulls. MLLD, FU, MF and FD do not carry them (as of 2026-10).

**`acquisition_type` (Freddie ILLD, IS and FU).** The pool's class in Freddie's Prefix
Library: `Cash`, `MultiLender` or `Guar` (single-seller swap), looked up from the pool
prefix and number in `schemas/umbs/fhlmc_prefix_library_ranges.csv`. It is null for
Fannie (no published equivalent) and for pools no range covers (Supers, exchanged mirror
pools, prefixes other than CL/CT/CI/CN). On the Freddie IS files from 2019-06, all 170,288
CL pools in the Q, R and T series fall in exactly one range. The column names the pool's
class, not the loan's channel, and the two agree only for 30- and 15-year pools through
April 2025:

- 20- and 10-year MultiLender pools have always held Freddie cash-window loans as well as
  swapped loans.
- From May 2025, 30- and 15-year MultiLender UMBS (`RQ`, `RR`) take in the generic cash
  pool that was previously paired with them inside a MultiLender Super, so they mix both.

## Reissue (R-) files and the silver layer {#reissue-files}
GSE disclosure folders come in matched original/reissue pairs:

| Kind | FNMA original / reissue | FHLMC original / reissue |
|------|------------------------|-------------------------|
| Loan-level issuance | `FNM_ILLD` / `FNM_RILLD` | `FRE_ILLD` / `FRE_RILLD` |
| Security-level issuance | `FNM_IS` / `FNM_RIS` | `FRE_IS` / `FRE_RIS` |
| Issuance stratified | `FNM_ISS` / `FNM_RISS` | `FRE_ISS` / `FRE_RISS` |

The `R-` file is a **same-month republication of every pool/security in which any field was corrected**, not a delta. It republishes *all* loans in a touched pool — the `Loan/Security Correction Indicator` column is `Y` only for the loans whose fields actually changed, and `N` for the unchanged-but-republished siblings. Empirically (verified across 70+ months of `FNM_RIS` and `FRE_RIS` and all available `RILLD` months) **every R-file row matches a same-month original** — no cross-month corrections.

**Silver consolidates the pair into one dataset per kind.** For each month, the silver importer (`umbs.import_silver`) takes the original for any key not present in that month's R-file and the R-file's row otherwise, then adds a `_record_source` column (`'original'` or `'correction'`). The pairings live in `CORRECTION_PAIRS` in `umbs/config.py` — register new corrected files there, not in the importer.

RIS and RISS run from 2019-06. RILLD is a newer file: the GSEs announced it in March 2026,
and the first file is issuance month 2026-03 for both, so ILLD silver before 2026-03 holds
original rows only. A reissue file is posted only for months that had corrections.

**ISS/RISS have no silver yet.** The stratified issuance files are headerless
multi-record-type files, which bronze splits by record type. The silver merge reads one
parquet per month, so it does not consume that layout; the ISS pairs stay registered in
`CORRECTION_PAIRS` but are skipped.

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FNMA API](fnma.md) - Fannie Mae specific processing
- [FHLMC API](fhlmc.md) - Freddie Mac specific processing
