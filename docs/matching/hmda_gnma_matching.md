# HMDA-GNMA Matching

The `match_hmda_gnma` workflow is the Ginnie Mae edge of the spine. It links HMDA government
originations to GNMA loan-level disclosure records and, with the FNMA and FHLMC crosswalks of
[`match_hmda_gse`](hmda_gse_matching.md), completes the HMDA↔MBS loan-level linkage across all
three agencies. Both populations are matched directly, HMDA loan to GNMA loan:

- **FHA** (HMDA `loan_type` 2, GNMA Agency F): a scored direct match, each HMDA loan
  carrying its FHA record through the `hmda_fha` crosswalk.
- **VA and USDA** (`loan_type` 3 and 4, GNMA Agency V and R): a scored direct match only, since
  no administrative bridge exists for these programs.

## Data sources

- `hmda_fha` crosswalk (HMDAIndex ↔ FHA_Index, 1:1, the post-2018 era file).
- HMDA post-2018 originations (`action_taken` 1). The FHA side is read at the release the
  `hmda_fha` crosswalk's `HMDAIndex` prefixes name (`hmda.load.scan_hmda_for_index`); the
  VA/USDA side reads each year's best available release, without reverse mortgages and
  with at most four units.
- FHA single-family endorsements: exact Mortgage Amount, Interest Rate, endorsement year and
  month.
- GNMA `dailyllmni/L` new-issuance files from pool month 2018-01 on, Loan Purpose 1 or 2
  only, one row per Disclosure Sequence Number (identical copies collapse; a number whose
  rows disagree is excluded): Agency F for FHA, V and R for VA and USDA.
- For the VA/USDA identity score: issuer names from GNMA `issrinfo` and institution names
  from the HMDA panel.
- For validation only: the `hmda_sellers_purchasers` crosswalk.

## FHA method

Targets are all FHA-matched HMDA loans, enriched with their FHA record. The FHA record
supplies the exact dollar amount, a hard key because GNMA's disclosed original balance is
that amount truncated to the $1,000 floor, and the endorsement month; HMDA supplies the
exact rate and term. Candidates are GNMA FHA loans with Loan Purpose 1 or 2 only (3 and 4
are modifications, 5 re-performing loans: re-pooled, never an HMDA origination).

| Round | Block keys | Score needed in round |
|---|---|---|
| 1 | state × exact amount ($1k floor) × exact rate (3 decimals; HMDA rate, FHA fallback) × purpose category × term | 3 |
| 2 | as round 1, rate in 0.125% buckets, no term | 9 |

The rounds are `ROUNDS_FHA` in `match_hmda_gnma/rounds.py`; a copied entry with changed
fields runs as a further round, and `match hmda-gnma info` prints them. Both rounds require
endorsement − origination month in [−1, +3] (`month_window`, a cross-year month index; the
distribution peaks at +1, the endorsement lag). Candidates are scored on fields outside the
block key, maximum 15:

| Component | Points | Rule |
|---|---|---|
| Units | 3 | exact |
| Borrowers | 2 | agree (HMDA count from `co_applicant_sex`, capped at 2) |
| MIP-aware CLTV | 4 / 3 / 1 | `min(\|g−h\|, \|g−h×1.0175\|)` ≤ 0.25 / 1 / 5 points |
| Month | 1 | difference in [0, 1] |
| LEI↔Issuer dyad | 2 | pair seen at least 5 times among first-pass matches |
| DTI, one-sided | 3 | GNMA DTI inside the HMDA bucket; `[h, h+1)` in the 36–49 exact region |

The match runs twice: the first pass, without the dyad term, supplies the pairs the dyad
map is learned from. The CLTV and DTI components rest on two facts verified on 2.3M
high-confidence pairs: GNMA CLTV includes the financed upfront MIP (HMDA base CLTV × 1.0175
for 77.7% of loans), and HMDA's integer DTI is a floor (87% of GNMA − HMDA differences lie in
[0, 1)).

A pair is accepted only as the strictly unique best score on both sides, at the round's
score; ties are dropped. An accepted pair claims both loans, so later rounds never see them,
even if the pair then falls short of admission. The crosswalk admits a pair at
`total_score` ≥ 11 (`FHA_MIN_SCORE`, env `HMDA_GNMA_MIN_SCORE`; 14 is the strict tier), or
on absent evidence. Streamline refinances carry no DTI and often no usable CLTV, so they top
out at 8 points however exact the match; such a pair enters when units, borrowers, month and
dyad all score in full and neither CLTV nor DTI disagrees (each is scored or missing).
`admission` records the route.

**No FHA→GNMA leg.** The edge does not chain HMDA → FHA → GNMA through a separate FHA↔GNMA
match: a chain reaches the same GNMA loan as the direct match for nearly every pair, and
where the two disagree the chain's pick is usually wrong.

## VA and USDA method

Every round blocks on agency (VA or USDA) × state × $10k HMDA amount bin (GNMA's
$1k-floored balance mapped to its bin, with bin-edge balances in both) × exact origination
year, plus:

| Round | Further block keys | Cells searched | Score needed in round |
|---|---|---|---|
| 1 | exact rate (3 decimals) × term | all | 3 |
| 2 | rate in 0.125% buckets | all | 9 |
| 3 | exact rate × term × purpose category × units × borrowers | only those round 1 dropped at the cap | `VA_USDA_CAPPED_MIN_SCORE` (13) |

The rounds are `ROUNDS_VA_USDA` in `rounds.py`. Each round sees only the rows earlier rounds
left unclaimed; as for FHA, a pair accepted inside a round claims both loans even if it then
falls below the crosswalk floor. Key cells with more than `CELL_CAP` (400, env
`HMDA_GNMA_CELL_CAP`) candidate pairs cannot resolve 1:1 and are dropped before the join;
round 3 exists to reopen them.

The score (maximum 19) uses the FHA components except month, which needs the FHA
endorsement, and adds two more; the CLTV component is fee-aware instead of MIP-aware:

- **Fee-aware CLTV** over the empirical factor set {1, 1.005, 1.01, 1.0215, 1.023, 1.033,
  1.036}. Unlike FHA, VA ratios are exactly 1.000 for 77% of true pairs; a financed funding
  fee is the minority case.
- **LEI↔Issuer dyad**, learned from the FHA crosswalk's pairs plus strict seed pairs (key
  cells holding exactly one HMDA row and one GNMA loan, DTI-consistent), at least 5 per pair.
- **Retail identity by purchaser type.** LEI↔issuer name alignment is near-deterministic for
  retail purchaser types (type 2 about 96%, type 0 80% and 59%) and absent for sold types, so
  alignment scores +2 and non-alignment −2 (type 2), −1 (type 0) or 0 otherwise.
- **Purpose evidence.** Ginnie publishes Loan Purpose (1 purchase, 2 refinance) and Refinance
  Type (1 not streamlined and not cash-out, 2 cash-out, 3 streamlined), mapped to HMDA 1, 31
  and 32; HMDA 31 covers both non-streamlined rate/term and streamlines, so refinance types 1
  and 3 share a category. Purchase versus refinance scores ±2; cash-out versus not, within
  refinance pairs, ±1.

The purpose term was calibrated on strictly 1×1 key cells, where one HMDA row and one GNMA
loan share the cell, so the pairing uses no score component and every scored field is held
out. The coarse leg agrees for 99.75% of VA and 99.92% of USDA pairs there, against 62.9% and
93.0% on a +$50k amount-shift placebo (lift 1.59× and 1.07×). The fine leg is scored for VA
only: 98.61% against 69.97% on the placebo (1.41×), while USDA's lift is 1.01×, because 98%
of USDA volume is purchases and its roughly 2,000 testable refinances carry no signal.
Against a rerun without the term (measured 2026-10-22), the 5,804 pairs it removes
triangulate at 36% and agree on held-out purpose 23% of the time, against 95.6% and 98.5%
for all pairs of that run. `VA_USDA_MIN_SCORE` (env `HMDA_GNMA_VA_USDA_MIN_SCORE`) is
9 rather than 7, so the purpose term's points do not lower the bar the other components
must clear.

**Round 3: the capped cells.** Of the 6,607,007 HMDA rows with a rate and a term, 3,549,016
(53.7%) sit in 65,430 round-1 cells over `CELL_CAP`, where one cell can hold 1.6M candidate
pairs. 95.3% of those rows are VA, and the 2020–21 VA IRRRL waves alone account for 1.87M.
Round 3 reopens exactly those cells with a finer key: purpose category × units × borrower
count. These are the only published fields both sides carry that discriminate. Ginnie's
loan-level `msa` and `seller_issuer_id` are blank in every vintage from 2013 to 2026, and
HMDA has no counterpart to Ginnie's FICO, first-time-buyer or down-payment-assistance fields.
The three keys agree on 98.55% of 1×1-cell seeds against 33.32% on the placebo (lift 2.96×)
and bring 50.6% of the capped rows back under the cap, at a hard-key recall cost of about
1.45%.

The round sees only HMDA rows and GNMA loans that rounds 1–2 never claimed, in cells they
never joined, so it cannot move or lose an existing pair. Its keys are also score components,
so `s_units = 3` and `s_borr = 2` are granted to every candidate, and it has its own floor:
`VA_USDA_CAPPED_MIN_SCORE` (env `HMDA_GNMA_VA_USDA_CAPPED_MIN_SCORE`) defaults to 13, a floor
of 8 on evidence not already in its key. Its triangulation is under Validation.

## Output

| File | Rows | Columns |
|---|---|---|
| `crosswalk/hmda_gnma/hmda_gnma_crosswalk_fha.parquet` | one per matched HMDA FHA loan | `HMDAIndex`, `FHA_Index`, `gnma_loan_id`; `match_round`, `total_score`, `month_diff`, `s_units`, `s_borr`, `s_cltv`, `s_month`, `s_dyad`, `s_dti`; `cltv_absent`, `dti_absent`; `admission` (`score` or `absent_evidence`) |
| `crosswalk/hmda_gnma/hmda_gnma_crosswalk_va_usda.parquet` | one per matched VA or USDA loan | `HMDAIndex`, `gnma_loan_id`, `agency` (`V` or `R`), `issuer_id`, `activity_year`; `match_round`, `total_score`, `s_identity`, `s_units`, `s_borr`, `s_cltv`, `s_dti`, `s_dyad`, `s_purpose` |

Both files are 1:1 in `HMDAIndex` and `gnma_loan_id`. A VA/USDA score of 12 is a
medium-confidence pocket.

```bash
mortgage-data match hmda-gnma info
mortgage-data match hmda-gnma run [--loan-type fha|va-usda|all] [--min-year] [--max-year] [--min-score]
mortgage-data match hmda-gnma run --loan-type fha [--output-dir]
mortgage-data match hmda-gnma run --loan-type va-usda [--capped-min-score]
mortgage-data match hmda-gnma validate [--loan-type fha|va-usda|all] [--by COLUMN] [--crosswalk PATH]
```

`--loan-type` defaults to `all`. `validate` builds its sellers/purchasers → LEI pair table on
first use; `--overwrite` rebuilds it after a sellers/purchasers or HMDA rebuild.

## Results

HMDA activity years 2018–2025.

**FHA**, over 8,277,066 HMDA FHA originations (`loan_type` 2, `action_taken` 1, on the
releases the `hmda_fha` crosswalk names):

| | Pairs | HMDA-side rate |
|---|---:|---:|
| Round 1 | 6,353,270 | 76.8% |
| Round 2 | 160,248 | 1.9% |
| Score ≥ 11 | 6,079,467 | 73.4% |
| Absent evidence | 434,051 | 5.2% |
| Total | 6,513,518 | 78.7% |

The absent-evidence route is almost all round 1 (433,422 of 434,051 pairs).

**VA and USDA**, with the purpose term and round 3. The last column reruns the same code
with neither, at a floor of 7 (measured 2026-10-22):

| | HMDA universe | Matched | HMDA-side rate | Without round 3 and the purpose term |
|---|---:|---:|---:|---|
| VA | 6,023,662 | 3,451,360 | 57.3% | 2,297,002 (38.1%) |
| USDA | 626,055 | 433,609 | 69.3% | 374,849 (59.9%) |
| Total | 6,649,717 | 3,884,969 | 58.4% | 2,671,851 (40.2%) |

By round: 2,613,245 in round 1, 96,038 in round 2 and 1,175,686 in round 3. Round 3 is most
of the gain; the purpose term adds 37,432 pairs net on rounds 1–2.

**GNMA side**, against the loan-level disclosure universe each program contributes in pools
from January 2018 on (including 2026 pools): FHA 75.5% (6,513,518 of 8,627,330), VA 54.6%
(3,451,360 of 6,322,574), USDA 65.9% (433,609 of 657,593). These sit below the HMDA-side
rates because Ginnie pools loans HMDA never reports as 2018–2025 originations (re-pooled,
non-reporter and 2026 volume), so the two denominators are different populations.

## Validation

DTI is a direct-match score input, so it cannot validate direct pairs. The validator
(`match hmda-gnma validate`) instead uses a field no matcher keys on: for loans also matched
seller → purchaser (the `hmda_sellers_purchasers` crosswalk), the GNMA pool issuer name
should equal the purchaser's (aggregator's, `lei_p`) HMDA panel name, and the originator's
name serves as a control. Measured 2026-10-22.

| FHA set | Testable pairs | Issuer = aggregator | Issuer = originator (control) |
|---|---:|---:|---:|
| All pairs | 2,024,564 | 95.3% | 0.8% |
| Score ≥ 11 | 2,006,466 | 95.3% | 0.8% |
| Absent evidence | 18,098 | 95.1% | 1.4% |

The absent-evidence tier is thinly testable: 18,098 of its 434,051 pairs carry a purchase
record, against a third of the score tier, because most are retail refinances.

| VA/USDA set | Testable pairs | Issuer = aggregator | Issuer = originator (control) |
|---|---:|---:|---:|
| All pairs | 986,419 | 95.4% | 1.0% |
| Round 1 | 659,594 | 96.0% | 0.9% |
| Round 2 | 49,708 | 92.1% | 0.2% |
| Round 3 | 277,117 | 94.8% | 1.4% |
| VA | 801,370 | 95.1% | 1.2% |
| USDA | 185,049 | 96.7% | 0.4% |

## Limitations

1. **State-size bias.** Spearman correlation of a state's HMDA volume with its HMDA-side
   match rate is −0.04 for FHA (p = 0.79, 55 state codes) and −0.84 for VA (measured
   2026-10-22). Weight or use state fixed effects for cross-state VA analysis.
2. **Selection.** Matched FHA loans under-represent Hispanic borrowers. Among applicants
   reporting an ethnicity on forward FHA originations, the Hispanic share is 6.9 points lower
   for matched than unmatched loans within activity year, and 7.1 within year and purpose
   (purchases 7.8, refinances 5.0); the gap widens to 10–11 points in 2024–2025. Purchases
   match 5–13 points less often than refinances in 2018 and 2022–2025, and 1–2 points more
   often in 2019–2021 (measured 2026-10-21). The direct route also mildly favors
   DTI-consistent loans (some issuers re-report DTI at pooling); `s_dti` is persisted so
   DTI-agnostic subsets can be rebuilt.
3. **DTI is not match evidence**, because it is a match input; use the name triangulation
   above.
4. **New HMDA years.** A new year needs the `hmda_fha` crosswalk through that vintage
   first.
5. **VA/USDA before 2022 is collision-bound.** 2,862,869 of the 3,549,016 HMDA rows in
   over-cap cells are from 2018–2021, 1.87M of them in the 2020–21 VA IRRRL waves. Round 3
   brings 50.6% of all capped rows back under the cap and recovers 1,175,686 pairs; the rest
   stay unresolvable with no further sub-block available (see Round 3), and round 3's hard
   keys exclude about 1.45% of true pairs by construction.
6. **Temporary buydowns in 2023–2024.** The buydown surge can put the note rate HMDA reports
   at odds with the rate in agency disclosure, beyond what the ±0.125 tolerance absorbs. This
   is not corrected, so a 2023–2024 dip in rate-keyed quality is not a pipeline defect.
