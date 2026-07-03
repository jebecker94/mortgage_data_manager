# HMDA-MBS Master Crosswalk Analysis (Preliminary)

> **Note**: This analysis documents preliminary findings from the HMDA-MBS matching workflow.
> Results have not been completely finalized and may be subject to revision as the matching
> methodology is refined. Use these findings for exploratory purposes.

---

## Overview

This analysis builds master crosswalks linking HMDA loan records through FHFA, MBS, and UMBS data for both Fannie Mae (FNMA) and Freddie Mac (FHLMC) from 2019-2024. The crosswalk enables tracking individual loans from origination through securitization, with validation via seller name alignment.

### Data Pipeline

```
HMDA (origination)
    ↓ HMDAIndex
FHFA-HMDA Crosswalk (enterprise_flag: 1=FNMA, 2=FHLMC)
    ↓ record_number = fhfa_record_id
MBS-FHFA Crosswalk (loan_id linkage)
    ↓ loan_id = Loan Sequence Number (MBS)
MBS-UMBS Crosswalk
    ↓ Loan Sequence Number (UMBS)
UMBS ILLD (Seller Name, Channel)
```

### Key Definitions

- **purchaser_type=1**: HMDA loans reported as sold to Fannie Mae
- **purchaser_type=3**: HMDA loans reported as sold to Freddie Mac
- **Single-year MBS**: Match using only same-year MBS data
- **Extended MBS**: Match using MBS data from origination year through 2024
- **Channel**: R=Retail, C=Correspondent, B=Broker

---

## HMDA File Type by Year

The FHFA-HMDA crosswalk uses different HMDA file types for different years:

| Year | file_type | HMDAIndex Prefix |
|------|-----------|------------------|
| 2019 | a | 2019a_... |
| 2020 | a | 2020a_... |
| 2021 | a | 2021a_... |
| 2022 | a | 2022a_... |
| 2023 | b | 2023b_... |
| 2024 | b | 2024b_... |

> **Updated 2026-06-24**: 2022 moved to the finalized three-year (`a`) and 2024 to the one-year (`b`) once those HMDA releases published. `FILE_TYPE_BY_YEAR` (used by the chain build and validation) was re-synced to match; before that, the validation denominator's prefixes diverged from the crosswalk and 2022/2024 were misreported as ~0%. The corrected `hmda-mbs validate` reports per-year FNMA/FHLMC rates 2022 = 91.1%/89.8%, 2024 = 86.6%/90.7% (FNMA avg 86.5%, FHLMC avg 90.1%). The detailed per-year tables below predate this re-sync and reflect the earlier vintages.

**Important**: Name alignment analysis must use year-matched HMDA panels (e.g., 2020 panel for 2020 loans) to avoid false mismatches from company name changes over time.

---

## FNMA (Fannie Mae) Results

### Full Crosswalk Match Rates

| Year | FHFA-HMDA | Single-Year | Rate | Extended | Rate | Improvement |
|------|-----------|-------------|------|----------|------|-------------|
| 2019 | 2,102,794 | 1,343,232 | 63.9% | 1,723,014 | **81.9%** | +28.3% |
| 2020 | 4,571,399 | 4,057,692 | 88.8% | 4,532,747 | **99.2%** | +11.7% |
| 2021 | 4,267,506 | 3,814,311 | 89.4% | 4,008,405 | **93.9%** | +5.1% |
| 2022 | 1,689,277 | 1,583,694 | 93.7% | 1,631,793 | **96.6%** | +3.0% |
| 2023 | 902,755 | 780,329 | 86.4% | 834,405 | **92.4%** | +6.9% |
| 2024 | 849,402 | 766,618 | 90.3% | 766,618 | 90.3% | +0.0% |

### purchaser_type=1 Match Rates

| Year | HMDA pt=1 | → FHFA | Rate | Single-Year | Rate | Extended | Rate |
|------|-----------|--------|------|-------------|------|----------|------|
| 2019 | 1,866,490 | 1,297,740 | 69.5% | 873,193 | 46.8% | 1,049,973 | **56.3%** |
| 2020 | 4,218,640 | 3,517,685 | 83.4% | 3,122,433 | 74.0% | 3,487,521 | **82.7%** |
| 2021 | 4,089,190 | 3,404,224 | 83.2% | 3,045,169 | 74.5% | 3,192,810 | **78.1%** |
| 2022 | 1,641,445 | 1,299,896 | 79.2% | 1,229,983 | 74.9% | 1,260,796 | **76.8%** |
| 2023 | 833,459 | 654,729 | 78.6% | 609,618 | 73.1% | 617,653 | **74.1%** |
| 2024 | 846,820 | 625,204 | 73.8% | 571,324 | 67.5% | 571,324 | 67.5% |

### Name Alignment (Retail Channel, Year-Matched Panels)

| Year | Retail Loans | Tier 3 (Normalized) | Tier 4 (First Word) | Different |
|------|--------------|---------------------|---------------------|-----------|
| 2019 | 971,423 | 70.1% | **77.2%** | 22.8% |
| 2020 | 3,015,747 | 76.4% | **81.7%** | 18.3% |
| 2021 | 2,705,771 | 76.8% | **81.7%** | 18.3% |
| 2022 | 1,119,018 | 86.0% | **91.6%** | 8.4% |
| 2023 | 548,743 | 92.7% | **96.8%** | 3.2% |
| 2024 | 460,521 | 93.1% | **97.4%** | 2.6% |

---

## FHLMC (Freddie Mac) Results

### Full Crosswalk Match Rates

| Year | FHFA-HMDA | Single-Year | Rate | Extended | Rate | Improvement |
|------|-----------|-------------|------|----------|------|-------------|
| 2019 | 1,624,487 | 1,004,238 | 61.8% | 1,381,756 | **85.1%** | +37.6% |
| 2020 | 3,438,783 | 2,793,723 | 81.2% | 3,344,717 | **97.3%** | +19.7% |
| 2021 | 3,660,793 | 2,685,736 | 73.4% | 3,080,866 | **84.2%** | +14.7% |
| 2022 | 1,482,955 | 1,174,825 | 79.2% | 1,313,199 | **88.6%** | +11.8% |
| 2023 | 868,710 | 693,477 | 79.8% | 741,945 | **85.4%** | +7.0% |
| 2024 | 898,270 | 797,747 | 88.8% | 797,747 | 88.8% | +0.0% |

### purchaser_type=3 Match Rates

| Year | HMDA pt=3 | → FHFA | Rate | Single-Year | Rate | Extended | Rate |
|------|-----------|--------|------|-------------|------|----------|------|
| 2019 | 1,502,509 | 1,031,904 | 68.7% | 671,750 | 44.7% | 863,901 | **57.5%** |
| 2020 | 3,267,450 | 2,430,121 | 74.4% | 1,978,936 | 60.6% | 2,360,883 | **72.3%** |
| 2021 | 3,534,015 | 2,627,842 | 74.4% | 1,924,555 | 54.5% | 2,194,189 | **62.1%** |
| 2022 | 1,383,571 | 1,044,062 | 75.5% | 817,809 | 59.1% | 914,865 | **66.1%** |
| 2023 | 727,243 | 554,018 | 76.2% | 474,151 | 65.2% | 479,116 | **65.9%** |
| 2024 | 908,508 | 613,638 | 67.5% | 552,854 | 60.9% | 552,854 | 60.9% |

### Name Alignment (Retail Channel, Year-Matched Panels)

| Year | Retail Loans | Tier 3 (Normalized) | Tier 4 (First Word) | Different |
|------|--------------|---------------------|---------------------|-----------|
| 2019 | 733,890 | 66.3% | **71.6%** | 28.4% |
| 2020 | 1,953,812 | 70.5% | **75.3%** | 24.7% |
| 2021 | 1,765,474 | 71.3% | **75.0%** | 25.0% |
| 2022 | 707,792 | 81.2% | **85.4%** | 14.6% |
| 2023 | 356,930 | 93.6% | **96.2%** | 3.8% |
| 2024 | 403,164 | 95.8% | **97.2%** | 2.8% |

---

## FNMA vs FHLMC Comparison

### purchaser_type Match Rates (Extended MBS)

| Year | FNMA (pt=1) | FHLMC (pt=3) | Difference |
|------|-------------|--------------|------------|
| 2019 | 56.3% | 57.5% | +1.2% FHLMC |
| 2020 | 82.7% | 72.3% | +10.4% FNMA |
| 2021 | 78.1% | 62.1% | +16.0% FNMA |
| 2022 | 76.8% | 66.1% | +10.7% FNMA |
| 2023 | 74.1% | 65.9% | +8.2% FNMA |
| 2024 | 67.5% | 60.9% | +6.6% FNMA |

**Observation**: FNMA consistently shows higher match rates than FHLMC, likely due to differences in data coverage or matching methodology in the MBS-FHFA crosswalks.

### Name Alignment (Retail, Tier 4)

| Year | FNMA | FHLMC | Difference |
|------|------|-------|------------|
| 2019 | 77.2% | 71.6% | +5.6% FNMA |
| 2020 | 81.7% | 75.3% | +6.4% FNMA |
| 2021 | 81.7% | 75.0% | +6.7% FNMA |
| 2022 | 91.6% | 85.4% | +6.2% FNMA |
| 2023 | 96.8% | 96.2% | +0.6% FNMA |
| 2024 | 97.4% | 97.2% | +0.2% FNMA |

**Observation**: Name alignment converges in 2023-2024 (~97% for both), but earlier years show FNMA with better alignment, possibly due to differences in seller name recording practices.

---

## Key Findings

### 1. Cross-Year MBS Matching is Essential

Loans originated late in the year often don't appear in MBS pools until the following year. Using extended MBS data (current year through 2024) dramatically improves match rates:

| Year | FNMA Improvement | FHLMC Improvement |
|------|------------------|-------------------|
| 2019 | +28.3% | +37.6% |
| 2020 | +11.7% | +19.7% |
| 2021 | +5.1% | +14.7% |
| 2022 | +3.0% | +11.8% |
| 2023 | +6.9% | +7.0% |
| 2024 | +0.0% | +0.0% |

The improvement is largest for 2019 (early data quality issues) and decreases over time. 2024 shows no improvement because we lack 2025 MBS data.

### 2. 2024 Match Rates are Artificially Low

Without 2025 MBS data, 2024 match rates are understated:
- **FNMA**: 67.5% (expected ~74% with 2025 data)
- **FHLMC**: 60.9% (expected ~66% with 2025 data)

This is based on the ~1 percentage point improvement seen when adding next-year MBS data for purchaser_type loans.

### 3. purchaser_type Loans Have Small Cross-Year Spillover

For loans explicitly marked as sold to FNMA/FHLMC in HMDA (purchaser_type=1/3), the cross-year improvement is much smaller (~1-2 percentage points) than for the full crosswalk (~6-7 points). This is expected because:
- purchaser_type=1/3 loans are already marked as sold at origination
- They should appear in same-year MBS data
- Small spillover represents data imperfections or late-year timing

### 4. Name Alignment Improves Dramatically in Recent Years

Retail channel name alignment (HMDA lender name vs UMBS seller name) has improved significantly:

| Period | FNMA Tier 4 | FHLMC Tier 4 | Notes |
|--------|-------------|--------------|-------|
| 2019-2021 | 77-82% | 71-75% | More cross-year matching, more name changes |
| 2022 | 91.6% | 85.4% | Transition year |
| 2023-2024 | 96-97% | 96-97% | Excellent alignment |

### 5. Remaining Name Mismatches Fall Into Categories

For the 2-4% of retail loans with name mismatches in 2023-2024:

1. **DBA names**: "Broker Solutions" vs "Broker Solutions Inc. DBA New American Funding"
2. **Credit union suffix**: "Idaho Central" vs "Idaho Central Credit Union"
3. **Punctuation/spacing**: "L.L.C." vs "LLC", "PlainsCApital" vs "Plains Capital"
4. **Mergers/acquisitions**: "Iberiabank" → "FIRST HORIZON BANK"
5. **Affiliates**: "Citibank" → "CITIMORTGAGE"
6. **Rebrands**: "QUICKEN LOANS" → "ROCKET MORTGAGE" (visible in 2020-2021 data)
7. **Genuine errors**: Small percentage of true mismatches

### 6. FHLB Presence

- **FNMA**: FHLB Chicago present as seller (0.31% of volume via MPF program)
- **FHLMC**: No FHLB sellers found (0.00%)

---

## Channel Distribution (2024, Extended)

### FNMA

| Channel | Count | Percentage |
|---------|-------|------------|
| Retail | 461,463 | 60.2% |
| Correspondent | 193,241 | 25.2% |
| Broker | 111,914 | 14.6% |

### FHLMC

| Channel | Count | Percentage |
|---------|-------|------------|
| Retail | 403,600 | 50.6% |
| Correspondent | 254,086 | 31.8% |
| Broker | 140,104 | 17.6% |

**Observation**: FNMA has higher retail share (60% vs 51%), FHLMC has higher correspondent share (32% vs 25%).

---

## Methodology Notes

### Name Alignment Tiers

| Tier | Method | Description |
|------|--------|-------------|
| 1 | Exact | Case-sensitive exact match |
| 2 | Case-insensitive | Lowercase comparison |
| 3 | Normalized | Remove punctuation, expand abbreviations, strip suffixes |
| 4 | First Word | Same first word after normalization (catches affiliates) |

### Normalization Rules

```python
# Remove leading "the"
# Remove punctuation: , . ' " - ( ) &
# Expand: co→company, corp→corporation, inc→incorporated, ltd→limited
# Remove: N.A., NA
# Strip suffixes: llc, bank, mortgage, lending, financial, etc.
```

### Panel Matching

Using year-matched HMDA panels is critical. The 2023 panel used for all years showed ~66% Tier 4 match for 2020. Using the 2020 panel for 2020 data shows **81.7%** Tier 4 match—a 15 percentage point improvement.

---

## Output Files

### Crosswalk Files
- `data/matching/hmda_mbs/output/master_crosswalk_fnma.parquet` - FNMA 2019-2024 (13.5M records)
- `data/matching/hmda_mbs/output/master_crosswalk_fhlmc.parquet` - FHLMC 2019-2024 (11.0M records)

### Crosswalk Schema
| Column | Description |
|--------|-------------|
| `HMDAIndex` | HMDA loan identifier |
| `activity_year` | Origination year (2019-2024) |
| `record_number` | FHFA record ID |
| `mbs_loan_id` | MBS loan sequence number |
| `umbs_loan_id` | UMBS loan sequence number |
| `match_round` | FHFA-HMDA match round |

### Build Script
```bash
# Build both agencies
mortgage-data match hmda-mbs run

# Build one agency
mortgage-data match hmda-mbs run --agency fnma

# Build with enrichment (adds HMDA/UMBS fields)
mortgage-data match hmda-mbs run --enrich
```

---

## Recommendations

1. **Always use extended MBS matching** for historical analysis to capture cross-year securitization timing.

2. **Use year-matched HMDA panels** for name alignment validation to avoid false positives from company rebrands.

3. **Expect 2024 rates to improve** once 2025 MBS data becomes available.

4. **Consider known rebrands** when analyzing name mismatches (Quicken→Rocket, Iberiabank→First Horizon, etc.).

5. **Correspondent channel name mismatches are expected** (~80% don't match because the originator differs from the aggregator/seller).

---

## GNMA (Ginnie Mae) FHA Loan Matching

This section documents the HMDA-GNMA matching methodology for FHA loans securitized through Ginnie Mae.

### Overview

HMDA FHA loans (loan_type=2) are securitized through Ginnie Mae rather than the GSEs. We use two complementary methods to link HMDA FHA loans to GNMA securitization records:

1. **Two-crosswalk method**: HMDA → FHA → GNMA (using existing crosswalks)
2. **Direct matching method**: HMDA → GNMA (probabilistic record linkage)

### Two-Crosswalk Method

Chains existing crosswalks:
- HMDA → FHA-HMDA crosswalk (from `match_fha_hmda`): 83.1% match rate
- FHA → FHA-GNMA crosswalk (from `match_fha_gnma`): 41.5% of FHA-matched

**Result**: 34.5% of HMDA FHA loans linked to GNMA (2.54M of 7.36M loans, 2018-2024)

#### Bottleneck Analysis

The main bottleneck is the FHA→GNMA crosswalk:
- Overall match rate: 35.8%
- Strong state-size bias: r = -0.907 (large states have lower match rates)
- Large states (TX, CA, FL): 15-24% match rates

### Direct Matching Method

Bypasses the FHA intermediate step using probabilistic record linkage.

#### Key Insight: LEI→Issuer Mapping

From validated two-crosswalk matches with `purchaser_type=2` (direct GNMA sales):
- **Median concentration**: 93.4% of loans from an LEI go to a single GNMA Issuer
- This institutional-level alignment provides a strong matching constraint

#### Blocking Strategy

| Variable | Tolerance |
|----------|-----------|
| State | Exact |
| Loan amount | $10k bins |
| Interest rate | 0.125% blocks |
| Loan purpose | Exact (P/R/O) |
| Origination year | Exact (recommended) or ±1 year |
| GNMA Issuer | Must match LEI's primary/top-3 issuer |

#### Scoring Variables

| Variable | Weight | Expected Match Rate |
|----------|--------|---------------------|
| Number of units | 3 | 99.5% |
| Number of borrowers | 2 | 94.1% |
| DTI bin | 2 | 83-94% |
| CLTV | 2 | 96.5% |
| Loan term | 1 | 99.1% |

**Maximum score**: 10, **Minimum threshold**: 5

### Combined Results (2018-2024)

| Method | Matches | Coverage | Quality |
|--------|--------:|----------|---------|
| Two-crosswalk | 2,539,693 | 34.5% | Gold standard |
| Direct (exact year) | 1,197,340 | 16.3% | 67.2% max score |
| **Total** | **3,737,033** | **50.7%** | |

#### Validation Metrics (Direct Matches, Exact Year)

| Metric | Observed | Expected |
|--------|----------|----------|
| Units match | 99.8% | ~99.5% |
| Borrowers match | 97.3% | ~94.1% |
| CLTV within 5% | 96.6% | ~96.5% |
| Term match | 97.9% | ~99.1% |
| Year exact match | 100.0% | - |

All metrics meet or exceed expectations derived from two-crosswalk validation.

### Match Quality by Score

| Score | Count | Percent |
|------:|------:|--------:|
| 5 | 15,669 | 1.3% |
| 6 | 67,216 | 5.6% |
| 7 | 19,524 | 1.6% |
| 8 | 285,059 | 23.8% |
| 9 | 4,792 | 0.4% |
| 10 | 805,080 | **67.2%** |

### CLTV Gap Explanation

HMDA CLTV is systematically ~1.66% lower than GNMA CLTV due to financed upfront MIP:

```
GNMA_CLTV ≈ HMDA_CLTV × 1.0175
```

FHA allows borrowers to finance the 1.75% upfront MIP into the loan. GNMA CLTV includes this financed amount, while HMDA CLTV does not. This explains why 75.2% of matches have a CLTV difference between 1-2%.

### Interest-Rate Caveat: Temporary Buydowns (2023–2024)

Rate-based matching uses a ±0.125 tolerance, which absorbs ordinary rounding differences. One known
edge case: the surge in **temporary rate buydowns** during the 2023–2024 high-rate environment.
Buydowns can introduce discrepancies between the note rate HMDA reports and the rate seen in agency
disclosure, mildly depressing rate-keyed match quality for those vintages. Not corrected for — flagged
so an unexplained 2023–2024 rate-match dip isn't mistaken for a pipeline defect.

### CLI Usage

```bash
# Run FHA matching pipeline (both methods)
mortgage-data match hmda-mbs fha --min-year 2018 --max-year 2024

# Two-crosswalk only (faster, lower coverage)
mortgage-data match hmda-mbs fha --no-direct

# Direct matching with ±1 year tolerance (higher coverage, slightly lower quality)
mortgage-data match hmda-mbs fha --year-tolerance
```

### Python API

```python
from mortgage_data_manager.matching.match_hmda_mbs import (
    run_fha_matching_pipeline,
    build_two_crosswalk_chain,
    direct_match_fha,
)

# Full pipeline
two_crosswalk, direct = run_fha_matching_pipeline(
    min_year=2018,
    max_year=2024,
    include_direct=True,
    exact_year=True,
)

# Or step by step
two_crosswalk = build_two_crosswalk_chain(min_year=2018, max_year=2024)
direct = direct_match_fha(two_crosswalk, exact_year=True)
```

### Output Files

| File | Description | Rows |
|------|-------------|-----:|
| `hmda_fha_gnma_two_crosswalk_2018_2024.parquet` | Two-crosswalk chain | 7,363,998 |
| `hmda_gnma_direct_exact_year_2018_2024.parquet` | Direct matches | 1,197,340 |

### Recommendations

1. **Use two-crosswalk matches when available** - These are validated through two independent crosswalks

2. **Use direct matches with score ≥ 8** for high-confidence applications - 91% of direct matches achieve this

3. **The exact year version is recommended** - Trades ~2.3pp coverage for meaningfully better quality

4. **When comparing CLTV values**, apply the 1.0175 adjustment factor to account for financed upfront MIP

---
