# HUD Multifamily API Reference

API reference for the HUD multifamily housing (`hud_mf`) subpackage.

## Overview

HUD publishes a family of FHA multifamily and assisted-housing extracts at
<https://www.hud.gov/hud-partners/multifamily-data> as **monthly point-in-time
Excel/Access snapshots**. This subpackage ingests the tabular extracts into the
medallion layout. Eleven logical tables in two families, bridged by one key.

Distinct from the `hud` subpackage (USPS ZIP↔Census crosswalk) and the `fha`
subpackage (single-family + HECM loan snapshots).

### Insured family — FHA-number spine

| Table | Source file | Rows | Grain / key |
|---|---|---|---|
| `insured_mortgages_active` | active_insured_mortgages.xlsx | 15,562 | `hud_project_number` (1/FHA#) |
| `insured_mortgages_terminated` | terminated_insured_mortgages.xlsx | 58,616 | `hud_project_number` (disjoint from active) |
| `section202_direct_loans` | 202_direct_loans.xlsx | 608 | `project_fha_number` (+suffix); **alphanumeric** FHA# |
| `insured_active_addresses` | insured_active_addresses.xlsx | 18,787 | `property_id` + `fha_number` (bridge) |

### Assisted family — `property_id` (REMS-id) spine

| Table | Source file | Rows | Grain / key |
|---|---|---|---|
| `sec8_contracts` | mf_assistance_sec8_contracts.xlsx | 24,309 | `contract_number` |
| `sec8_properties` | mf_properties_assistance_sec8.xlsx | 23,610 | `property_id` |
| `contract_rents_utility` | contracts_rent_utility.xlsx | 80,340 | (`property_id`,`contract_number`,bedroom); **two sheets unioned** |
| `contract_renewals` | contract_renewal_all.xls | 24,309 | `contract_number` |
| `portfolio_property` | active_portfolio_property.xlsx · sheet 1 | 36,873 | `property_id` (full active portfolio, superset) |
| `portfolio_property_fha` | active_portfolio_property.xlsx · sheet 2 | 17,508 | `property_id` + `fha_number` (bridge) |

### Reference

| Table | Source file | Rows | Notes |
|---|---|---|---|
| `soa_codes` | soa_list.xlsx | 93 | Section-of-the-Act code → title lookup |

`property_id` is the universal spine; `fha_number` links the insured subset
(≈21% of assisted properties also carry an FHA-insured mortgage). The
property↔FHA bridge lives in `insured_active_addresses` and
`portfolio_property_fha`. **Active and terminated insured mortgages are disjoint
on FHA number**; Section 202 uses a disjoint alphanumeric project-number
namespace.

### Medallion layout

```
data/hud_mf/
├── raw/
│   ├── active_insured_mortgages.xlsx        (10 source workbooks, friendly names)
│   ├── ...
│   ├── download_manifest.json               (per-file URL / Last-Modified / bytes)
│   └── dictionaries/
│       └── ded_*.pdf                         (3 Data Element Dictionaries)
├── bronze/
│   └── <table>.parquet                       (snake_case, whitespace-stripped, snapshot-stamped)
└── silver/
    └── <table>.parquet                       (cleaned + normalized join keys)
```

The Access `.zip` HUD also offers is **intentionally not downloaded**: it
bundles the same two Section 8 Excel extracts we already fetch plus a 65 MB
`.accdb` — pure redundancy.

## Configuration

```python
from mortgage_data_manager.hud_mf import HudMfConfig, DATASET_MAP, VALID_DATASETS

HudMfConfig.HUD_MF_RAW_DIR        # data/hud_mf/raw
HudMfConfig.HUD_MF_BRONZE_DIR     # data/hud_mf/bronze
HudMfConfig.HUD_MF_SILVER_DIR     # data/hud_mf/silver
HudMfConfig.raw_path("soa_list.xlsx")
HudMfConfig.bronze_path("sec8_contracts")
HudMfConfig.silver_path("portfolio_property")
```

Environment overrides: `HUD_MF_DATA_DIR`, `HUD_MF_RAW_DIR`, `HUD_MF_BRONZE_DIR`,
`HUD_MF_SILVER_DIR`. Optional dependency: `pip install mortgage-data-manager[hud_mf]`
(adds `fastexcel`; the calamine engine reads both `.xlsx` and the one `.xls`).

## Download

```python
from mortgage_data_manager.hud_mf.download import (
    download_files,         # source workbooks → raw/ (+ download_manifest.json)
    download_dictionaries,  # the 3 DED PDFs → raw/dictionaries/
    resolve_section202_url, # HEAD-check pinned 202 URL; scrape index on 404
)

download_files()                          # all workbooks
download_files(["sec8_contracts"], overwrite=True)
download_dictionaries()
```

Each file is a monthly snapshot with no in-band vintage, so the download layer
records each source's `Last-Modified` into `raw/download_manifest.json`; bronze
stamps every row with that `snapshot_date`. The **Section 202** URL is
date-stamped in its filename and HUD rolls it forward monthly —
`resolve_section202_url` HEAD-checks the pinned URL and re-scrapes the index
page for the current `202directloans*.xlsx` link when it 404s.

## Bronze / Silver

```python
from mortgage_data_manager.hud_mf.import_bronze import build_bronze
from mortgage_data_manager.hud_mf.import_silver import build_silver, scan_silver

build_bronze()                 # one typed parquet per logical table
build_silver(overwrite=True)   # cleaned + key-normalized

lf = scan_silver("insured_mortgages_active")
```

Bronze reads each workbook at the registry's `header_row` (several HUD files
carry a title/count or instruction block above the real header), unions split
sheets (rent/utility), snake_cases columns, strips the pervasive fixed-width
trailing whitespace (empty → null), and keeps identifier columns (FHA numbers,
property ids, zips, SoA codes) as strings so leading zeros survive. The SoA
lookup sheet uses a **manual header promotion** (calamine's `header_row`
width-detection latches onto its sparse instruction rows).

Silver adds normalized join keys without dropping the raw columns —
`property_id_norm` and `fha_number_norm` (strip + uppercase + left-pad to 8) —
and filters `soa_codes` down to real codes (dropping category-banner rows). The
tables are small (≤60k rows) so silver is a flat parquet per table, not hive
partitioned; `overwrite` only replaces the single table file.

## Vintages & time-series value

Every file is a monthly **point-in-time** snapshot and HUD republishes a single
current file each month with **no historical archive** — so for any table whose
value is mutating current-state, history can't be backfilled later: each month
not captured is lost permanently. The decision is which tables to **start
archiving per-vintage** (e.g. switch those from overwriting `{table}.parquet` to
partitioning by `snapshot_date`). Assessment of the 2026-06 snapshot:

| Table | Panel value | Why |
|---|---|---|
| `contract_rents_utility` | ★★★ highest | No date column at all — pure current rent / FMR / utility by bedroom; one snapshot = one period of the rent series. |
| `sec8_contracts` | ★★★ high | Current rents, `rent_to_fmr_ratio`, status, current expiration mutate on renewal; contracts enter/exit. |
| `insured_mortgages_active` | ★★★ high | `amoritized_principal_balance`, rate, holder, servicer are current-state; loans leave when terminated. UPB paydown + servicer/holder transfers unrecoverable. |
| `sec8_properties` | ★★ mod-high | Only the *current* owner / mgmt agent is shown; prior owners lost without vintages. |
| `portfolio_property` | ★★ mod-high | Current active portfolio; `is_*` financing/subsidy flags flip and properties enter/exit. |
| `contract_renewals` | ★★ moderate | 1:1 with contracts → *current* renewal stage only, not a renewal log; vintages reconstruct the renewal-option sequence. |
| `section202_direct_loans` | ★ moderate | Shrinking runoff roster of a legacy program (no new originations; endorsements 1967–2011) with ticking-down UPB. **Not** a strict superset — the latest file *loses* matured loans — but small and origination is in-band. |
| `insured_mortgages_terminated` | ✗ low | Append-only/cumulative: `term_date` already spans 1939→2026 in one file, so the termination series is fully in-band. |
| `insured_active_addresses` | ✗ low | Static address lookup for the current active set; no mutable quantity. |
| `portfolio_property_fha` | ✗ low | Derivative property↔FHA join table. |
| `soa_codes` | ✗ none | Reference dimension; `superceded_parent_*` columns already encode code lineage. |

Single-snapshot **event-style analysis** is supported without vintages wherever
dates are in-band: endorsement / first-payment / maturity / termination dates
(insured) and TRACS effective / expiration / renewal dates (assisted).

## CLI

```bash
mortgage-data hud-mf info                       # paths + logical-table registry + snapshots
mortgage-data hud-mf download                    # all workbooks + DED PDFs
mortgage-data hud-mf download -t sec8_contracts --overwrite
mortgage-data hud-mf bronze
mortgage-data hud-mf silver --overwrite
mortgage-data hud-mf pipeline                    # download → bronze → silver
```

## Common Pitfalls

- **Two FHA-number namespaces.** Insured mortgages / addresses / portfolio use
  numeric zero-padded 8-char project numbers (`00011186`); Section 202 uses
  alphanumeric ones (`000EH104`). They are disjoint — never blanket-coerce 202
  numbers to integers. `fha_number_norm` zero-pads numerics and passes the
  alphanumerics through unchanged.
- **The SoA lookup is incomplete.** Codes appear in the data that are absent
  from `soa_codes` — 7 in active insured, **62 in terminated** (decades of
  retired program codes), 3 of the 202 codes. Treat the lookup as best-effort
  enrichment, not an exhaustive dimension.
- **Monthly snapshots, no history.** Every file is point-in-time with no
  vintage column. To build a panel, archive `raw/` per vintage (the
  `download_manifest.json` `Last-Modified` and bronze `snapshot_date` anchor
  each). Event-style fields for longitudinal work: endorsement/maturity dates
  and termination type/date (insured), TRACS effective/expiration and renewal
  dates (assisted).
- **Pervasive trailing whitespace.** HUD's fixed-width exports pad every string
  field; bronze strips it, but anything joined against the *raw* workbooks must
  strip first or keys silently miss.
- `contracts_rent_utility` ships as **two near-disjoint sheets** (52,675 +
  27,665 rows, overlap = 1); they are vertically unioned into one long table
  (one row per bedroom size per contract).
```
