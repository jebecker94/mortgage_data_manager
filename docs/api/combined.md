# Combined API Reference

API reference for the cross-source master datasets (`combined`) subpackage.

---

## Overview
`combined` assembles a **small, curated** set of cross-source master datasets from
the per-source silver layers. It is deliberately *not* an exhaustive vendor-style
aggregation catalog — security-level state-concentration summaries and the like are
out of scope.

The value it adds is harmonization: each agency discloses the same economic facts
under different column names, code spaces, scales, and sentinels. A builder maps
every source into one canonical schema so a single query spans Fannie, Freddie, and
Ginnie without per-agency branching.

| Dataset | Grain | Agencies | Rows | Cols | Partitions | Status |
|---|---|---|---:|---:|---|---|
| `loan_issuance` | loan | FNMA · FHLMC · GNMA | 158,104,240 | 153 | `agency` / `issuance_year` | built |
| `loan_performance` | loan × month | FNMA · FHLMC · GNMA | 8,143,744,319 | 41 | `agency` / `reporting_year` | built |
| `loan_terminations` | loan × event | FNMA · FHLMC | 79,177,009 | 30 | `agency` / `zero_balance_year` | built |
| `loan_outcomes` | loan | FNMA · FHLMC · GNMA | 145,092,064 | 35 | `agency` / `orig_year` | built |
| `mbs_issuance` | security | GNMA · UMBS | 715,762 | 42 | `agency` / `issue_year` | built |
| `mbs_performance` | security × month | GNMA · UMBS | 90,939,164 | 33 | `agency` / `as_of_year` | built |
| `issuer` | issuer | — | — | — | — | **scaffold** (raises `NotImplementedError`) |
| `msr_transfers` | transfer | GNMA · UMBS | — | — | — | **external** — built by [`analytics.transfers`](analytics.md) |

Rows and columns were measured 2026-10-21 on the current builds; the column counts leave
out the two partition keys. The GSE loan panels carry every reporting month from 2000
(FNMA) and 1999 (FHLMC). `samples/` beside the datasets holds small extracts for schema
inspection.

`mbs_performance` spans FNMA 2019-06→2026-06, FHLMC 2025-12→2026-06 and GNMA
2012-08→2026-05. `mbs_issuance` spans GNMA 2012-02→2026-05 and both GSEs
2019-06→2026-05; the **clean tri-agency window is 2020-01→**, with the GSEs alone
reaching back to 2019-06 and Ginnie alone to 2012-02. Every row carries
`_source_dataset`, so a homogeneous window is one filter away:

| `_source_dataset` | Agency | Window | Rows |
|---|---|---|---:|
| `IS` / `RIS` | FNMA · FHLMC | 2019-06 → 2026-05 | 396,673 / 9,005 |
| `nimonSFPS_PS` | GNMA | 2020-01 → 2026-04 | 214,363 |
| `nissues_D` | GNMA | 2012-02 → 2019-12 | 88,938 |
| `factorAplat` | GNMA | 2019-06 → 2026-05 | 6,783 |

`RIS` is the GSE reissue/republication file, folded into the same silver as `IS`; it
is exactly the `record_source = 'correction'` population. `nissues_D` is the legacy
Ginnie pool-at-issuance header, mapped to the same grain and scoped to single-family
plus the pre-2017-08 Platinum pool-of-pools rows (multifamily project and
construction pools are dropped, matching the modern feed's scope). `factorAplat`
rows are Platinum **first observations** — Ginnie publishes no Platinum new-issuance
file, but a Platinum's first appearance in the factor panel is its issue month for
100.0% of in-window issues, and the static fields do not move month to month.
Securities that were already outstanding when that panel starts (2019-06) keep their
exact static fields but carry a **null** `current_face`, `security_factor` and WA
block rather than a mid-life snapshot.

All three loan-level datasets include the FNMA **HARP** panel (1,035,452 underwater-refi
loans, verified id-disjoint from the Primary SF dataset) as rows under `agency=FNMA`,
tagged `source_universe` (performance) / `source_dataset` (issuance, terminations)
`= 'gse_harp_disclosure'`. HARP is a program population (every loan
is a >80-LTV refi), not a sample — filter on the tag when the standard-acquisition
universe is required. The silver `loan_mapping` bridge ties each refinance to its
pre-HARP original loan, and both ids key the performance and termination panels.
In `loan_issuance` a HARP loan also carries its UMBS disclosure where the `mbs_umbs`
crosswalk reaches it: 236,365 of them do, concentrated in first-payment 2013–2016
(57% of the 2013 cohort rising to 82% of 2016), because Fannie's loan-level disclosure
begins with securities issued 2013-01 and earlier HARP vintages sit in pools that
predate it. No crosswalk reaches HMDA from a HARP loan — every HARP first payment falls
in 2009–2016, and the loan-level HMDA↔GSE linkage is post-2018 only.

### Output layout

```
data/combined/
├── loan_issuance/agency=<FNMA|FHLMC|GNMA>/issuance_year=<YYYY>/*.parquet
├── loan_performance/agency=<...>/reporting_year=<YYYY>/*.parquet
├── loan_terminations/agency=<FNMA|FHLMC>/zero_balance_year=<YYYY>/*.parquet
├── loan_outcomes/agency=<...>/orig_year=<YYYY>/*.parquet
├── mbs_issuance/agency=<...>/issue_year=<YYYY>/*.parquet
├── mbs_performance/agency=<...>/as_of_year=<YYYY>/*.parquet
└── samples/
```

`combined` sits *above* the medallion layers — it reads per-source silver (and, where
the grain only exists there, bronze) and writes one hive-partitioned tree per master.
It is not itself a raw→bronze→silver pipeline, so it has no `download`/`bronze`/`silver`
verbs.

---

## Configuration

::: mortgage_data_manager.combined.config

---

## Builders

::: mortgage_data_manager.combined.builders

---

## Schema modules

::: mortgage_data_manager.combined.loan_issuance_schema

::: mortgage_data_manager.combined.loan_performance_schema

::: mortgage_data_manager.combined.mbs_performance_schema

---

## CLI
```bash
mortgage-data combined info                          # registry + output path
mortgage-data combined build                          # all buildable masters
mortgage-data combined build -t mbs_issuance --overwrite
```

`build` is the only build verb — the canonical `download`/`bronze`/`silver` verbs do
not apply here (see *Output layout*).

---

## Common Pitfalls
- **Sentinels are scrubbed, and the bounds are empirical.** Credit scores outside
  300–850 (9999/7777/0/blank), underwriting ratios of 999 or −99999, ELTV ≥ 900
  ("not available" — ~42M FHLMC rows), and the 99.999 frontier-month rate placeholder
  are all nulled at build. Anything joining *raw* silver against these outputs must
  apply the same scrub or the distributions will not line up.
- **Negative loan age is not uniformly junk.** −1 to −3 is the benign GSE
  pre-first-payment convention; a genuine FHLMC junk cluster sits at −859 to −965.
  Don't filter on `loan_age < 0` wholesale.
- **`event_class`, not `event_type`, for cross-agency work.** The detailed
  vocabularies are disjoint by construction, so pooling on `event_type` silently
  splits economically identical events across agencies.
- **Both `mbs_*` masters double count until you filter `is_resecuritization`.** The
  GSE factor feeds carry Giants/Megas/Supers/Mirrors beside the base pools they wrap;
  Ginnie adds Platinum, which also nests inside itself.
- **`mbs_performance` speeds are only as good as `amort_basis`.** Half the Ginnie
  panel amortizes off proxies because the WA block does not exist before 2020-05.
- **The FHLMC security count steps up at 2026-04.** Freddie folded its ARM universe
  into the `FD` factor feed that month — 27,077 securities appear, 23,641 of them ARM
  (net +26,111 after retirements). It is a coverage change, not a data error, so
  don't read it as a surge in issuance.
- **Ginnie's `social_indicator` is `Y` on 100% of rows in every month it exists.**
  Both the `nimonSFPS` and `platmonPPS` PS records put a literal `Y` in the trailing
  field on every row from 2024-09, so the Boolean is all-`True` on the Ginnie side and
  carries no cross-sectional information. Treat a null there as "not published", not
  as "not social", and do not compare Ginnie's rate against the GSEs'.
- **Ginnie pool numbers starting `78` are Platinum**, in a pool-type code space
  (`SP`, `JP`, `AP`, `BP`, `EP`, `WA`–`WN`, and `PA`–`PF` for HMBS Platinum) disjoint
  from the base forward-MBS codes. `core.reference.gnma_pool_types` resolves them with
  `family = "platinum"`.
- **53 columns were pruned as always-null at source** (`PRUNED_ALWAYS_NULL_FIELDS`).
  `tests/test_combined/test_pruned_columns_guard.py` re-verifies that against current
  silver on every run: if a disclosure vintage starts populating one, the test fails
  — that's the signal to un-prune the field and rebuild, not to silence the test.
- **`overwrite` replaces the whole dataset tree**, not selected partitions. These are
  full rebuilds from silver, unlike the partition-scoped `overwrite` in the per-source
  silver builders.
- **`build_issuer` is a scaffold** and raises `NotImplementedError`. It is last in the
  dispatch order, so `build_datasets()` with no arguments builds the six real masters
  and *then* raises — the work is not lost, but the call does not return normally. Pass
  `-t/--datasets` explicitly to avoid it.
- **`msr_transfers` is registered but not built here.** Detection lives in
  `analytics.transfers.detect_servicer_transfers`; the registry entry exists so the
  dataset is discoverable from `combined info`.
