# Analytics API Reference

## Overview
`analytics` computes derived metrics on top of the silver and combined layers. It is
a **library-only** subpackage — there is no `mortgage-data analytics` CLI; you import
and call it.

The organizing principle is epistemic: modules are split by whether a result is exact
given its inputs or requires a model.

| Module | Kind | What it does | Maturity |
|---|---|---|---|
| `calculated` | deterministic | SMM/CPR/CDR conversions, balance-weighted rollups, delinquency roll rates, security-level net speeds from month-over-month UPB | stable |
| `servicing` | deterministic | Expected vs. realized servicing-fee PV at origination | stable |
| `transfers` | deterministic | MSR servicer/issuer transfer detection → seller→buyer event tables | stable |
| `servicer_names` | deterministic | Name normalization + parent-entity consolidation | stable |
| `schema` | contract | The source-neutral loan-month schema shared by the panel/feature/model chain | stable |
| `panel` | pipeline | GSE loan-month panel projected from the combined masters + outcome construction | **preliminary** |
| `features` | pipeline | Feature engineering for the hazard models | **preliminary** |
| `estimated` | model-based | Prepayment/default hazards, speed projection, lifetime loss | **preliminary** |

"Deterministic" is a real claim, not a label: given the inputs, those outputs are
exact and reproducible. `servicing` lives on that side even though it involves a
prepayment assumption, because the PSA speed is *fixed* rather than estimated.

The preliminary modules work, but their interfaces are still moving and the panel
defaults to a 10% loan sample (`ANALYTICS_PANEL_SAMPLE_FRAC`). Treat the reference for
those three as orientation rather than a stable contract.

## Configuration

::: mortgage_data_manager.analytics.config

## Schema

::: mortgage_data_manager.analytics.schema

## calculated — closed-form metrics

::: mortgage_data_manager.analytics.calculated

## servicing — MSR revenue PV

::: mortgage_data_manager.analytics.servicing

## transfers — MSR transfer detection

::: mortgage_data_manager.analytics.transfers

## servicer_names — entity normalization

::: mortgage_data_manager.analytics.servicer_names

## panel, features, estimated

::: mortgage_data_manager.analytics.panel

::: mortgage_data_manager.analytics.features

::: mortgage_data_manager.analytics.estimated

## Common Pitfalls
- **No CLI.** `analytics` is import-only. `mortgage-data analytics ...` does not exist,
  by design — these are library calls with parameter choices, not pipeline stages.
- **Check which side of the split you are on.** `calculated` results are exact given
  inputs; `estimated` results carry a model specification and uncertainty and should
  never be reported without it.
- **Servicer parents are curated, not exhaustive.** `PARENT_MAP` is a hand-maintained
  map of 128 names. Work that depends on complete entity resolution in the tail needs to
  extend it, and the bank/nonbank fallback is a keyword guess.
- **`realized_servicing_pv` is right-censored.** A loan still active at the end of the
  panel carries only the servicing earned to date; filter on `is_terminated` for
  completed histories.
