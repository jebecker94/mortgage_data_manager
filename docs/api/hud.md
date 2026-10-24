# HUD Multifamily API Reference

## Overview
HUD publishes a family of FHA multifamily and assisted-housing extracts at
<https://www.hud.gov/hud-partners/multifamily-data> as **monthly point-in-time
Excel/Access snapshots**. This subpackage ingests the tabular extracts into the
medallion layout. Eleven logical tables in two families, bridged by one key.

Distinct from the `usps_crosswalk` subpackage (HUD's USPS ZIP↔Census crosswalks) and the `fha`
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

`property_id` is the universal spine; `fha_number` links the insured subset (in the
2026-06 snapshot, 21% of Section 8 properties and 47% of the active portfolio carry an FHA
number). The
property↔FHA bridge lives in `insured_active_addresses` and
`portfolio_property_fha`. **Active and terminated insured mortgages are disjoint
on FHA number**; Section 202 uses a disjoint alphanumeric project-number
namespace.

### Medallion layout

```
{HUD_DATA_DIR}/
├── raw/
│   ├── active_insured_mortgages.xlsx        (10 source workbooks, friendly names)
│   ├── ...
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

::: mortgage_data_manager.sources.hud.config

## Download

::: mortgage_data_manager.sources.hud.download

## Bronze layer

::: mortgage_data_manager.sources.hud.import_bronze

## Silver layer

::: mortgage_data_manager.sources.hud.import_silver

## Pipeline

::: mortgage_data_manager.sources.hud.pipeline

## Vintages & time-series value
Every file is a monthly **point-in-time** snapshot and HUD republishes a single
current file each month with **no historical archive** — so for any table whose
value is mutating current-state, history can't be backfilled later: each month
not captured is lost permanently. Bronze and silver overwrite `{table}.parquet` each
month, so a panel requires archiving the raw workbooks per vintage. How much each table
gains from that, assessed on the 2026-06 snapshot:

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
mortgage-data hud info                       # paths + logical-table registry + snapshots
mortgage-data hud download                    # all workbooks + DED PDFs
mortgage-data hud download -t sec8_contracts --overwrite
mortgage-data hud bronze
mortgage-data hud silver --overwrite
mortgage-data hud pipeline                    # download → bronze → silver
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
  vintage column. To build a panel, archive `raw/` per vintage (the ledger's
  `Last-Modified` and bronze `snapshot_date` anchor each). Event-style fields for longitudinal work: endorsement/maturity dates
  and termination type/date (insured), TRACS effective/expiration and renewal
  dates (assisted).
- **Pervasive trailing whitespace.** HUD's fixed-width exports pad every string
  field; bronze strips it, but anything joined against the *raw* workbooks must
  strip first or keys silently miss.
- `contracts_rent_utility` ships as **two near-disjoint sheets** (52,675 +
  27,665 rows, overlap = 1); they are vertically unioned into one long table
  (one row per bedroom size per contract).
