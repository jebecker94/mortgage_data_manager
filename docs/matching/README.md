# Matching Workflows — Match Rate Summary

This page summarizes match rates across all matching workflows in the project. Each
row is drawn from the corresponding `docs/matching/{workflow}_matching.md` report;
follow the workflow link for full methodology, round breakdowns, and validation.

Workflows with separate pre-2018/post-2018 pipelines or per-GSE (FNMA/FHLMC) splits
are shown as separate rows to stay faithful to the underlying reports. Rows marked
⚠️ **Preliminary** come from analyses that are documented as subject to revision.

## Summary Table

| Workflow | Datasets linked | Coverage | Source A records | Source B records | Matched pairs | Match rate A | Match rate B | Notes |
|---|---|---|---|---|---|---|---|---|
| [fha_gnma](fha_gnma_matching.md) | FHA endorsements ↔ GNMA loan-level | 2015–2024 | FHA: 10.89M | GNMA FHA: 11.59M | 3.90M | 35.8% (FHA) | 33.7% (GNMA) | ⚠️ Strong state-size bias (Spearman r=−0.907) |
| [fha_hmda](fha_hmda_matching.md) | FHA endorsements ↔ HMDA | 2018–2024 | FHA: 7.20M | HMDA FHA: 7.11M | 6.12M | 84.9% (FHA) | 86.1% (HMDA) | 1:1 matches; post-2018 only |
| [fhfa_hmda](fhfa_hmda_matching.md) (post-2018) | HMDA ↔ FHFA GSE tract | 2018–2024 | FHFA: 32.74M | HMDA: — | 29.19M | 89.2% (FHFA) | — | Per-year 80.0% → 92.7% |
| [fhfa_hmda](fhfa_hmda_matching.md) (pre-2018) | HMDA ↔ FHFA GSE tract | 2009–2017 | FHFA: 41.42M | HMDA: — | 29.23M | 70.6% (FHFA) | 77.3% (HMDA GSE-sold) | Structural ceiling (no rate/LTV/DTI in FHFA) |
| [hmda_fhlb](hmda_fhlb_matching.md) (post-2018) | HMDA ↔ FHLB AMA | 2018–2024 | FHLB: 0.417M | HMDA: — | 0.228M | 54.6% (FHLB) | — | Data-quality bottleneck, not missing keys |
| [hmda_fhlb](hmda_fhlb_matching.md) (pre-2018) | HMDA ↔ FHLB AMA | 2009–2017 | FHLB: 0.441M | HMDA: — | 0.264M | 83.6% (FHLB) | — | Purpose-mapping gain (25.9% → 58.8% on type 2→3) |
| [sellers_purchasers](hmda_sellers_purchasers_matching.md) | HMDA originations ↔ HMDA purchases | 2018–2024 | — | — | 9.74M | — | — | Intra-HMDA link; 8 rounds, R1 = 76.8% |
| [mbs_fhfa](mbs_fhfa_matching.md) (FNMA) | FNMA MBS LLD ↔ FHFA tract | 2019–2024 | FNMA: 15.7M | FHFA: 16.0M | 14.9M | 94.8% (FNMA) | 93.0% (FHFA) | 0.01% interest-rate tolerance |
| [mbs_fhfa](mbs_fhfa_matching.md) (FHLMC) | FHLMC MBS LLD ↔ FHFA tract | 2019–2024 | FHLMC: ~13.4M | FHFA: 13.54M | 12.66M | 93.9% (FHLMC) | 93.5% (FHFA) | MSA crosswalk; high-LTV (>97%) gap ~1.5% |
| [mbs_fhlb](mbs_fhlb_matching.md) (GNMA) | GNMA MBS LLD ↔ FHLB AMA | 2015–2024 | — | — | 15,388 | ~90% (GNMA) | ~90% (FHLB) | Exceptional match quality (rate P95=0.005%); only the gov-insured slice of FHLB AMA is eligible |
| [mbs_fhlb](mbs_fhlb_matching.md) (FNMA) | FNMA UMBS ↔ FHLB AMA | 2019–2024 | — | — | 0 | ~0% | ~0% | Mutual-exclusivity test — confirmed (separate channels) |
| [mbs_umbs](mbs_umbs_matching.md) (FNMA) | MBS monthly perf ↔ UMBS ILLD | Jun 2019– | — | — | 15.50M | ~99% (MBS) | ~98% (UMBS) | Stable after 2019 ramp-up |
| [mbs_umbs](mbs_umbs_matching.md) (FHLMC) | MBS monthly perf ↔ UMBS ILLD | Jun 2019– | — | — | 13.59M | ~99% (MBS) | ~98% (UMBS) | FICO/VantageScore transition handled in R3 |
| [hmda_mbs](preliminary_hmda_mbs_matching.md) (FNMA chain) | HMDA→FHFA→MBS→UMBS | 2019–2024 | — | — | — | 81.9%–99.2% by year | — | ⚠️ **Preliminary** |
| [hmda_mbs](preliminary_hmda_mbs_matching.md) (FHLMC chain) | HMDA→FHFA→MBS→UMBS | 2019–2024 | — | — | — | 84.2%–97.3% by year | — | ⚠️ **Preliminary** |
| [hmda_mbs](preliminary_hmda_mbs_matching.md) (GNMA FHA chain) | HMDA→GNMA FHA | 2018–2024 | — | — | 3.74M | 50.7% coverage | — | ⚠️ **Preliminary** (two-crosswalk + direct combined) |

## Notes on reading the table

- **Match rate A / B** report the share matched from each source dataset's perspective.
  A dash means the report did not state that direction (commonly the HMDA side, where
  the relevant denominator is the GSE-sold or purchased subset rather than all of HMDA).
- **Per-year ranges** (e.g. "81.9%–99.2% by year") indicate the workflow's rate varies
  materially across years; see the workflow report for the full series. 2024 chain rates
  are artificially low pending 2025 MBS data.
- **Caveats** in the Notes column are the headline biases only. Every workflow report
  contains a fuller validation section (temporal, geographic, loan-characteristic).
- **Matched-pair counts** are taken from the workflow reports, except `mbs_umbs` and
  `mbs_fhlb`, whose reports give only approximate rates — those four counts were read
  directly from the crosswalk parquet files (`crosswalk/{workflow}/`) as of 2026-06-09:
  FNMA 15.50M, FHLMC 13.59M (mbs_umbs); GNMA 15,388, FNMA 0 (mbs_fhlb).
