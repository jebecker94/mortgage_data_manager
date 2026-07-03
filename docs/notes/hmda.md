# HMDA Notes

## Data Quality Issues

Systematic LEI-level reporting errors identified in HMDA post-2018 bronze data (2018–2024). Analysis covered 62.1M originated loans across 6,388 LEIs. Bronze data was used for detection because it preserves CFPB's original values before any pipeline transformations.

File types (a=Three-Year, b=One-Year, c=Snapshot) contain identical loan records per LEI-year. Values 1111, 8888, 9999 are exemption sentinels — replace with NULL, not cataloged as errors.

**Full LEI-level catalog**: [`hmda_reporting_errors.md`](hmda_reporting_errors.md) — 1,075 LEI-year-file_type records, verified independently per file type. Some lenders corrected errors between filings (e.g., Three-Year file fixed but Snapshot still wrong), so the error list varies by file type.

### Income

Expected unit: thousands of dollars (e.g., 86 = $86,000).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Monthly instead of annual | Lender's system stores monthly income; filed without annualizing | income × 12 | 10 |
| Mixed monthly/annual in same filing | ~80–90% of a lender's loans have monthly income, rest are correct; likely different internal pipelines or branch-level conventions | income × 12 where income < threshold | 3 |
| Raw dollars instead of thousands | Filed actual dollar income (e.g., 86000) instead of converting to thousands | income ÷ 1000 | 8 |
| Unreliable (median ~1, ×12 doesn't align) | Unknown — possibly placeholder values | → NULL | 3 |

### Interest Rate

Expected unit: percentage (e.g., 4.5 = 4.5%).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Decimal instead of percentage | Filed as proportion (e.g., 0.045) instead of percentage (4.5) | interest_rate × 100 | 12 |
| Placeholder/sentinel (1111, 5250) | System default or "not applicable" code used instead of leaving blank | → NULL | 12 |

### Combined Loan-to-Value Ratio

Expected unit: percentage (e.g., 80 = 80%).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Decimal instead of percentage | Filed as proportion (e.g., 0.80) instead of percentage (80); same misunderstanding as interest rate — some LEIs make this error in both fields | CLTV × 100 | 22 |
| Tenths instead of percentage | Filed as value out of 10 (e.g., 8.0) instead of out of 100 (80); possibly a UI or spec misread | CLTV × 10 | 9 |
| Placeholder/sentinel (1111, 8888) | System default code used instead of leaving blank | → NULL | 4 |

### Rate Spread

Expected unit: percentage points above APOR (e.g., 1.5 = 1.5 pp).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Decimal instead of pp | Filed as proportion (e.g., 0.015) instead of pp (1.5); confirmed by checking that ×100 aligns with aggregate (0.3–3.0× year median) — most lenders with median near zero are legitimate, not errors | rate_spread × 100 | 92 |
| Basis points instead of pp | Filed as bp (e.g., 215) instead of pp (2.15); one LEI (549300GNIV169ZIHU012) across 3 years | rate_spread ÷ 100 | 3 |

### Discount Points

Expected unit: dollars (e.g., $1,500).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Percentage points instead of dollars | Filed as points (e.g., 1.25 = 1.25% of loan amount) instead of dollar amount; lender's system tracks points and didn't convert | discount_points × loan_amount ÷ 100 | 10 |

Note: 66 additional LEI-years may be reporting in basis points (values 50–430 with fee/loan ratios 5–10× below normal). These require case-by-case validation and are cataloged in the full error table but not counted here as confirmed.

### Lender Credits

Expected unit: dollars (year median $296–$550).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Percentage points instead of dollars | Filed as points (e.g., 0.50 = 0.50% of loan amount) instead of dollar amount; values < $3 with clustering at quarter-point increments | lender_credits × loan_amount ÷ 100 | 49 |
| Likely in hundreds instead of dollars | Values $3–$15 where ×100 aligns with year median; possibly a system that stores in hundreds or truncates trailing zeros | lender_credits × 100 | 163 |

### Origination Charges

Expected unit: dollars.

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Percentage points instead of dollars | Same as discount points — system tracks as % of loan, not dollars | origination_charges × loan_amount ÷ 100 | 4 |

### Property Value

Expected unit: dollars, rounded to nearest $5,000.

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Placeholder $5,000 | Filed minimum reportable value ($5K) for loans with much larger amounts (implied LTV 700–6100%); data system default for "unknown" | → NULL | 35 |

### Loan Term

Expected unit: months (e.g., 360 = 30 years).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| All terms = 1 month | Every loan for the LEI-year has term = 1; likely a system default or placeholder rather than actual 1-month loans | → NULL | 11 |

### Prepayment Penalty Term

Expected unit: months (aggregate median = 36).

| Issue | Theory | Fix | Count |
|-------|--------|-----|-------|
| Years instead of months | Filed as years (e.g., 3) instead of months (36); all values for the LEI-year are small integers whose ×12 gives standard terms (24, 36, 72) | prepayment_penalty_term × 12 | 22 |

### Variables with No Systematic Errors Detected

- **loan_amount**: 100% of values are multiples of $5,000. No scale errors found. High-value LEIs (median > $10M) are legitimate multifamily/commercial lenders with normal LTVs.
- **intro_rate_period**: Legitimate range is 1–120 months; year median is 1–6 months. Individual record outliers exist but no systematic LEI-level errors.

---

### References

**Analysis scripts**: `investigations/scripts/investigation_hmda_lei_reporting_errors_*.py`

**Detection method**: LEI-level median analysis on bronze data (raw string values, closest to CFPB source). Rule-based detection for known error patterns, validated by checking whether the proposed correction aligns the LEI's distribution with the year aggregate (ratio test). 62.1M originated loans, 6,388 LEIs, 28,476 LEI-year groups with ≥25 loans.
