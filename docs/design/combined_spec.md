# Combined Master Datasets — Harmonization Spec

**Status:** DRAFT — _dated by the maintainer_

This document specifies how the five cross-source "combined" master datasets are harmonized from the per-agency silver layers (GNMA, FNMA, FHLMC, plus the regulatory/identity sources HMDA, FDIC, NCUA, FFIEC, FHLB, FHFA). It defines, for each target grain, a source-neutral target schema, the agency-by-agency source-to-target crosswalk, the consolidated code/value harmonization catalog, the identifier and join strategy, the units/scaling/sentinel normalization rules, the coverage matrix, and the consolidated set of open questions the maintainer must decide before each builder is implemented.

**Build status (2026-06-25):** `loan_issuance` and `mbs_issuance` are **built** (tri-agency, production), `loan_performance` is **partially built** (GNMA v1; GSE spines pending), and `mbs_performance` + `issuer` remain `NotImplementedError` scaffolds. See the §1 table for per-grain status.

---

## 1. Scope & target grains

| grain | row meaning | source agencies / datasets | build status |
|---|---|---|---|
| `loan_issuance` | one loan at origination/issuance | GNMA `dailyllmni`; FNMA `FNM_ILLD` + SF Loan-Perf `issuances`; FHLMC `FRE_ILLD` + SF origination | **BUILT** (tri-agency, 65.1M rows) |
| `loan_performance` | loan × month | GNMA `llmon1`/`llmon2` L; FNMA `FNM_MLLD` + SF Loan-Perf; FHLMC `FU` + SF Loan-Perf | **PARTIAL** — GNMA v1 built (llmon1∪llmon2 L); GSE spines (SF-perf ⋈ MLLD/FU, *fused* per the 2026-06-25 alignment investigation) pending |
| `mbs_issuance` | one security/pool at issuance | GNMA `nimonSFPS` (+`nissues` backfill); FNMA `FNM_IS`(+`FNM_RIS`); FHLMC `FRE_IS`(+`FRE_RIS`) | **BUILT** (tri-agency, 612K rows) |
| `mbs_performance` | pool × month | GNMA `factor*`/`CPRmon`/`monthlySFPS`/`ptermmon`; FNMA `FNM_IS`/`FNM_DPR_FCTR`; FHLMC `FRE_IS`/`FRE_DPR_Fctr`/`PF` | placeholder (`build_mbs_performance` scaffold) |
| `issuer` | canonical party × effective period | GNMA `issuers`/`issrinfo`; GSE seller/servicer names; HMDA LEI; FDIC `CERT`/`FED_RSSD`; NCUA `CU_NUMBER`; FFIEC `IDRSSD`; FHLB `FHFBID` | placeholder (`build_issuer` scaffold) |

> `msr_transfers` is registered in `config.DATASET_MAP` as `external=True` and is produced by a separate project; it is **not** built by this package and is out of scope for this spec.

**Cross-cutting structural premise.** Within each GSE there are **two non-ID-joinable loan-level universes**: a UMBS pool-disclosure spine (ILLD/MLLD/FU — pool/CUSIP-linked, no termination code) and an SF credit-research spine (SF Loan-Perf — full Zero-Balance-Code waterfall, no pool/CUSIP). GNMA is a third spine with its own fixed-point scaling, removal-reason taxonomy, and no persistent loan ID. The loan grains therefore carry a mandatory `source_dataset`/`source_universe` discriminator and treat these as separate spines bridged only by attribute fingerprint, never by an ID equi-join.

---

## 2. Source dataset inventory matrix

| agency | level | dataset(s) | grain | coverage | key id |
|---|---|---|---|---|---|
| GNMA | loan-issuance | `dailyllmni` | loan | 2013-09→ | `Pool ID` ‖ `Disclosure Sequence Number` (no native loan ID) |
| GNMA | loan-perf | `llmon1` (Ginnie I), `llmon2` (Ginnie II) L | loan×month | 2013-10→ | `Pool ID` ‖ `Disclosure Sequence Number` |
| GNMA | mbs-issuance | `nimonSFPS` (PS); legacy `nissues` (D, 201202–202104) | pool | 202001→ (backfill 201202) | `Pool ID` / `CUSIP` |
| GNMA | mbs-perf | `factorA1/A2/Aplat/AAdd/B1/B2`, `CPRmon`, `monthlySFPS`, `ptermmon` | pool×month | factor 2012-08→; CPRmon 2025-07→ | `Pool ID` / `CUSIP` |
| GNMA | issuer | `issuers`, `issrinfo` | party | issuers 2012+; issrinfo 2018-04+ | `Issuer ID` (4-digit) |
| FNMA | loan-issuance | `FNM_ILLD`; SF Loan-Perf `issuances` | loan | ILLD 2019-06→; SF 2016Q1→ | `Loan Identifier` (anon str / 12-dig num, disjoint) |
| FNMA | loan-perf | `FNM_MLLD`; SF Loan-Perf | loan×month | MLLD 2019-06→; SF 2016Q1→ | `Loan Identifier` (disjoint per spine) |
| FNMA | mbs-issuance | `FNM_IS`(+`FNM_RIS`), `FNM_GN_MEGA`, `FNM_ISS` | pool | 2019-06→ | `CUSIP` / `Security Identifier` |
| FNMA | mbs-perf | `FNM_IS`, `FNM_DPR_FCTR` (by Type-of-Security agg) | pool×month | 2019-06→ | `Security Identifier` / `CUSIP` |
| FHLMC | loan-issuance | `FRE_ILLD`; SF origination | loan | ILLD 2019-06→; SF 2019→ (raw) | `Loan Identifier` 98*/99* / `Loan Sequence Number` `F{YY}Q…` |
| FHLMC | loan-perf | `FU`; SF Loan-Perf `historical_data_time_*` | loan×month | FU 2019-06→; SF 2019→ | `Loan Identifier` (98*/99*, mod→MA*) / `Loan Sequence Number` |
| FHLMC | mbs-issuance | `FRE_IS`(+`FRE_RIS`); AR/FD/XF variants; MI/MW (MF) | pool | 2019-06→ | `CUSIP` / `Security Identifier` |
| FHLMC | mbs-perf | `FRE_IS`, `FRE_DPR_Fctr` (cohort agg), `PF` (unschema'd) | pool×month | 2019-06→ | `Security Identifier` / `CUSIP` |
| HMDA | issuer | LAR (`lei` post-2018; `respondent_id` pre-2018) | party | 2007→ (LEI 2018→) | `lei` / `respondent_id` |
| FDIC | issuer | institutions endpoint | party | current | `CERT` / `FED_RSSD` |
| NCUA | issuer | FOICU | party | redesign 2022 | `CU_NUMBER` / RSSD |
| FFIEC | issuer | HMDA panel / transmittal | party | quarterly | `IDRSSD` (RSSD anchor) |
| FHLB | issuer | Avery panel | party | Avery + quarterly | `FHFBID` |
| FHFA | issuer (provenance) | disclosure / PUDB | party | — | none (reached via FHFA-HMDA crosswalk) |

---

## 3. Per-grain target schemas

For brevity, each grain lists its target schema and its source-to-target crosswalk. `—` = absent in that agency; `[v]` = version-gated. Code harmonization for shared categoricals is consolidated in §4; units/sentinels in §6; identifiers in §5.

### 3.1 `loan_issuance`

There are **five at-issuance universes**, one logical row per (agency, source_spine, loan), discriminated by `source_dataset`.

| target_field | type | definition | required? |
|---|---|---|---|
| `agency` | enum {GNMA,FNMA,FHLMC} | issuing/guaranteeing agency | yes |
| `source_dataset` | enum {gnma_dailyllmni, fnma_illd, fhlmc_illd, fnma_sf_perf, fhlmc_sf_orig} | spine of origin | yes |
| `loan_key` | string | agency-native loan identifier (§5) | yes |
| `loan_key_kind` | enum {umbs_anon, gse_numeric, sf_seq, gnma_seq} | namespace of `loan_key` | yes |
| `pool_id` | string | GNMA Pool ID / GSE Security Identifier | GNMA+UMBS |
| `cusip` | string(9) | security CUSIP | UMBS only |
| `security_prefix` | string | product/pool prefix | UMBS only |
| `issuance_month` | int YYYYMM | disclosure month at issuance | yes |
| `first_payment_date` | int YYYYMM | first scheduled payment | yes |
| `origination_date` | date YYYYMMDD | note origination | GNMA(V1.6+)/SF |
| `maturity_date` | int YYYYMM | scheduled maturity | yes |
| `loan_purpose` | enum (§4.1) | purchase / refi-rate-term / cash-out / construction / modified-reissue / other | yes |
| `refi_type` | enum {streamline, cash_out, not_streamline, na} | refi sub-type | GNMA only |
| `occupancy` | enum {principal, second, investor} | occupancy | GSE only |
| `property_type` | enum {single_family, condo, coop, pud, manufactured, unknown} | dwelling type | GSE only |
| `number_of_units` | int 1–4 | living units | yes |
| `channel` | enum {retail, correspondent, broker, tpo, not_third_party, unknown} | origination channel | yes |
| `amortization_type` | enum {FRM, ARM} | product type | yes |
| `first_time_homebuyer` | bool | FTHB flag | partial |
| `loan_term_months` | int | original amortization term | yes |
| `note_rate` | float % | origination/note rate | yes |
| `net_rate` | float % | pass-through net of g-fee/servicing | UMBS only |
| `original_upb` | float USD | original principal balance | yes |
| `issuance_investor_upb` | float USD | investor share of UPB at issuance | UMBS only |
| `credit_score` | int 300–850 | origination FICO/VS4 | partial |
| `credit_score_model` | enum {classic_fico, vantage4} | scoring model | yes-if-score |
| `ltv` | int % | original LTV | partial (GNMA null pre-May-2017) |
| `cltv` | int % | original combined LTV | partial |
| `dti` | int % | debt-to-income | partial |
| `number_of_borrowers` | int | borrower count | yes |
| `mi_percent` | float | private-MI coverage % (GSE only; see §4.9) | partial |
| `property_state` | string(2) | USPS state | yes |
| `msa` | string(5) | CBSA/MSA | GNMA + SF |
| `zip3` | string(3) | 3-digit ZIP prefix | SF only |
| `government_insurer` | enum {FHA, VA, RD, PIH, none} | GNMA insurer | GNMA only |
| `seller_name` | string | seller/issuer institution | yes |
| `servicer_name` | string | servicer institution | UMBS/SF |
| `issuer_id` | int | GNMA Issuer ID | GNMA only |
| `seller_issuer_id` | int | GNMA Seller Issuer ID | GNMA(V1.6+) |
| `special_eligibility_program` | enum {na, affordable, refi_relief} | HomeReady/Home Possible/RefiNow | GSE UMBS |
| `property_valuation_method` | enum {appraisal, ace_avm, other, unknown} | valuation method | GSE only |
| `interest_only` | bool | IO indicator | GSE only |
| `arm_index` | string | ARM index code | ARM only |
| `arm_margin` | float % | gross margin | ARM only |
| `schema_version` | string | source schema vintage | yes |

**Source-to-target crosswalk (`loan_issuance`)** — selected load-bearing rows; full agency notes in §4/§6:

| target_field | FNMA (ILLD / SF-Perf) | FHLMC (ILLD / SF-orig) | GNMA dailyllmni | transform |
|---|---|---|---|---|
| `loan_key` | `Loan Identifier` / `Loan Identifier`(12-dig) | `Loan Identifier` 98*/99* / `Loan Sequence Number` | `Pool ID`‖`Disclosure Sequence Number` | GNMA synthesize composite (§5) |
| `pool_id` | `Security Identifier` / — | `Security Identifier` / — | `Pool ID` | SF spines have no pool linkage |
| `cusip` | `CUSIP` / — | `CUSIP` / — | via L→P join on Pool ID | GNMA L lacks CUSIP |
| `first_payment_date` | `First Payment Date` M(M)YYYY / MMYYYY | M(M)YYYY / YYYYMM | CCYYMMDD | three encodings (§6) |
| `origination_date` | — / `Origination Date` MMYYYY | — / derive | `Loan Origination Date` [v V1.6+] | ILLD has none |
| `loan_purpose` | P/C/N/**M** / P/**R**/C | P/C/N/M / P/C/N(/R) | `Loan Purpose` 1/2/3/4 | 4 code-spaces (§4.1) |
| `occupancy` | P/S/I / P/I/S | letters / P/I/S/9 | — | GNMA has NO occupancy field |
| `property_type` | SF/CO/CP/PU/MH | letters / +99→null | — (its field = unit count!) | do not map GNMA |
| `number_of_units` | `Number of Units` | `Number of Units`(99=NA) | `Property Type (Number of Living Units)` | GNMA's "property type" IS this |
| `channel` | R/C/B/T | R/B/C/T/9 | `Third-Party Origination Type` 1/2/3 | letter vs numeric (§4.5) |
| `amortization_type` | FRM/ARM | FRM/ARM | derive (Index Type[v]/margin>0) | GNMA no explicit flag pre-V1.7 |
| `note_rate` | % / % | % / % | `Loan Interest Rate` ÷1000 | GNMA fixed-point (§6) |
| `original_upb` | USD / USD | USD / USD (**rounded $1k**) | `Original Principal Balance` ÷100 | GNMA cents; FHLMC SF rounded |
| `credit_score` | `Borrower Credit Score`→`Classic FICO`[v] / `Credit Score at Origination` | same / `Credit Score` | `Credit Score` int | sentinels differ (§6) |
| `ltv` | `Loan-To-Value (LTV)` / `Original LTV` | / `Original LTV`(999=NA) | `Loan To Value (LTV)` ÷100 | GNMA null pre-May-2017 |
| `mi_percent` | `Mortgage Insurance Percent` / `MI Percentage` | string / `MI %`(0=none,999=NA) | `Annual MIP`/`Upfront MIP` ÷1000 | **semantic mismatch** (§4.9) |
| `msa` | — / `MSA`(5) | — / `MSA`(5) | `MSA`(5) | ILLD lacks MSA |
| `zip3` | — / `Zip Code Short` | — / `Postal Code` | — | SF only |
| `government_insurer` | — | — | `Agency (Loan Type)` F/V/R/P/NA | GNMA only (§4.10) |
| `special_eligibility_program` | 7/H/R | 7/H/R / `Program Indicator` H/F/R/9 | — | GSE only |

### 3.2 `loan_performance`

One row per (agency, source_universe, loan, reporting_period). Origination-snapshot covariates live on `loan_issuance` and are joined by `loan_key` — not re-stated here (GNMA blanks them on the liquidation-month row; UMBS values are static).

| target_field | type | definition | required? |
|---|---|---|---|
| `agency` | P | GNMA / FNMA / FHLMC | yes |
| `source_universe` | P | umbs_disclosure / sf_credit / gnma_disclosure | yes |
| `program` | P | GNMA ginnie_i/ginnie_ii; GSE conventional | yes |
| `loan_key` | P | within-spine loan id (§5) | yes |
| `pool_id` | P | GNMA Pool ID / GSE Security Identifier; null in SF | cond. |
| `cusip` | P | 9-char CUSIP; null in SF | cond. |
| `reporting_period` | D (YYYYMM) | as-of month | yes |
| `current_upb` | $ | current UPB | yes |
| `current_interest_rate` | % | current note rate | yes |
| `current_net_rate` | % | pass-through/net rate | no (GNMA null) |
| `loan_age` | I | months seasoning | yes |
| `remaining_months_to_maturity` | I | RMM | no |
| `delinquency_months` | I | full cycles delinquent (0=current), capped at 6 cross-agency | yes |
| `delinquency_months_exact` | I | uncapped cycles (GSE/UMBS only) | no |
| `delinquency_raw` | P | original agency token (audit) | no |
| `event_flag` | P | Y if terminated/removed this period | yes |
| `event_type` | P | unified event taxonomy (§4.8) | cond. |
| `event_raw_code` | P | original ZBC / Removal Reason (audit) | cond. |
| `event_upb` | $ | UPB at removal/termination | no |
| `modification_flag` | P | Y/N modified as of period | no |
| `modification_type` | P | harmonized mod type (§4.10b) | no |
| `modification_type_raw` | P | raw agency mod code (audit) | no |
| `current_deferred_upb` | $ | deferred principal | no (GNMA absent) |
| `borrower_assistance` | P | harmonized forbearance/workout (§4 note) | no |
| `scheduled_principal` | $ | scheduled principal | SF-credit only |
| `unscheduled_principal` | $ | prepayment principal | SF-credit only |
| `loss_amount` | $ | actual/net credit-event loss | SF-credit only |
| `property_state` | P | 2-char USPS | no |

**Source-to-target crosswalk (`loan_performance`)** — load-bearing rows:

| target_field | FNMA (MLLD / SF-Perf) | FHLMC (FU / SF-Perf) | GNMA llmon1/2 L | transform |
|---|---|---|---|---|
| `program` | const conventional | const conventional | llmon1→ginnie_i, llmon2→ginnie_ii | **UNION llmon1+llmon2** (llmon2 ≈95%) |
| `loan_key` | `Loan Identifier` / `Loan Identifier`(12-dig) | `Loan Identifier`(98*/99*, mod→MA*…) / `Loan Sequence Number` | `Pool ID`+`Disclosure Sequence Number` | disjoint namespaces |
| `current_upb` | `Current Investor Loan UPB` / `Current Actual UPB` | same / `Current Actual UPB` | `Unpaid Principal Balance` | GNMA ÷100; investor vs actual differ (§7) |
| `current_interest_rate` | `Current Interest Rate` | `Current Interest Rate` | `Loan Interest Rate` ÷1000 | — |
| `delinquency_months` | SF status 0/1/2/…; MLLD derive `Days Delinquent`÷30 | SF status 0/1/2/RA/XX; FU derive | `Months Delinquent` 0–6 (6=6+) | 3 encodings (§4.8b) |
| `event_flag` | SF: ZBC non-null; MLLD: **disappearance** | SF: ZBC non-null; FU: disappearance | `Current Month Liquidation Flag`=Y | UMBS has no event row |
| `event_type` | ZBC → map | ZBC → map | `Removal Reason` → map | core harmonization (§4.8) |
| `modification_type` | MLLD T/B/F/R/C/O / SF amounts | FU B/T/D/R/C/S/U/O/F | — (mod via Removal Reason=4) | code-spaces differ (§4.10b) |
| `loss_amount` | SF `Actual Loss` block | SF `Actual Loss Calculation` | — | SF-credit only |

### 3.3 `mbs_issuance`

One row per security/pool at issuance. The post-merge silver IS/PS record is the spine; ISS-family stratifications are out of grain.

| target_field | type | definition | required? |
|---|---|---|---|
| `agency` | enum | GNMA/FNMA/FHLMC | yes |
| `pool_id` | string | Pool ID / Security Identifier | yes |
| `cusip` | string(9) | security CUSIP (cross-agency key) | yes |
| `prefix` | string | product/prefix (GSE; GNMA null) | no |
| `program` | enum {GNMA_I,GNMA_II,UMBS,MBS,MEGA,PLATINUM,SUPERS,MF} | structure family | yes |
| `pool_type_raw` | string | native pool/issue-type code | no |
| `issue_type` | enum {single_issuer, multi_issuer, custom, manufactured} | GNMA Pool Indicator X/C/M | GNMA only |
| `issue_date` | date | issuance date | yes |
| `maturity_date` | date | maturity | yes |
| `as_of_date` | month | disclosure month | yes |
| `loan_count` | int | loans in pool | yes |
| `original_face` | float USD | original aggregate / issuance investor UPB | yes |
| `current_face` | float USD | current investor UPB (=orig at issuance) | no |
| `pool_upb` | float USD | sum of loan UPBs | GNMA only |
| `security_factor` | float 0–1 | current/original factor (=1.0 at issuance) | no |
| `pass_through_rate` | float % | net pass-through coupon | yes |
| `wac` | float % | WA gross note rate | yes |
| `wa_net_rate` | float % | WA net accrual rate | GSE |
| `avg_loan_size` / `wa_loan_size` | float USD | AOLS / WA original loan size | no |
| `wa_orig_loan_term` / `wa_rem_term` / `wa_loan_age` | int mo | WAOLT / WARM / WALA(~0) | yes / yes / yes |
| `wa_ltv` / `wa_cltv` / `wa_dti` | int % | WA LTV / CLTV / DTI | no (GNMA LTV null pre-May-2017) |
| `wa_credit_score` | int | WA Classic FICO | no |
| `wa_vs4` | int | WA VantageScore 4.0 | GSE ≥202512 |
| `wa_margin` / `index_type` / `is_arm` | float% / string / bool | ARM block | ARM |
| `is_interest_only` | bool | IO security | no |
| `tpo_upb_pct` | float % | % UPB third-party-originated | GSE only |
| `issuer_id` | string | GNMA Issuer Number (4-digit) | GNMA only |
| `issuer_name` / `seller_name` / `servicer_name` | string | party names | issuer all; seller/servicer GSE |
| `social_indicator` / `green_indicator` | bool | ESG flags | social all (GNMA ~202410+); green GSE |
| `is_resecuritization` | bool | pool-of-pools (exclude from flow aggregates) | yes |
| `record_source` | enum {original, correction} | reissue/correction provenance | yes |

**Source-to-target crosswalk (`mbs_issuance`)** — load-bearing rows:

| target_field | FNMA | FHLMC | GNMA | transform |
|---|---|---|---|---|
| `cusip` | `CUSIP` | `CUSIP` | `CUSIP Number` (PS) | FNM_GN_MEGA Security Identifier Int64→String |
| `program` | derive (Prefix; MEGA+GN→MEGA) | derive (UMBS/MF) | nimonSFPS→GNMA_II if Pool Indicator=X else GNMA_I; platmonPS→PLATINUM | source-keyed (§4) |
| `issue_type` | — | — | `Pool Indicator` X/C/M | GNMA only |
| `issue_date` | `Issue Date` Int64 8-dig **MMDDYYYY** (verified) | `Issue Date` Int64 **MMDDYYYY** (verified; NOT MMYYYY) | `Issue Date` PS YYYYMMDD; factor MMDDYY | both GSEs identical MMDDYYYY — resolves OQ#11 (correction 2026-06-23) |
| `original_face` | `Issuance Investor Security UPB` | same | `Original Aggregate Amount` PS (**already USD, no ÷100**) | all dollars; `nimonSFPS` PS is pre-decoded (correction 2026-06-23). The ÷100 cents rule applies only to legacy `nissues` D + loan-level `dailyllmni`, NOT PS |
| `pass_through_rate` | `WA Net Interest Rate` | same | `Security Interest Rate` PS ÷1000 | net, NOT WAC (§4.7) |
| `wac` | `WA Issuance Interest Rate` | same | `WA Interest Rate (WAC) at Issuance` PS | gross; differs ~44–50bp |
| `wa_credit_score` | `WA Origination Classic FICO`/`…Credit Score` | `WA Origination Credit Score` (AR/FD/XF: `…Classic FICO`) | `WA Credit Score` PS | alias both names; 9999/7777 |
| `issuer_id` | — | — | `Issuer Number` PS | GNMA only |
| `is_resecuritization` | Prefix=MEGA | XS pseudopool/REMIC | platmonPS/platcoll | flag pool-of-pools |
| `record_source` | `_record_source` (IS+RIS) | `_record_source` (IS+RIS) | const original | GNMA has no reissue twin |

### 3.4 `mbs_performance`

One row per pool × as-of month.

| target_field | type | definition | required? |
|---|---|---|---|
| `agency` | enum | GNMA/FNMA/FHLMC | yes |
| `pool_id` | string | pool identifier | yes |
| `cusip` | string | CUSIP (GNMA via P-record) | yes |
| `as_of_date` | int YYYYMM | reporting/factor month | yes |
| `program` | enum {GINNIE_I,GINNIE_II,UMBS,MBS,PLATINUM,MEGA,…} | program/structure | yes |
| `pool_type` | string | agency-native pool type | no |
| `issue_date` / `maturity_date` | int YYYYMMDD | issuance / maturity | yes / no |
| `orig_security_upb` | $ | original aggregate / issuance investor UPB | yes |
| `current_security_upb` | $ | current investor UPB (=RPB) | yes |
| `rpb_factor` | float 0–1 (≥8dp) | current/original | yes |
| `loan_count` | int | loans remaining | no (factor* lack it) |
| `passthrough_rate` | float % | security/net coupon | yes |
| `wac` | float % | WA gross note rate | yes (factor* lack it) |
| `wala` / `warm` / `waolt` | int mo | WA age / RMM / orig term | yes / yes / no |
| `scheduled_principal` | $ | amortization principal | yes (derived) |
| `unscheduled_principal` | $ | prepayment principal | yes (derived) |
| `total_principal_reduction` | $ | total paydown | yes (derived) |
| `smm` | float 0–1 | single monthly mortality | yes (GNMA-derived disclosed; GSE derived) |
| `cpr_1m` | float % | 1-month CPR | yes (GNMA disclosed; GSE derived) |
| `cpr_3m` | float % | 3-month CPR | no (GNMA disclosed; GSE derived) |
| `involuntary_removal_count` | int | DQ-buyout/repurchase count | GSE only |
| `involuntary_removal_upb` | $ | prior-month UPB of removed loans | GSE only |
| `pool_status` | enum {ACTIVE, TERMINATED} | active/terminated | yes |
| `termination_date` | int YYYYMMDD | pool termination | GNMA `ptermmon` only |
| `record_source` | enum {original, correction} | provenance | no |
| `_source_dataset` | string | originating feed | yes |

**Source-to-target crosswalk (`mbs_performance`)** — load-bearing rows:

| target_field | FNMA | FHLMC | GNMA | transform |
|---|---|---|---|---|
| `current_security_upb` | `Current Investor Security UPB` | same | factor* `Remaining Security RPB` ÷100 / CPRmon / monthlySFPS | RPB |
| `rpb_factor` | `Security Factor` ≤1.0 | same | factor* `RPB Factor` ÷1e8 / CPRmon / monthlySFPS | — |
| `passthrough_rate` | `WA Net Interest Rate` | same | factor* `Pool Interest Rate` ÷1000 / monthlySFPS `Security Interest Rate` ÷1000 | NET not WAC (§4.7) |
| `wac` | `WA Current Interest Rate` | same | CPRmon `WA Interest Rate (WAC)` ÷1000 / monthlySFPS | factor* lack WAC |
| `wala`/`warm`/`waolt` | `FNM_IS` | `FRE_IS` | CPRmon / monthlySFPS | factor* lack WA stats |
| `scheduled_principal` | **derive** (DPR agg cross-check) | **derive** (DPR cohort cross-check) | **derive** (amortization formula) | not disclosed per-pool anywhere |
| `unscheduled_principal` | derive = total−scheduled | derive | derive = ΔRPB−scheduled | — |
| `smm` | **derive** | `FRE_DPR_Fctr.SMM` (cohort) + derive | **derive** (or invert CPRmon CPR) | GNMA discloses CPR not SMM |
| `cpr_1m` | **derive** from factor | `FRE_DPR_Fctr.CPR` (cohort) + derive | `CPRmon` CP-07 ÷10 | only GNMA per-pool |
| `cpr_3m` | derive rolling | derive rolling | `CPRmon` CP-08 ÷10 | GNMA disclosed |
| `involuntary_removal_count/upb` | `FNM_IS` Involuntary Loan Removal | `FRE_IS` same | n/a | GSE only |
| `pool_status` | `Security Status Indicator` | same | derive (factor=0 / absent / ptermmon) | GSE code list not enumerated (OQ) |
| `termination_date` | n/a (disappearance) | n/a | `ptermmon.Termination Date` | GNMA only |

### 3.5 `issuer`

Party master / cross-source identity crosswalk. One row per canonical party × effective period; supporting bridge tables `party_role`, `party_gnma_issuer`, `party_alias`.

| table | target_field | type | definition | required? |
|---|---|---|---|---|
| party_master | `party_key` | string | surrogate PK `PRTY_######` | yes |
| party_master | `party_name` / `party_name_raw` | string | normalized / verbatim legal name | yes / yes |
| party_master | `rssd` | int64 | Fed RSSD anchor; null for nonbank/GSE/GNMA-only | no |
| party_master | `lei` | string | HMDA/GLEIF 20-char LEI; null pre-2018 | no |
| party_master | `fdic_cert` / `ncua_cu_number` / `gnma_issuer_id` / `fhfb_id` | int64 | agency ids | no |
| party_master | `party_type` | enum {bank, thrift, credit_union, nonbank_imb, gse, housing_agency, unknown} | type | yes |
| party_master | `is_depository` | bool | FDIC/NCUA/FFIEC-regulated | yes |
| party_master | `effective_start` / `effective_end` | date | identity validity window | yes / no |
| party_master | `id_source_flags` | bitmap | which sources contributed an id | yes |
| party_role | `party_key`/`agency`/`role`/`agency_party_id` | — | role rows (originator/seller/servicer/issuer/sponsor) | yes |
| party_role | `match_method` | enum {id_exact, rssd_lei_bridge, name_fuzzy, manual} | resolution provenance | yes |
| party_role | `match_confidence` | float 0–1 | confidence | yes |
| party_gnma_issuer | `party_key`/`gnma_issuer_id`/`issuer_name_long`/`issuer_status` | — | 1-party→many-issuer-ids bridge | yes |
| party_alias | `party_key`/`alias_name_raw`/`alias_source` | — | name variants (DBA, seller-vs-servicer spelling) | yes |

**Source-to-target crosswalk (`issuer`)** — load-bearing rows:

| target_field | FNMA | FHLMC | GNMA | transform |
|---|---|---|---|---|
| `party_name_raw` | `Seller`/`Servicer`/`Issuer` name | same | `Issuer Name`/`Issuer Name Long` | free-text on GSE; GNMA has name+id |
| `party_name` | normalize(Seller/Servicer) | same | normalize(Issuer Name Long) | reuse `build_master_crosswalk.normalize_name` |
| `gnma_issuer_id` | — | — | `Issuer ID`/`Issuer Number` | only stable issuer id in any MBS feed |
| `lei` | — | — | — | from HMDA only; bridges to names |
| `rssd` | — | — | — | from FFIEC/FDIC/NCUA; absent from every MBS feed |
| role=seller `agency_party_id` | `Seller Name` | `Seller Name` | `Seller Issuer ID` (V1.6+) + monthlySFPS RT03 | GNMA seller=id, GSE seller=name |
| role=issuer `agency_party_id` | `Issuer`="Fannie Mae" const | `Issuer`="Freddie Mac" const | `Issuer ID` | GSE issuer = the GSE; GNMA issuer = the lender (§4.11) |

---

## 4. Code & value harmonization catalog

One subsection per shared categorical concept. Tables map FNMA / FHLMC / GNMA raw codes to a unified value; notes flag genuine semantic mismatches. (Loan-perf-only and party-only concepts are included where they cross grains.)

### 4.1 Loan purpose — the central mismatch (4 code-spaces)

| unified | GNMA `Loan Purpose` | FNMA ILLD / FHLMC ILLD | FNMA SF-Perf | FHLMC SF-orig |
|---|---|---|---|---|
| `purchase` | 1 | P | P | P |
| `refi_rate_term` | 2 (+ Refi Type≠3) | N | **R** (collapsed) | N (or R) |
| `refi_cash_out` | 2 (+ Refi Type=3) | C | C | C |
| `refi_unspecified` | 2 (Refi Type blank) | — | (R) | R |
| `construction` | 3 | — | — | — |
| `modified_reissue` | — | **M** | — | — |
| `other` | 4, 5 | — | — | 9→null |

Notes: GNMA collapses all refi to `2`; cash-out/rate-term split lives in `Refinance Type` (1/2/3) → derive unified purpose from the **pair** for GNMA, keep `refi_type` as GNMA-only enrichment. FNMA SF-Perf collapses C+N into `R` (`R`→`refi_unspecified`, lossy). `M` modified-reissue exists only in UMBS ILLD/MLLD — flag `is_modified_reissue=true`, exclude from new-origination analytics (null channel). GNMA `3`=construction-to-permanent has no GSE analog; keep distinct. Definition drift in older GNMA pool-grain data ("1=Regular" vs "1=Purchase") — map via table, never positionally. **Incomplete (flagged 2026-06-23):** the silver `dailyllmni` `Loan Purpose` field actually carries codes **1–5**; code `5` is undocumented above (a later schema addition, absent in 2013), and the exact `3`/`4`/`5` labels are NOT authoritatively resolved — sources disagree (this spec says 3=construction/4=other; other project notes suggest 3/4/5 are modification/loss-mit re-pools). The `loan_issuance` builder maps 1→purchase, 2→refi (split by `Refinance Type`), 3→construction, 4/5→other, and **retains the raw digit in `loan_purpose_raw`** for lossless reclassification once the code table is confirmed.

### 4.2 Occupancy

| unified | GNMA | FNMA ILLD / SF-Perf | FHLMC ILLD / SF-orig |
|---|---|---|---|
| `principal` | — | P / P | P / P |
| `second` | — | S / S | S / S |
| `investor` | — | I / I | I / I |

GNMA dailyllmni and llmon L have **no occupancy field** → null for all GNMA. ILLD glossary order P/S/I; SF-Perf P/I/S — map by **letter, not position**.

### 4.3 Property type

| unified | FNMA | FHLMC ILLD / SF-orig | GNMA |
|---|---|---|---|
| `single_family` | SF | SF / SF | — |
| `condo` | CO | CO / CO | — |
| `coop` | CP | CP / CP | — |
| `pud` | PU | PU / PU | — |
| `manufactured` | MH | MH / MH | — |
| `unknown` | — | — / 99→null | — |

**GNMA "Property Type" is a unit count, NOT a dwelling enum** — route to `number_of_units`, leave `property_type=null` for GNMA.

### 4.4 Number of units — clean integer across all five (GNMA from its mislabeled "Property Type"; SF-orig/ILLD `99`→null).

### 4.5 Channel

| unified | GNMA `TPO Type` | FNMA | FHLMC ILLD / SF-orig |
|---|---|---|---|
| `broker` | 1 | B | B / B |
| `correspondent` | 2 | C | C / C |
| `retail` / `not_third_party` | 3 | R | R / R |
| `tpo` (unspecified) | — | T | T / T |
| `unknown` | — | — | — / 9→null |

**Numeric vs letter mismatch** — GNMA `1/2/3` ≠ GSE `B/C/R/T`; remap by meaning. GNMA `3` conflates "retail" and "not-TPO" → map to `retail`, flag conflation. ILLD `M`-rows have null channel.

### 4.6 Amortization / product type — `FRM`/`ARM` direct for all GSE. GNMA has no explicit flag: derive `ARM` iff (`Index Type` non-blank, V1.7+) or (`Loan Gross Margin` > 0); else `FRM`. Pre-V1.7 GNMA ARM detection relies on margin only — lower confidence.

### 4.7 Pass-through rate vs WAC (security grain) — genuine hazard, not a code mismatch

All three carry **two** rates: a net/security/pass-through coupon and a gross WAC, differing ~44–50 bps (SF). Map `pass_through_rate` ← GNMA `Security Interest Rate`/`Pool Interest Rate` / GSE `WA Net Interest Rate`; `wac` ← GNMA `WA Interest Rate (WAC)` / GSE `WA Issuance/Current Interest Rate`. **Never collapse into one field.** GNMA `CPRmon.CP-11` is gross WAC, not pass-through.

### 4.8 Delinquency status & event taxonomy (loan grain)

**Delinquency** — three encodings → one (`delinquency_months`, canonical = months/cycles, cap at 6 for cross-agency comparability; retain `delinquency_months_exact` GSE/UMBS):

| unified | GNMA `Months Delinquent` | GSE SF status | UMBS `Days Delinquent` |
|---|---|---|---|
| 0 (current) | 0 | 0 | 0 |
| 1 (30–59) | 1 | 1 | 1–59 |
| 2 (60–89) | 2 | 2 | 60–89 |
| 3 (90–119) | 3 | 3 | 90–119 |
| n | 4,5 | 4,5,… | floor(days/30) |
| 6+ (cap) | 6 | 6+ | ≥180 |
| unknown | blank→null | `XX`→null | null |
| REO | — | `RA`→ event_type=foreclosure_reo, null DQ | — |

**Event taxonomy** — GNMA Removal Reason vs GSE Zero-Balance-Code (the hardest unification):

| concept | GNMA Removal Reason | FNMA SF ZBC | FHLMC SF ZBC | UMBS (MLLD/FU) | → unified `event_type` |
|---|---|---|---|---|---|
| voluntary payoff | 1 | 01 | 01 | disappearance | `voluntary_prepay` (→`maturity` if RMM≈0) |
| delinquent buyout | 2 | (n/a; repurch=06) | (n/a) | disappearance w/ prior DQ | `dq_buyout` (GNMA-specific) |
| foreclosure w/ claim | **3** | 09 | 09 | n/a | `foreclosure_reo` |
| loss-mit workout | 4 | — | — | n/a | `loss_mit_workout` |
| substitution / other | 5 / 6 | — | 96 | n/a | `substitution` / `other_removal` |
| third-party sale | — | 02 | 02 | n/a | `third_party_sale` |
| short sale | — | 03 | 03 | n/a | `short_sale` |
| repurchase (seller) | — | 06 | 06 | n/a | `repurchase` |
| note sale | — | 15 | 15 | n/a | `note_sale` |
| reperforming sale | — | 16 | 16 | n/a | `reperforming_sale` |

Resolutions: GNMA `3` (foreclosure-with-claim) is a credit/default terminal event — map to `foreclosure_reo`, and for competing-risks modeling **bucket GNMA `3` WITH `2`** (derived `is_credit_event` = GNMA Removal Reason ∈ {2,3} ∨ GSE ZBC ∈ {02,03,06,09,15}). GNMA `2` DQ-buyout has no GSE analog (GSE delinquent loans disappear from the pool but survive in SF-credit). UMBS spines have **no event code** — set `event_type='active'`, synthesize only a derived `last_obs+1` `exit_unspecified` marker (cannot tell prepay vs default from pool feed). FHLMC `96`→`other_removal`. GNMA blanks covariates on the liquidation-month row → take event covariates from the prior month.

### 4.9 Mortgage insurance — genuine semantic mismatch, do NOT unify into one numeric

GNMA `Upfront MIP`/`Annual MIP` (÷1000) are **government insurance premium rates** (FHA/VA/RD); GSE `Mortgage Insurance Percent` is **private-MI coverage percentage** (conventional). Keep `mi_percent` = GSE-private-MI only; expose GNMA MIP rates in separate `gov_upfront_mip_rate`/`gov_annual_mip_rate` (agency-specific extension). See OQ#2.

### 4.10 Government insurer & modification

**4.10a Government insurer (GNMA-only):** `Agency (Loan Type)`: F→FHA, V→VA, R→RD, P→PIH(184), NA/blank→none. No GSE rows ever populate this. Cleanest cross-agency government-vs-conventional partition.

**4.10b Modification type** — same letters do NOT mean the same thing across GSEs; use two separate dictionaries, never a shared letter map:

| unified | Fannie | Freddie | GNMA |
|---|---|---|---|
| rate_only | T | T | — |
| rate_plus_capitalization | B | B | — |
| forbearance/deferral | F | D (deferral) | — |
| term_extension/recast | R | R | — |
| combination | C | C | — |
| step | (ARM block) | S | — |
| other | O | O, U | Removal Reason 4→loss_mit_workout |

GNMA has no mod-type field (mod surfaces only as Removal Reason 4). Retain raw in `modification_type_raw`. Borrower assistance: FNMA `Borrower Assistance Plan` F/T/R/N vs FHLMC `Borrower Assistance Status Code` F/R/T → forbearance/trial/repayment/none. Pull FNMA deferral from `Alternative Delinquency Resolution` (C/P/D/7), NOT the deferral-mod indicator (100% constant `7`, unusable).

### 4.11 Issuer / party-id semantics — the core party mismatch

| concept | FNMA | FHLMC | GNMA | resolution |
|---|---|---|---|---|
| "Issuer" field | const "Fannie Mae" | const "Freddie Mac" | `Issuer ID` = the lender | **GNMA issuer ≠ GSE issuer.** GNMA: lender issues its own pool, retains servicing → map GNMA `Issuer ID` to role=issuer AND role=servicer. GSE: the GSE is the issuer; the lender is seller/servicer → GSE role=issuer rows are the GSE party (party_type=gse), never a lender. |
| Seller | `Seller Name` (string) | `Seller Name` (string) | `Seller Issuer ID` (4-digit, V1.6+) | GNMA seller=joinable id; GSE seller=fuzzy name |
| Servicer | `Servicer Name` | `Servicer Name` | (issuer = servicer) | — |

Aggregation sentinels (NOT parties — drop before keying): `"SCR"`, `"Multiple"`, `"Other sellers"`, `"Other servicers"`, `"Other"`, GNMA Issuer ID `0000`/blank → set `party_key=NULL`, flag `aggregation_proxy`. GNMA one party → many Issuer IDs (separate Ginnie I/II numbers, acquisitions) → `party_gnma_issuer` is 1-party-to-many. Seller-vs-servicer name-spelling drift → collapse via `party_alias`.

### 4.12 ARM index (where carried) — normalize GSE free-text `Index` and GNMA `Index Type` short code to controlled vocab {CMT, LIBOR, SOFR, COFI, TREASURY, OTHER}; preserve raw in `index_type_raw`.

### 4.13 Special-eligibility / program — FNMA/FHLMC UMBS `7/H/R` (affordable/refi-relief); FHLMC SF `Program Indicator` H/F/R/9 → {na, affordable, refi_relief}. GNMA has none.

---

## 5. Identifier & join strategy

**Primary keys**

| grain | primary key | note |
|---|---|---|
| `loan_issuance` | `(agency, source_dataset, loan_key)` + `issuance_month` | `source_dataset` mandatory — two GSE spines reuse "Loan Identifier" for disjoint namespaces |
| `loan_performance` | `(agency, source_universe, loan_key, reporting_period)` | same discriminator requirement |
| `mbs_issuance` | `(agency, cusip)`; fallback `(agency, pool_id, as_of_date)` | CUSIP is the universal 9-char security key |
| `mbs_performance` | `(agency, pool_id, as_of_date)` | — |
| `issuer` | surrogate `party_key` (`PRTY_######`) | no natural key spans all sources |

**Per-agency `loan_key` construction.** GNMA: `Pool ID ‖ Disclosure Sequence Number` (`gnma_seq`), the only cross-month tracking key; handle pre-2021 trailing space whitespace-insensitively. FNMA ILLD: native `Loan Identifier` (`umbs_anon`), persists through re-securitization → dedupe to first issuance month. FHLMC ILLD: `Loan Identifier` 98*/99* (`gse_numeric`), modified loans switch to `MA*/HB*/HA*/RK*/MB*`. FNMA SF: 12-digit numeric, disjoint from ILLD. FHLMC SF: `F{YY}Q…` (`sf_seq`).

**Cross-grain links (loan ↔ pool ↔ CUSIP).**
- GNMA: loan L row → `Pool ID` (no CUSIP on L) → join L→P (pool header, same file) on `Pool ID` to attach CUSIP (validated 100%) → CUSIP → security grain.
- GSE UMBS: loan ILLD/MLLD/FU carries `Security Identifier` + `CUSIP` + `Prefix` directly → trivial equi-join to `FNM_IS`/`FRE_IS`.
- GSE SF-credit spines: **no pool/CUSIP at all** — cannot link to a security; loan-only.

**Resecuritization (avoid double-count).** GNMA `platcoll` (J record) maps Platinum CUSIP → underlying pools; FNMA Megas (`FNM_GN_MEGA`) and FHLMC pseudopools/REMIC (`ISS RT43` Collateral List) map resec → constituents. Set `is_resecuritization=true` and **exclude resec securities from flow/RPB aggregates**.

**Cross-spine / cross-agency loan linkage.** No shared loan key exists across agencies, and no public ID bridge between an agency's UMBS spine and its SF-credit spine. Linkage is **attribute-fingerprint only** (FHLMC modified-loan recovery `MA*`→`F{YY}Q…` ~73.6%; FNMA in-panel via persistent ILLD `Loan Identifier` ~82%). Surface a separate probabilistic `fingerprint_id`; never overload `loan_key`.

**Issuer anchor spine — `RSSD ↔ LEI`.** RSSD is the only id shared by FDIC/NCUA/FFIEC; LEI is the only id in HMDA. The FFIEC HMDA Panel provides `LEI ↔ RSSD` (post-2018); the Avery file provides pre-2018 `respondent_id ↔ RSSD ↔ FHFBID`. GNMA `Issuer ID` and GSE seller/servicer names enter **only by name-fuzzy** (no id bridge to RSSD/LEI). Reuse existing crosswalks rather than re-deriving: `crosswalk/hmda_mbs/master_crosswalk_{fnma,fhlmc}.parquet` (HMDA LEI ↔ UMBS seller name), `crosswalk/hmda_mbs/fhlb_crosswalk_fnma.parquet` (HMDAIndex ↔ lei), `data/matching/hmda_fhlb/hmda_fhlb_members_post2018.parquet` (AMA→LEI, FHLB membership), Avery (`respondent_id ↔ FHFBID ↔ RSSD`), FOICU RSSD↔HMDA panel, `crosswalk/fhfa_hmda/fhfa_hmda_crosswalk_2018_2024.parquet`. Carry each crosswalk's `match_method`/`match_confidence` into `party_role`.

---

## 6. Units, scaling & sentinel normalization rules

GNMA silver stores **raw fixed-point digit strings and does NOT auto-apply COBOL implied decimals** — every GNMA scaling below must be applied manually. GSE feeds are as-published (no scaling).

**Rates → percent (e.g. 6.125):** GSE as-is; GNMA `9(2)v9(3)` → **÷1000** (rate/margin/MIP/ARM caps/pool/security rate) — uniformly, **including V1.0**. (Correction 2026-06-23, verified against silver: the previously-asserted "V1.0 (Oct–Nov 2013) rate `9(3)v9(2)` → ÷100 special case" is NOT borne out — every `dailyllmni` month back to 2013-09 stores the rate as a 5-digit `0RRRR` ÷1000, e.g. `03500`→3.500%; ÷100 would give an absurd ~35–43%.)

**Balances (UPB/OPB/face) → USD:** GSE as-is dollars; GNMA `9(9)v9(2)` / `9(13)v9(2)` → **÷100** (cents). FHLMC SF-orig `Original UPB` is **rounded to nearest $1,000** (privacy) → flag `upb_rounded=true`. UMBS loans aged ≤6mo rounded to $1,000 independently per file (QA note).

**Factors → decimal 0–1:** GSE `Security Factor` ≤1.0 as-is; GNMA factor files `9(1)v9(8)` → **÷1e8**; GNMA PS factor format "1.8".

**Ratios (LTV/CLTV/DTI) → percent:** GSE integer percent as-is; GNMA `9(3)v9(2)` → **÷100** (GNMA gets decimals, GSE integer).

**CPR (GNMA disclosed):** CPRmon CP-07/CP-08 format "3.1" → **÷10** (annualized percent).

**Dates → canonical `YYYYMM` (month fields) or `YYYYMMDD` (full dates).** Multiple encodings — parse against the **source feed's** encoding before unification:

| source / field | raw encoding | transform |
|---|---|---|
| GNMA `As of Date` | CCYYMM (6) | already YYYYMM |
| GNMA first-payment / maturity / origination (loan & PS) | CCYYMMDD (8) | already YYYYMMDD |
| GNMA **factor files** | **MMDDYY (6)** | parse separately |
| FNMA/FHLMC **ILLD/MLLD** first-payment/maturity | **M(M)YYYY** int | year = `v % 10000`, month = `v // 10000` (**NOT** `// 100`) |
| FNMA SF `Monthly Reporting Period` / dates | **MMYYYY** string | year=last4, month=first1–2 |
| FHLMC SF `Monthly Reporting Period` | **YYYYMM** | as-is |
| FHLMC `FRE_IS` `Issue Date` | **MMDDYYYY Int64** (verified; NOT MMYYYY) | zfill(8) → `%m%d%Y` (same as FNMA) |
| FNMA `FNM_IS` `Issue Date` | Int64 8-dig **MMDDYYYY or YYYYMMDD — verify per file** | OQ#11 (blocks date-keyed joins) |

**Sentinel → null map (per agency):**

| concept | GNMA | FNMA UMBS | FHLMC UMBS | FNMA SF | FHLMC SF |
|---|---|---|---|---|---|
| credit score | 0/blank | 9999 | 9999 | blank/`XX` | 9999 |
| LTV/CLTV/DTI | 0/blank (+LTV/CLTV **0 pre-May-2017 ≠ true 0**) | 999 | 999 | blank | 999 |
| units / borrowers | — | — | — | — | 99 |
| MSA | 00000/blank | n/a | n/a | blank | blank |
| FTHB/channel/occupancy/purpose alpha | blank | blank | blank | blank | 9 |
| property type / valuation method | n/a | — | — | — | 99 / 9 |
| VS4 or Classic-FICO "N/A, uses other model" | n/a | **7777** | **7777** | n/a | 7777 |

**`7777` handling:** "Not Applicable, pool uses the other score system." Do NOT coerce to a real score; null that model's column, trust the populated one — but **retain a `_raw` copy** (dropping it breaks ISS-replication aggregate reconciliation). **Special non-null-but-meaningful:** GNMA LTV/CLTV `0`/blank before ~May 2017 = systematically unavailable → null + availability flag (~95–99% populated after). FNMA SF `Loan Age = -1` = pre-first-payment (keep). FNMA SF deferral-mod indicator 100% `7` (unusable). CPRmon negative CPR and CPR≥100 are **real** — do not null (document range [−750, 100]). **Whitespace traps:** GNMA pre-2021 `Disclosure Sequence Number` trailing space; FHLMC `Cumulative CPR` ~120 trailing spaces; double-space headers in FNM_GN_MEGA / FHLMC MI/MW — strip and match whitespace-insensitively.

---

## 7. Coverage & temporal matrix

**Clean common cross-agency windows:** loan grains 2019-06→present (GNMA-only 2013–2018); `mbs_issuance` 2020-01→present (GSE-only 2019-06→2019-12; GNMA backfill to 201202 via `nissues`); `mbs_performance` GNMA factor RPB 2012-08→ (deepest), GSE 2019-06→, GNMA CPRmon 2025-07→.

| capability | GNMA | FNMA | FHLMC | systematic gap |
|---|---|---|---|---|
| persistent loan ID | ✗ (composite) | ✓ | ✓ | GNMA has no native loan/case number |
| pool/CUSIP link (loan) | ✓ via P-record | ✓ UMBS / ✗ SF | ✓ UMBS / ✗ SF | no GSE spine has both pool link AND sub-state geo |
| loan_purpose | coarse 1–4 | P/C/N/M | P/C/N/M(R) | GNMA folds refi into 2 |
| occupancy / dwelling property type | ✗ | ✓ | ✓ | absent for the entire government book |
| LTV/CLTV (loan & pool) | ✗ pre-May-2017; ✓ after | ✓ | ✓ | hard temporal cliff, not row noise |
| DTI / credit score | ~65–87% / ~85–94% | ✓ | ✓ | GNMA partial populations |
| MSA / zip3 | MSA ✓ / zip3 ✗ | ✗ / ✗ | SF-only / SF-only | UMBS ILLD lacks all sub-state geo |
| origination_date | ✓ V1.6+ | ✗ (first-payment only) | SF derive | ILLD has no orig date |
| government_insurer | ✓ | ✗ | ✗ | GNMA-only |
| explicit event/removal code (loan) | ✓ V1.5+ | ✗ UMBS / ✓ SF | ✗ UMBS / ✓ SF | UMBS can never tell prepay vs default |
| loss/disposition waterfall | ✗ | SF-credit only | SF-credit only | neither UMBS nor GNMA has losses |
| disclosed per-pool CPR | ✓ CPRmon (2025-07+) | ✗ derive | cohort only | only GNMA per-pool |
| per-pool scheduled/unscheduled split | ✗ derive | DPR agg only | DPR cohort only | none disclose per-pool → derive all |
| involuntary removal count/UPB (pool) | ✗ | ✓ | ✓ | GNMA pool grain lacks it |
| explicit pool termination date | ✓ ptermmon | ✗ disappearance | ✗ disappearance | GSE termination implicit |
| VS4 credit | ✗ | ✓ ≥202512 | ✓ ≥202512 | UMBS only, post-Dec-2025 |
| numeric institution id (party) | ✓ Issuer ID | ✗ | ✗ | GSE feeds carry no lender id; RSSD in no MBS feed |
| Seller Issuer ID | ✓ V1.6+ (Apr-2015+) | ✗ | ✗ | absent V1.0–V1.5 (Sep-2013–Mar-2015) |
| LEI / RSSD | ✗ / ✗ | ✗ / ✗ | ✗ / ✗ | LEI HMDA-only post-2018; all GSE/GNMA→RSSD is name-fuzzy |

**GNMA schema-version gates:** V1.0 (Oct–Nov 2013, rate ÷1000 like all later months — see §6 correction, no removal fields), liquidation/removal V1.5+, origination date + seller-issuer-id V1.6+, ARM block V1.7+. **Regime breaks to split on:** DU 12.0 (casefiles ≥ 2025-11-16, GSE 620 FICO floor removed), VS4 (≥202512, UMBS only), FHLMC FU mods (Apr-2026+; 0 mods 2019–2025).

---

## 8. Open questions & decisions needed

Consolidated, numbered, de-duplicated across all five grains. This is the section the maintainer must clear before any builder leaves scaffold state.

**Structural / spine**

1. **Two GSE spines per agency — fold or keep parallel?** UMBS (pool-linked, no MSA/zip) vs SF-credit (MSA/zip, no pool link) are non-ID-joinable. Recommend keeping both as distinct `source_dataset`/`source_universe` rows with a probabilistic `fingerprint_id`; confirm the master should NOT merge them into one row per economic loan (they would double-count under different IDs). Applies to both `loan_issuance` and `loan_performance`.
2. **GNMA Ginnie I + II co-residency.** Confirm UNION of llmon1+llmon2 into one spine with a `program` discriminator (picking only llmon2 silently drops ~5% Ginnie I).
3. **Pre-2019 cross-agency scope.** GSE spines don't reach the GNMA 2013 start. Confirm the effective cross-agency window is 2019-06→present, with GNMA-only coverage 2013–2018 (loan grains) and the GNMA `nissues` backfill decision for `mbs_issuance` (see #14).

**Loan-grain code/value**

4. **GNMA loan-purpose coarseness.** Confirm unified `loan_purpose` is derived from the (`Loan Purpose`, `Refinance Type`) pair for GNMA, and that FNMA SF-Perf `R` is acceptably lossy as `refi_unspecified`.
5. **Mortgage-insurance semantics.** GNMA MIP (government premium *rate*) vs GSE MI (private coverage *percent*) are different concepts. Decide: keep `mi_percent` = GSE-private-MI only + agency-specific `gov_upfront_mip_rate`/`gov_annual_mip_rate` for GNMA (recommended), OR a single `mi_field` + `mi_kind` discriminator?
6. **GNMA channel `3` conflation** (retail vs not-third-party collapsed) — accept mapping to `retail`, or add a distinct `not_third_party` value the GSEs can't populate?
7. **GNMA ARM detection pre-V1.7.** No explicit FRM/ARM flag before Dec-2017; deriving from `Loan Gross Margin > 0` is heuristic. Accept the lower-confidence flag for 2013-09→2017-11, or restrict GNMA ARM analytics to V1.7+?
8. **GNMA LTV/credit-score pre-May-2017 nulls.** Confirm convention: map `0`/blank LTV/CLTV (and credit score) before ~2017-05 to null + availability flag (not real zero).
9. **Credit-score model unification.** With VS4 arriving 202512, single `credit_score` column + `credit_score_model` discriminator (recommended) vs two parallel `fico`/`vs4` columns preserving `7777`/`9999`? (SF-credit + GNMA are FICO-only.)
10. **FHLMC SF-orig UPB rounding** ($1,000) — confirm `upb_rounded` flag so it isn't compared loan-for-loan against unrounded UMBS/GNMA UPBs.
11. **`origination_date` availability asymmetry.** Only GNMA (V1.6+) and SF-credit carry a true origination date; UMBS has only first-payment. Confirm `origination_date` stays optional and cross-source analytics key off `first_payment_date`.

**Loan-performance specifics**

12. **One table or two physical spines for `loan_performance`?** Single table with `source_universe` (recommended) co-resides UMBS-disclosure + SF-credit rows that double-count the same GSE loans under different IDs — confirm acceptable.
13. **GNMA liquidation-row provenance** — confirm baking "take event covariates from prior month" into the harmonizer (vs flagging blanked rows, leaving covariates null).
14. **Delinquency top-coding** — keep both `delinquency_months` (capped-at-6, cross-agency) and `delinquency_months_exact` (GSE/UMBS)? (Recommend both.)
15. **`current_upb` semantics** — GSE UMBS reports **investor** UPB (participation-weighted); GNMA and GSE SF report **actual** UPB. Decide which the unified `current_upb` is, and whether to carry both (silent ~1–few% discrepancy for partial-participation pools if mixed).
16. **UMBS synthesized exit** — want a derived `last_obs+1` terminal marker with `event_type='exit_unspecified'`, or leave UMBS rows with no terminal event (event lives only in SF-credit)?
17. **`is_credit_event` definition** — confirm GNMA Removal Reason {2,3} and GSE ZBC {02,03,06,09,15}; decide whether ZBC 15/16 (note/reperforming sales) count as credit events or a separate `non_performing_sale` bucket.
18. **Modification dictionaries** — confirm separate Fannie (T/B/F/R/C/O) and Freddie (B/T/D/R/C/S/U/O/F) maps; decide whether Fannie `F` (forbearance) and Freddie `D` (deferral) collapse to one unified bucket or stay distinct.
19. **FHLMC SF performance has no silver builder yet** (only origination silver exists). Confirm the harmonizer reads FHLMC SF performance straight from bronze, and whether `Net Sales Proceeds` (can be `"C"`/`"U"` codes, not just dollars) needs special parsing before `loss_amount`.

**MBS-issuance specifics**

20. **FNMA `Issue Date` encoding** — Int64 8-digit "MMDDYYYY or YYYYMMDD per sample; confirm per-file." Need a definitive parse rule + validation (do month/day ever exceed 12/31?). Wrong guess silently corrupts every FNMA issue/maturity date — **decision needed before any date-keyed join.**
21. **GNMA Pool Type vocabulary** (SF/ET/RG/JM/…, MBS Guide Ch.1 App.1) is **not transcribed in the repo** — hand-author it, or keep `pool_type_raw` opaque and derive `program`/`is_arm` from other fields?
22. **GNMA pre-2020 backfill** — ingest legacy `nissues` D record (201202–202104; different schema, YYYYMMDD dates, no-implied-decimal UPB on some fields) to extend `mbs_issuance` before `nimonSFPS`, or start the master at 2020-01 (clean, GSE-aligned)?
23. **`7777` handling** — null in analytic `wa_credit_score`/`wa_vs4` but retain a `_raw` copy for ISS-replication reconciliation (recommended) — confirm.
24. **Pass-through vs WAC canonical default** — confirm `pass_through_rate` ← net rate, `wac` ← gross issuance rate; tell single-rate downstream consumers which to use (~44–50bp gap).
25. **Resecuritization scope** — include Megas/Platinum/pseudopools in `mbs_issuance` (flagged `is_resecuritization=true`, always netted in flow aggregates via `platcoll`/`FNM_GN_MEGA`/`ISS RT43`), or split into a separate `mbs_resecuritization` grain?
26. **AR/FD/XF prefix-family variants (FHLMC)** — confirm `FRE_IS` is canonical SF security source and AR/FD/XF are fallbacks; need a dedup rule keyed on `Security Identifier` (which file wins on conflict).
27. **GSE seller/servicer ↔ GNMA issuer_id** — no common institution key (free-text names vs 4-digit Issuer Number). Build a name-normalization crosswalk (reuse §5 machinery) or leave agency-local?
28. **Multifamily scope** — FHLMC `MI`/`MW` (and FNMA `FNM_MF`, GNMA mf*) carry DSCR/balloon/IO instead of LTV/FICO. Confirm MF is a separate grain partition (recommended), excluded from the SF spine.
29. **FHLMC cash-window channel** — Freddie has a reliable pool-ID-prefix cash-window convention (not encoded). Add a `channel` (cash-window vs swap) field FHLMC-only, or omit until confirmed? (FNMA cannot be classified reliably.)

**MBS-performance specifics**

30. **Per-pool CPR/SMM derivation method (GSE).** GSEs disclose prepay only at aggregate grain (`FNM_DPR_FCTR` by Type-of-Security; `FRE_DPR_Fctr` by cohort). Confirm per-pool SMM = unscheduled_principal_t / beginning_scheduled_UPB_t, with unscheduled = (UPB_{t-1} − scheduled_amort_t) − UPB_t — **need sign-off on the scheduled-amortization formula** (WAC + WARM + UPB).
31. **`FRE_DPR_Fctr` / `FNM_DPR_FCTR` grain & SMM/CPR units** — verify on-disk files truly lack a per-pool id; ingest only as aggregate validation? `FRE_DPR_Fctr.SMM`/`CPR` scaling (decimal vs percent) is unannotated — verify against ILLD-derived speeds.
32. **FHLMC `PF` (Pool Factor) ingestion** — flagged as the FHLMC pool-level factor/RPB source but **unschema'd**. Run `umbs.schemas build` and prefer `PF` over deriving from `FRE_IS` month-over-month? Same question whether `FNM_IS.Security Factor` month-over-month is the canonical FNMA source.
33. **GNMA CPRmon vs derived-CPR method split** — CPRmon CPR (pool-period, excludes amortization) does not reconcile with loan-level gross-paydown CPR. Confirm using CPRmon directly for GNMA and deriving only for GSEs (so GNMA and GSE `cpr_1m` come from different methods) — document the method-split or accept minor cross-agency incomparability.
34. **`pool_type` unification** — keep agency-native string and skip a unified enum, or hand-author the GNMA pool-type table + a coarse cross-agency `structure_class` (pass-through / platinum / mega / remic)?
35. **GSE `Security Status Indicator` code list** — not enumerated in the inventories; needs transcription to map `pool_status` {ACTIVE, TERMINATED} (+ intermediate states). Until then derive from factor=0 / disappearance.
36. **Termination semantics asymmetry** — populate `termination_date` for GNMA only (`ptermmon`) and leave GSE null (infer month = last-observed + 1), or synthesize a GSE `termination_date` = first absent month?
37. **Platinum/Mega/REMIC double-count guard** — confirm the combined RPB master excludes Platinum (`platcoll`), Mega (`FNM_GN_MEGA`), REMIC/SMBS tranche balances from any "total outstanding pass-through RPB" aggregate; need an explicit `is_resecuritization` flag + dedupe rule.
38. **GNMA factor-file family selection** — six factor files (A1/A2/Aplat/AAdd/B1/B2) cover Ginnie I/II × Factor-A/B × Platinum/Additional, and include HMBS (out of scope). Confirm the SF-MBS subset rule (union factorA2+factorAplat+factorA1, filter HMBS by `Pool Type`) and which of Factor-A vs Factor-B is the RPB-of-record.

**Issuer / party-identity specifics**

39. **Surrogate vs RSSD as PK** — confirm project surrogate `party_key` (recommended; RSSD-only would drop every nonbank IMB and GSE-name-only party).
40. **GNMA Issuer ID → name → LEI bridge is name-fuzzy** — accept name-fuzzy resolution (reuse `normalize_name` + `compute_name_similarity`), or is there a hand-curated GNMA-issuer ↔ LEI crosswalk seeded from the largest issuers?
41. **GNMA `Issuer Status Indicator` code list** (issrinfo single-char) **not transcribed in the repo** — need the official active/inactive enumeration to populate `issuer_status`.
42. **GNMA `Program` code (issrinfo, 2-char)** — retain as a party attribute (program eligibility) or drop for the identity grain?
43. **Seller vs servicer collapse** — one `party_key` per legal entity (collapse seller/servicer spellings via `party_alias`, recommended) vs keep seller-party and servicer-party distinct?
44. **Aggregation buckets** — confirm "SCR"/"Multiple"/"Other sellers"/"Other servicers" excluded (`party_key=NULL`, `aggregation_proxy=true`) — not parties.
45. **Pre-2018 HMDA party id** — use `respondent_id` (agency-code-prefixed) via the Avery file to reach RSSD/FHFBID; confirm the Avery file is the canonical pre-2018 panel source.
46. **Effective-period granularity** — annual (HMDA `activity_year` / FHFA securitization year) vs monthly (GNMA issrinfo, NCUA `CYCLE_DATE`). Recommend annual periods with monthly source provenance retained.
47. **Merger/acquisition handling** — one surviving `party_key` absorbs acquired entities' historical ids (with `effective_end`), or separate keys linked by `successor_party_key`?
48. **FHFA party id** — FHFA disclosure carries no institution id; reached via the FHFA-HMDA crosswalk. Confirm FHFA is treated purely as a *provenance* source on the HMDA-anchored party, not an independent id axis.
