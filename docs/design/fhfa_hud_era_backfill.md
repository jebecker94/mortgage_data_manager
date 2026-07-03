# FHFA HUD-era Multifamily Backfill (1993–2007)

## Overview

Before the Housing and Economic Recovery Act (HERA) moved the GSE Public Use
Database from HUD to FHFA in July 2008, **HUD** published the same three
multifamily files this package already ingests for 2008+:

| HUD-era file | FHFA dataset | Description |
|---|---|---|
| `MF{year}C.LOA` | `mf_c` | Multifamily **census-tract** file (property-level, geography) |
| `MF{year}B.LOA` | `mf_property_b` | Multifamily **national / property-level** file |
| `MF{year}B.UNI` | `mf_unit_b` | Multifamily **national / unit-class** file |

This backfill extends those three series back to **1993**, so the FHFA pipeline
now covers multifamily GSE acquisitions continuously from 1993 to the present.
It is **multifamily only** — the HUD archive's single-family loan-level files are
not part of this work.

- **Source:** HUD USER archive — <https://www.huduser.gov/archives/portal/datasets/gse.html>
  (files under `https://www.huduser.gov/archives/portal/datasets/gse/`).
- **Coverage:** 1993–2007, contiguous (the 1998–2002 zips were re-downloaded to
  fill a gap in the originally-saved set). 138,924 property records and
  1,182,997 unit-class records total.
- **Raw layout:** archive zips are staged under `data/fhfa/raw/hud_era/`.
  Bronze and silver land in the **same** `bronze/{dataset}/` and
  `silver/{dataset}/` namespaces as the modern data (years never overlap), so
  the backfill is a transparent extension of the existing series.

## Format

The HUD-era files are **whitespace-delimited** (one blank space between fields),
not fixed-width like the modern FHFA PUDB, and carry **CRLF** line endings (the
1996–98 era additionally has trailing spaces before `\r\n`). The bronze builder
strips `\r`, splits on whitespace, drops rows whose token count does not match
the expected field count, and types each column by inspection (Float64 if any
value contains a decimal point, else Int64 — matching the integer storage of
FIPS/code fields in the modern bronze).

## Schema eras

Field counts were verified empirically against the raw files and against the
PDF data dictionaries bundled in the zips (present for 1996–2007). Era
boundaries differ per dataset:

| Dataset | 1993–1995 | 1996–2003 | 2004–2007 |
|---|---|---|---|
| `mf_c` (census tract) | 16 fields | 14 fields | 14 fields |
| `mf_property_b` (property) | 10 fields | 9 fields | 11 fields |
| `mf_unit_b` (unit class) | 6 fields | 6 fields | 6 fields |

Census geography vintage embedded in the field names is **1990** for 1993–1998
and **2000** for 1999–2007 (derived into a `Census Year` silver column).

Exact ordered layouts live in `fhfa/import_bronze.py::hud_era_layout()`.

## Harmonization to silver

HUD-era bronze columns are named to match the modern FHFA bronze names wherever
the concept is identical, so the **existing** `save_to_silver` transform
(rename dictionary `schemas/fhfa/unique_column_names_dict.csv`, `Census Year`
derivation, and `SENTINELS_TO_NULL` masking) processes HUD-era and modern years
through the **same code path**. HUD-era 2007 and modern 2008 produce an
identical 15-column core; the only differences are the documented ones below.

Rename-dictionary additions: 1990-vintage county/tract/income names, the
`Area Median Family Income (1993..2007)` year variants, `Government Insurance`,
`Geographically Targeted Indicator` (→ `underserved_areas_indicator`), and
`Acquisition UPB Range` (→ `upb_acquisition_range`). MF census "not available"
sentinels (`999999`, `9999.0`) were added to `SENTINELS_TO_NULL`.

## Known caveats

- **UPB is bucketed, not dollars.** In the HUD-era census file the acquisition
  UPB is a 1–5 range code (1=≤\$500k … 5=>\$4m, 9=missing), kept in
  `upb_acquisition_range` — deliberately **distinct** from the modern
  actual-dollar `upb_acquisition` so the two are never conflated.
- **FHFA-era-only fields are absent** from the HUD era: `loan_purpose`,
  `federal_guarantee`, and `lien_status` (census file) do not exist before 2008.
- **The targeting flag is absent in 1993–1995** (`underserved_areas_indicator`):
  the Geographically Targeted goal did not yet exist. A naive multi-file
  `scan_parquet` across the full HUD era therefore needs
  `extra_columns='ignore'` (or an explicit column selection), exactly as the
  modern data's year-varying schemas already require.
- **1993–1995 is undocumented** (no PDF dictionary shipped). The recoverable
  fields (geography, income measures, UPB range, seller, the property buckets)
  are mapped by position; the three unmapped middle census fields and the two
  trailing property dollar fields are carried under explicit `HUD Provisional …`
  names and excluded from the harmonized columns.
- **Census-tract vintage break.** 1993–1998 use 1990-Census geography and
  1999–2007 use 2000-Census geography (see `Census Year`); cross-vintage tract
  joins lose overlap, consistent with the project's other census-vintage notes.

## CLI usage

```bash
# Full backfill: download archive → bronze → silver for 1993–2007
mortgage-data fhfa pipeline hud-era

# Download the archive zips only
mortgage-data fhfa download hud-era

# Rebuild one era from already-staged raw zips
mortgage-data fhfa pipeline hud-era --skip-download --skip-bronze \
    --min-year 1996 --max-year 1998 --overwrite
```

## Python API

```python
from mortgage_data_manager.fhfa.pipeline import run_hud_era_backfill

# All three datasets, full span
run_hud_era_backfill(overwrite=True)

# Lower-level entry points
from mortgage_data_manager.fhfa.download import download_hud_gse_archive
from mortgage_data_manager.fhfa.import_bronze import build_bronze_mf_hud_era
```
