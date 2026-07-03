# FNMA-HMDA Matching via FHLB Chicago: Direct Approach (Retired)

## Overview

This document describes a **direct probabilistic matching** approach for linking FNMA (Fannie Mae) loan-level disclosure data to HMDA records for loans originated through FHLB Chicago's MPF (Mortgage Partnership Finance) program. The approach was developed, validated, and ultimately **retired in favor of the chain approach** (HMDA → FHFA → MBS → UMBS), which achieves higher coverage and rests on administrative linkages rather than probabilistic matching.

The document is retained as a reference for the methodology and the comparison that motivated the decision.

## Data Sources

- **FNMA ILLD (Investor Loan-Level Disclosure)**: UMBS loan-level data filtered to loans where Seller Name = "FEDERAL HOME LOAN BANK OF CHICAGO" (~134k unique loans, 2018–2024)
- **HMDA Silver**: Post-2018 HMDA loan application register, filtered to FHLB member lenders via LEI crosswalk
- **FHLB-HMDA LEI Crosswalk**: Maps LEI to FHLB district membership (~4,700 LEIs)

## Direct Matching Methodology

### Join Keys (Exact Match)

The direct approach joins FNMA and HMDA records on exact agreement across seven categorical variables:

1. **Year**: FNMA origination year = HMDA activity year
2. **State**: Property state
3. **Loan Amount Bin**: $10k bins (floor division by 10,000), with boundary handling for exact multiples
4. **Loan Purpose**: Purchase / Cash-out refi / No cash-out refi (mapped from FNMA codes to HMDA codes)
5. **Occupancy**: Primary / Secondary / Investment
6. **Term**: Loan term in months
7. **Units**: Number of units

### Tolerance Filters

After the exact join, tolerance-based filters narrow candidates:

| Field | Tolerance | Notes |
|-------|-----------|-------|
| Loan Amount | ±$5,000 | Within $10k bin |
| Interest Rate | ±0.0625% | Most rates in 1/8% increments |
| CLTV | ±1 point | |
| DTI Bin | ±1 | HMDA reports DTI in bins: <20%, 20–30%, 30–36%, exact 36–49, 50–60%, 60%+ |

### Quality Filters

1. **Co-borrower agreement** (required): FNMA Number of Borrowers ≥ 2 must agree with HMDA co-applicant presence. Removes ~20% of candidates with large quality improvement.

2. **Mutual best matching**: For each FNMA loan, find its best HMDA match (lowest rate difference), and vice versa. Keep only pairs where both sides agree. Ensures 1:1 matching.

3. **Lender quality filter**: Drop lenders with both fewer than 10 matches AND less than 2% of originations going through FHLB. Drop singleton lenders. Removes spurious matches from lenders with minimal FHLB activity.

### HMDA Purchaser Type

FHLB-sold loans are typically reported as `purchaser_type=9` ("Other purchaser") in HMDA. Restricting HMDA candidates to type 9 is critical for match quality — non-type-9 candidates (e.g., type 1 = Fannie Mae, type 0 = not sold) produce matches that fail independent validation at high rates. A small number of lenders systematically misreport their FHLB sales under other purchaser types, but including these introduces far more noise than signal.

## Why the Chain Approach is Preferred

The **chain approach** links HMDA to FNMA via administrative record linkages rather than probabilistic matching:

> HMDA → FHFA (via `match_fhfa_hmda`) → MBS (via `match_mbs_fhfa`) → UMBS (via `match_mbs_umbs`)

The final crosswalk is enriched with FNMA ILLD fields including Seller Name, allowing filtering to FHLB Chicago. This approach rests on deterministic identifiers at each link in the chain (FHFA loan number → MBS pool/sequence → UMBS loan ID), rather than matching on observable loan characteristics.

### Head-to-Head Comparison

A systematic comparison of the two approaches on the same FHLB Chicago FNMA universe (133,686 loans) found:

| Metric | Direct (pt9 only) | Chain |
|---|---:|---:|
| FNMA loans matched | 35,457 | 87,667 |
| Match rate | 26.5% | 65.6% |
| Unique lenders | 122 | 1,626 |

The chain achieves 2.5× the coverage. The direct approach's lender quality filter excludes many small lenders that the chain captures.

### Concordance Analysis

Of the 31,252 FNMA loan IDs matched by both approaches, **90.5% were linked to the same HMDA record** (concordant). The remaining 9.5% discordance reflects genuine matching ambiguity — multiple HMDA records from the same lender/year/state with similar characteristics.

From the HMDA side, concordance was 98.3%: when both approaches selected the same HMDA record, they almost always agreed on which FNMA loan it belonged to.

### Marginal Value of the Direct Approach

After restricting to purchaser_type=9, the direct approach captures only 4,205 FNMA IDs that the chain misses (3.1 percentage points of additional coverage). These could be recovered as a supplemental matching step downstream rather than maintained as a standalone workflow.

### Key Takeaway

The direct approach was valuable during development for understanding FHLB-HMDA reporting patterns (especially the purchaser_type=9 finding), but as a production matching workflow it is dominated by the chain approach on both coverage and reliability.

## Historical Notes

### Purchaser Type Reporting Patterns

During development, analysis of HMDA purchaser type distributions revealed that FHLB MPF loans are overwhelmingly reported as `purchaser_type=9` ("Other"). When the direct match was run without this restriction, concordance with the chain dropped from 90.5% to 69.0%, with the discordant matches concentrated among non-type-9 HMDA candidates. This finding — that purchaser_type=9 is the reliable signal for FHLB sales in HMDA — is useful context for any future work involving FHLB loan identification in HMDA data.

### Geographic and Temporal Patterns

The FHLB Chicago MPF program draws lenders from across multiple FHLB districts, with the Dallas district contributing the largest share of participating lenders. Match rates were lower during the 2020–2021 refinance boom due to rate volatility and timing mismatches. These patterns are properties of the underlying data, not the matching methodology, and apply equally to the chain approach.

## Related Investigations

- `investigations/reports/investigation_fhlb_hmda_mbs_comparison_2026-02-14.md`: Full comparison analysis with figures
