# Combined API Reference

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
| `loan_issuance` | loan | FNMA · FHLMC · GNMA | 158,667,372 | 146 | `agency` / `issuance_year` | built |
| `loan_performance` | loan × month | FNMA · FHLMC · GNMA | 9,189,921,066 | 41 | `agency` / `reporting_year` | built |
| `loan_terminations` | loan × event | FNMA · FHLMC · GNMA | 115,838,237 | 35 | `agency` / `zero_balance_year` | built |
| `loan_outcomes` | loan | FNMA · FHLMC · GNMA | 161,210,942 | 37 | `agency` / `orig_year` | built |
| `mbs_issuance` | security | FNMA · FHLMC · GNMA | 739,147 | 42 | `agency` / `issue_year` | built |
| `mbs_performance` | security × month | FNMA · FHLMC · GNMA | 122,077,406 | 33 | `agency` / `as_of_year` | built |

Rows and columns were measured 2026-11-03 on the current builds; the column counts leave
out the two partition keys. The GSE loan panels carry every reporting month from 2000
(FNMA) and 1999 (FHLMC); Ginnie's start in 2013-09 (issuance) and 2013-10 (performance).
`combined build` runs the masters in registry order, so `loan_outcomes` follows the three
loan masters it reads, and warns when a master it skips is older than one of its inputs.
A build writes to a temporary tree and swaps it in only when complete, so an interrupted
build leaves the previous one in place.

### Provenance and keys

| Dataset | Key | Provenance column and values |
|---|---|---|
| `loan_issuance` | `agency`, `loan_key` | `source_dataset`: `fnma_sf_perf`, `gse_harp_disclosure`, `fnma_umbs_only`, `fhlmc_sf_orig`, `fhlmc_umbs_only`, `gnma_dailyllmni` |
| `loan_performance` | `agency`, `loan_key`, `reporting_period` | `source_universe`: `gse_disclosure`, `gse_harp_disclosure`, `fnma_exclusion`, `gnma_disclosure`; `month_source`: `both`, `sf_only`, `umbs_only` (null for GNMA) |
| `loan_terminations` | `agency`, `loan_key` (one terminal event per loan) | `source_dataset`: `gse_disclosure`, `gse_harp_disclosure`, `fnma_exclusion`, `gnma_disclosure` |
| `loan_outcomes` | `agency`, `loan_key` | `source_universe`, as in `loan_performance` |
| `mbs_issuance` | `cusip` | `source_dataset`: `IS`, `RIS`, `nimonSFPS_PS`, `nissues_D`, `factorAplat` |
| `mbs_performance` | `agency`, `pool_id`, `as_of_month` | `source_dataset`: `FNM_MF`, `FD`, `GN_MEGA`, `factorA1`, `factorA2`, `factorAplat` |

`loan_key_kind` names the namespace of `loan_key`: `gse_numeric` (Fannie SF), `sf_seq`
(Freddie SF), `umbs_seq` (a GSE loan known only from the UMBS disclosure) and `gnma_seq`
(Ginnie's disclosure sequence number). `fnma_exclusion` is Fannie's Exclusion dataset, the
ARM, Alt-A and other loans left out of its standard release; its panel ends in 2020-09.

The two MBS masters share column names for the same fields (`as_of_month`, `original_face`,
`current_face`, `security_factor`, `pass_through_rate`, `wa_loan_age`, `wa_rem_term`,
`wa_orig_loan_term`). Across every master, a month key (`as_of_month`, `reporting_period`)
is a `YYYYMM` integer and a calendar date is a `Date`.

`issuance_year` means a different date per source: the origination year for Fannie SF
rows, the first-payment year for Freddie SF and both `*_umbs_only` sources, and the
issuance month's year for Ginnie.

`mbs_performance` spans FNMA and FHLMC 2019-06→2026-10 and GNMA
2012-08→2026-08. `mbs_issuance` spans GNMA 2012-02→2026-08 and both GSEs
2019-06→2026-08; the **clean tri-agency window is 2020-01→**, with the GSEs alone
reaching back to 2019-06 and Ginnie alone to 2012-02. Every row carries
`source_dataset`, so a homogeneous window is one filter away:

| `source_dataset` | Agency | Window | Rows |
|---|---|---|---:|
| `IS` / `RIS` | FNMA · FHLMC | 2019-06 → 2026-08 | 408,079 / 9,086 |
| `nimonSFPS_PS` | GNMA | 2020-01 → 2026-07 | 226,100 |
| `nissues_D` | GNMA | 2012-02 → 2019-12 | 88,937 |
| `factorAplat` | GNMA | 2019-06 → 2026-08 | 6,945 |

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

All four loan-level datasets include the FNMA **HARP** panel (1,035,452 underwater-refi
loans, verified id-disjoint from the Primary SF dataset) as rows under `agency=FNMA`,
tagged `source_universe` (performance, outcomes) / `source_dataset` (issuance, terminations)
`= 'gse_harp_disclosure'`. HARP is a program population (every loan
is a >80-LTV refi), not a sample — filter on the tag when the standard-acquisition
universe is required. The silver `loan_mapping` bridge ties each refinance to its
pre-HARP original loan, and both ids key the performance and termination panels.
A HARP loan also carries its UMBS disclosure where the `mbs_umbs` crosswalk reaches it,
in issuance and in its monthly panel alike: 236,365 of them do, concentrated in
first-payment 2013–2016 (57% of the 2013 cohort rising to 82% of 2016), because Fannie's
loan-level disclosure begins with securities issued 2013-01 and earlier HARP vintages sit
in pools that predate it. No crosswalk reaches HMDA from a HARP loan — every HARP first payment falls
in 2009–2016, and the loan-level HMDA↔GSE linkage is post-2018 only.

### Output layout

```
data/combined/
├── loan_issuance/agency=<FNMA|FHLMC|GNMA>/issuance_year=<YYYY>/*.parquet
├── loan_performance/agency=<...>/reporting_year=<YYYY>/*.parquet
├── loan_terminations/agency=<...>/zero_balance_year=<YYYY>/*.parquet
├── loan_outcomes/agency=<...>/orig_year=<YYYY>/*.parquet
├── mbs_issuance/agency=<...>/issue_year=<YYYY>/*.parquet
└── mbs_performance/agency=<...>/as_of_year=<YYYY>/*.parquet
```

`combined` sits *above* the medallion layers — it reads per-source silver (and, where
the grain only exists there, bronze) and writes one hive-partitioned tree per master.
It is not itself a raw→bronze→silver pipeline, so it has no `download`/`bronze`/`silver`
verbs.

## Configuration

::: mortgage_data_manager.combined.config

## Builders

::: mortgage_data_manager.combined.builders

::: mortgage_data_manager.combined.loan_issuance

::: mortgage_data_manager.combined.loan_performance

::: mortgage_data_manager.combined.loan_outcomes

::: mortgage_data_manager.combined.mbs_issuance

::: mortgage_data_manager.combined.mbs_performance

## Schema modules

Each master's source mappings are tables: the GSE loan plans name the columns behind each
field on both spines, and every single-source leg (Ginnie's loan files, each MBS feed)
lists its output columns in order with the source columns and the reader that builds
each one.

::: mortgage_data_manager.combined.loan_issuance_schema

::: mortgage_data_manager.combined.loan_performance_schema

::: mortgage_data_manager.combined.mbs_issuance_schema

::: mortgage_data_manager.combined.mbs_performance_schema

## Common Pitfalls
- **Sentinels are scrubbed, and the bounds are empirical.** Credit scores outside
  300–850 (9999/7777/0/blank), underwriting ratios of 999 or −99999, ELTV ≥ 900
  ("not available" — ~42M FHLMC rows), and the 99.999 frontier-month rate placeholder
  are all nulled at build, and so is the UMBS not-available code `9` in purpose, channel
  and occupancy. Anything joining *raw* silver against these outputs must apply the same
  scrub or the distributions will not line up.
- **`*_raw` columns keep the published code.** `loan_purpose_raw` holds Freddie's `N`
  (no-cash-out refinance) where Fannie publishes `R`; both decode to `refi_rate_term` in
  `loan_purpose`. The decode tables are in `mortgage_data_manager.core.harmonized`.
- **Negative loan age is not uniformly junk.** −1 to −3 is the benign GSE
  pre-first-payment convention; a genuine FHLMC junk cluster sits at −859 to −965 and is
  nulled (anything below −24). Don't filter on `loan_age < 0` wholesale. For the same
  reason `months_to_termination` in `loan_outcomes` can be slightly negative.
- **A null `mi_percent` means different things by source.** Fannie SF leaves it null when
  a loan has no MI, while the ILLD disclosure writes an explicit 0; Ginnie loans carry
  government MIP in `gov_*_mip_rate` instead and are null here.
- **Terminal rows blank part of the loan's state.** Fannie's leave the loan age and current
  rate empty and report a zero current balance; Ginnie's leave the age empty on every
  removal and the balance on 1-5%; Freddie's carry all three. `loan_outcomes` therefore
  reads the latest disclosed value: `last_upb` is the last positive balance, with its month
  in `last_upb_period`, and `loan_age_at_termination` carries the last disclosed age forward
  to the event month. `loan_terminations` fills `loan_age_at_removal`,
  `interest_rate_at_removal` and `delinquency_months_at_removal` the same way from the
  previous month, so a Ginnie removal in a family's first file (2013-10) has no age.
- **Ginnie terminations carry no losses.** Ginnie publishes the removal reason and balance
  but no loss waterfall, so its `loan_terminations` rows leave every proceeds, cost and loss
  column null, and `zero_balance_code` too: `event_raw_code` holds the source code for all
  three agencies. The 37 loans Ginnie removes in two different months are left out, since
  which removal ended the loan is unknowable; they keep both events in `loan_performance`.
- **`terminated` covers disclosed events only.** A `umbs_seq` loan, known only from the
  UMBS disclosure, can leave the panel without a zero-balance row. `observed_at_frontier`
  is False for any loan whose last month falls before the last month of its own release,
  so `~terminated & ~observed_at_frontier` is a loan that left unseen. The delinquency
  flags (`ever_30` and on) are null for `umbs_seq` loans, whose panel carries days
  delinquent rather than the SF status.
- **`event_class`, not `event_type`, for cross-agency work.** The detailed
  vocabularies are disjoint by construction, so pooling on `event_type` silently
  splits economically identical events across agencies.
- **Both `mbs_*` masters double count until you filter `is_resecuritization`.** The
  GSE factor feeds carry Giants/Megas/Supers/Mirrors beside the base pools they wrap;
  Ginnie adds Platinum, which also nests inside itself.
- **`mbs_performance` speeds are only as good as `amort_basis`.** Half the Ginnie
  panel amortizes off proxies because the WA block does not exist before 2020-05.
- **Each `mbs_performance` feed has its own last month.** Ginnie's Platinum panel runs
  a month ahead of its base factor files and Fannie's Ginnie-backed Mega file a month
  behind its factor file. A pool counts as disappeared only against its own feed's last
  month, so compare agencies over months every feed covers.
- **Ginnie multifamily pools are out of scope.** Project and construction pools are
  dropped from both `mbs_*` masters, so the Ginnie rows are single-family plus Platinum.
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
