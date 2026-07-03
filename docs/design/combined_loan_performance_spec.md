# Combined `loan_performance` + `loan_terminations` spec (v2)

Status: implemented (`combined/builders.py`, `combined/loan_performance_schema.py`).
Derived from `investigations/reports/investigation_perf_field_volatility_2026-06-26.md`
(which columns are time-varying) and `investigation_sf_umbs_perf_alignment_2026-06-25.md`
(SF ⋈ MLLD/FU fuse on `reporting_period`).

## Design principles

1. **Only time-varying columns live in the long panel.** The field-volatility study
   showed ~10–15 of ~110 monthly columns ever change; the rest are origination-static
   (repeated ~42–45× per loan). Static covariates live once on `loan_issuance` and join
   by `loan_key`. Result: the panel is ~25 columns, not ~110.
2. **Full-outer month fusion, tagged.** Each GSE loan is the SF Loan-Performance panel
   (base) full-outer-joined to the UMBS MLLD/FU monthly factor (enrichment) on
   `reporting_period`, bridged by the 1:1 mbs_umbs crosswalk. This retains a matched
   loan's **SF-only early months** (UMBS discloses ~2–3 mo after issuance) and its
   **UMBS-only recent months** (SF release lags ~9 mo). `month_source ∈ {both, sf_only,
   umbs_only}` labels every row; double-counting is opt-out (`month_source != 'umbs_only'`
   or by `loan_key_kind`).
3. **Keep both UPB notions.** `current_upb` = SF full-loan actual balance;
   `current_investor_upb` = UMBS participation-weighted balance.
4. **Pool identity is per-month, not static.** FNMA re-pools ~0.23% of loans, so
   `cusip`/`security_identifier`/`prefix` ride on the monthly row (loan×pool bridge).
5. **Terminal/loss fields → a separate sparse events file**, not the dense panel (they
   populate ~1 month in ~42).
6. **Tri-agency.** GNMA (single disclosure spine, v1) + FNMA + FHLMC reindex to one
   canonical schema. GNMA event info stays inline (`event_type`); it has no loss waterfall.

## File 1 — `data/combined/loan_performance` (grain: loan × reporting-month)

Hive-partitioned `agency=/reporting_year=`. One row per `(loan_key, reporting_period)`.

### Keys & provenance
| column | type | source | notes |
|--------|------|--------|-------|
| `loan_key` | str | SF id (matched/sf_only) / UMBS id (umbs_only) | matches `loan_issuance.loan_key` |
| `loan_key_kind` | str | — | `gse_numeric` (FNMA) / `sf_seq` (FHLMC) / `umbs_seq` (umbs_only loan) |
| `loan_key_umbs` | str | UMBS `Loan Identifier` | pool cross-ref; null on SF-only months |
| `agency` | str | — | partition; `FNMA`/`FHLMC`/`GNMA` |
| `source_universe` | str | — | `gse_disclosure` / `gnma_disclosure` |
| `program` | str | — | `umbs` when in a UMBS pool that month, else null (GNMA: `ginnie_i/ii`) |
| `reporting_period` | i64 | SF `Monthly Reporting Period` / UMBS filename | `YYYYMM` |
| `reporting_year` | i64 | — | partition |
| `month_source` | str | — | `both` / `sf_only` / `umbs_only` |

### Dynamic core (GSE_PERF_PLAN)

`resolve` precedence for shared fields is **UMBS-first**: SF is right-censored at the
release frontier (~9 mo lag), so on a `both` month UMBS is the timelier current-state
view; SF only fills where UMBS is null (pre-2019-06 history).

| column | type | resolve | source(s) |
|--------|------|---------|-----------|
| `loan_age` | i64 | umbs_first | UMBS/SF `Loan Age` |
| `remaining_months_to_maturity` | i64 | umbs_first | UMBS / SF `Remaining Months to (Legal) Maturity` |
| `current_upb` | f64 | sf | SF `Current Actual UPB` (full loan) |
| `current_investor_upb` | f64 | umbs | UMBS `Current Investor Loan UPB` (participation-weighted) |
| `interest_bearing_upb` | f64 | umbs_first | UMBS / SF `Interest Bearing UPB` |
| `current_deferred_upb` | f64 | umbs_first | forbearance deferred balance |
| `current_interest_rate` | f64 | umbs_first | SF==UMBS 99.9999%; moves for ARMs/mods |
| `current_net_interest_rate` | f64 | umbs | UMBS pass-through net rate |
| `servicer_name` | str | umbs_first | **MSR transfers** (~30–40% of loans) — UMBS is the timely source |
| `borrower_assistance_plan` | str | umbs_first | forbearance/workout plan |
| `total_deferral_amount` | f64 | umbs_first | |
| `modification_flag` | str | sf | |
| `number_of_modifications` | i64 | umbs | |
| `mortgage_insurance_percent` | f64 | umbs | dynamic on UMBS (~5% = cancellation) |
| `mi_cancellation_indicator` | str | umbs_first | |
| `estimated_ltv` | i64 | umbs_first | mark-to-market LTV (UMBS ELTV / FHLMC SF ELTV fallback) |
| `updated_credit_score` | i64 | umbs | refreshed score (rare; FHLMC FU null) |
| `cusip` | str | umbs | per-month pool identity |
| `security_identifier` | str | umbs | |
| `prefix` | str | umbs | |

### Delinquency & events (bespoke)
| column | type | source | notes |
|--------|------|--------|-------|
| `delinquency_months` | i64 | SF `Current Loan Delinquency Status` parsed | 0 = current (GNMA-compatible) |
| `delinquency_raw` | str | SF status string | |
| `days_delinquent_umbs` | i64 | UMBS `Days Delinquent` | mislabeled 0–6 CYCLE count, null ~17% |
| `event_flag` | bool | — | terminal month (ZBC populated) |
| `event_type` | str | ZBC taxonomy | prepaid_or_matured / third_party_sale / short_sale_or_chargeoff / repurchase / reo_disposition / note_sale / reperforming_or_other_sale / removal |
| `event_raw_code` | str | ZBC (2-char) | |
| `is_credit_event` | bool | — | ZBC ∈ {02, 03, 09} |
| `zero_balance_effective_date` | date | SF | terminal-month convenience (full waterfall in file 2) |

## File 2 — `data/combined/loan_terminations` (grain: one row per loan terminal event)

GSE only (FNMA + FHLMC; GNMA has no loss waterfall). Hive-partitioned
`agency=/zero_balance_year=`. Filtered to rows where `Zero Balance Code` is populated
(~1 per loan). SF-sourced.

| column | type | notes |
|--------|------|-------|
| `loan_key`, `loan_key_kind`, `agency`, `source_dataset` | — | join back to panel / issuance by `loan_key` |
| `zero_balance_code` | str | normalized 2-char |
| `event_type`, `is_credit_event` | str/bool | same taxonomy as the panel |
| `zero_balance_effective_date`, `zero_balance_year` | date/i64 | event date; partition |
| `upb_at_removal` | f64 | balance at removal |
| `last_paid_installment_date`, `foreclosure_date`, `disposition_date`, `defect_settlement_date` | date | event timeline |
| `net_sales_proceeds`, `credit_enhancement_proceeds`, `mi_recoveries`, `non_mi_recoveries`, `repurchase_make_whole_proceeds` | f64 | recoveries/proceeds |
| `foreclosure_costs`, `legal_costs`, `maintenance_preservation_costs`, `taxes`, `misc_expenses`, `asset_recovery_costs`, `expenses` | f64 | cost waterfall |
| `actual_loss`, `modification_cost`, `delinquent_accrued_interest`, `principal_forgiveness`, `principal_writeoff` | f64 | loss components |

**Note:** several FNMA CRT-only loss fields (`credit_event_net_gain_loss`, modification-loss
amounts) are absent in the *standard* SF Loan-Performance product → carried for schema
completeness but null for FNMA here. FHLMC populates its loss waterfall for the ~0.05%
of loans with losses.

## Build & CLI

```bash
mortgage-data combined build -t loan_performance   # tri-agency panel (rebuild: --overwrite)
mortgage-data combined build -t loan_terminations  # GSE events file
```

`build_loan_performance` materializes each GSE's UMBS perf side to a temp parquet once,
computes the tri-agency canon, then chunks by SF vintage file (full-outer join per chunk)
plus a final UMBS-only anti-join pass. **The full build is I/O-heavy** (the UMBS temp is
re-scanned per vintage); run it as a one-off. Validated on a real FNMA slice (1 vintage ×
6 UMBS months): `month_source` tagging, dual UPB population, delinquency parse, CUSIP on
monthly rows, recency-tail (`gse_numeric` + `umbs_only`) and event taxonomy all confirmed.
