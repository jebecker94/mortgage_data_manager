# CRT ↔ MBS Matching

This workflow links GSE credit-risk-transfer (CRT) reference-pool disclosures to the
single-family loan-level datasets, with one sub-workflow per GSE:

- **FHLMC (STACR/ACIS).** Freddie's CRT disclosure identifies loans only by deal-scoped
  Loan Identifiers (`19DNA1000001`), so each loan is matched to an SF `Loan Sequence Number`
  on at-origination characteristics and confirmed against the monthly performance panel.
- **FNMA (CAS/CIRT).** Deal membership is extracted directly from the `Reference Pool ID`
  and `Deal Name` fields of the CRT historical downloads. The CRT loan id is its own
  namespace, not the LPPUB `Loan Identifier`, so the extraction gives CRT-internal
  membership only; linking to the SF dataset needs a characteristics matcher like
  Freddie's.

## Data sources

| Side | Dataset | Location |
|------|---------|----------|
| FHLMC CRT | At-issuance Reference Pool disclosure (150 deals, 15.0M loans, 2013–2026) | `fhlmc/silver/crt/` |
| FHLMC SF | SF loan-level origination silver (vintages 1999–2026) | `fhlmc/silver/origination/` |
| FHLMC SF | SF loan-level performance silver (loan × month; vintages 1999+) | `fhlmc/silver/performance/` |
| FNMA SF | SF Loan Performance bronze quarterlies (2016Q1+) | `fnma/bronze/` |

## Method (FHLMC)

For each deal, a cascade of exact joins runs from strict to loose against the deal's SF
origination pool, windowed to the deal's first-payment-date range (vintage partitions ±1
year). A loan pins when the join is 1:1 in both directions; SF loans claimed at an earlier
round leave the pool before the next round runs. The rounds are `ROUNDS_FHLMC` in
`match_crt_mbs/rounds.py`: a copied entry with changed keys runs as a further round, and
`match crt-mbs info` prints them.

| Round | Keys |
|------|------|
| 1 | FPD, maturity, state, ZIP3, orig UPB, rate (×1000), FICO, LTV, CLTV, DTI, purpose, property type, occupancy, units, FTHB |
| 2 | FPD, maturity, state, ZIP3, orig UPB, rate, FICO, LTV, CLTV |
| 3 | FPD, state, ZIP3, orig UPB, rate, FICO, LTV |

On the CRT side, occupancy `O` is recoded to `P` (early deals code owner-occupied as `O`),
rate is compared at 3 decimals through an integer mill cast, and UPB as integer dollars.

**Confirmation.** A pin is `upb_confirmed` when the SF performance panel has a month
within ±1 of the CRT `Period` whose `Current Actual UPB` equals the CRT `UPB at Issuance`
to the cent (tolerance $0.005). The cent-level UPB acts as a loan fingerprint; the
±1-month window absorbs a one-month offset between the CRT Period and the panel's
`Monthly Reporting Period`. A pin whose vintage has no performance silver gets
`upb_confirmed = null`: unverifiable, not failed. Performance silver covers every pinned
vintage, so every pin is checkable.

## Method (FNMA)

One streaming pass over the `CAS_*.zip` and `CIRT_*.zip` historical downloads collects
the distinct loan-deal pairs. Each download is one large pipe-delimited CSV in the
113-column LPPUB layout (839.8M CAS and 191.0M CIRT monthly rows). `Reference Pool ID` is
field 1 and `Deal Name` field 104. The deal name is populated from mid-2018; a (loan, pool)
key takes the one non-empty name its rows carry, which also covers the earlier months. A key
whose rows carry two different names gets a null `Deal Name` rather than one of the two.
With no CRT zips present, the extraction falls back to scanning the SF performance bronze
for populated deal fields, which are entirely null in the current LPPUB vintage.

**Separate id namespaces (verified 2026-10-22).** The CRT loan id is a CRT-specific
sequence, not the public LPPUB `Loan Identifier`. Both are dense sequential runs in
overlapping numeric ranges with no identity overlap: 111,491 extracted ids fall inside the
2024Q1 LPPUB id block and none match. The output column is therefore `CRT Loan
Identifier`. Linking Fannie CRT loans to the SF dataset needs a characteristics matcher
mirroring Freddie's (not built; the CRT panel carries the full LPPUB characteristic set to
match on).

## Output

`crosswalk/crt_mbs/crt_mbs_crosswalk_fhlmc.parquet`, one row per (deal, CRT loan):

| Column | Type | Notes |
|--------|------|-------|
| Reference Pool Number | str | Deal (e.g. `19DNA1`) |
| Loan Identifier | str | Deal-scoped CRT loan id |
| Loan Sequence Number | str | SF LLD key |
| Period | date | Reference-pool formation month |
| match_round | UInt8 | 1, 2 or 3 (see Method) |
| upb_confirmed | bool | True/False when the performance quarter is available; null otherwise |

`crosswalk/crt_mbs/crt_mbs_crosswalk_fnma.parquet`, one row per (loan, deal): `CRT Loan
Identifier` (CRT namespace; does not join to LPPUB), `Reference Pool ID`, `Deal Name` (null
where the key's rows name two deals), `program` (CAS/CIRT).

Per-deal diagnostics (loans, pins by round as `n_t1`, `n_t2`, …, confirmation counts) are written to
`data/matching/crt_mbs/output/crt_mbs_deal_summary_fhlmc.parquet`. `CRTMBSMatchingConfig` holds the
confirmation tolerances (`CONFIRM_MONTH_TOLERANCE = 1`, `CONFIRM_UPB_TOLERANCE = 0.005`).

```bash
mortgage-data match crt-mbs run --agency fhlmc                     # all deals, with confirmation
mortgage-data match crt-mbs run --agency fhlmc -d 19DNA1 -d 24DNA3 --no-confirm
mortgage-data match crt-mbs run --agency fnma                      # needs a populated LPPUB vintage
mortgage-data match crt-mbs check                                  # crosswalk health report
mortgage-data match crt-mbs info                                   # paths and rounds
```

## Results

### FHLMC (run 2026-10-21)

All 150 deals, against SF origination silver vintages 1999–2026:

| Metric | Value |
|--------|-------|
| CRT loans | 15,026,378 |
| Pinned 1:1 | 14,900,962 (99.17%) |
| Round 1 (15 keys) | 14,468,023 (97.1% of pairs) |
| Round 2 (9 keys) | 412,990 (2.8%) |
| Round 3 (7 keys) | 19,949 (0.1%) |
| UPB-confirmed | 13,437,135 (90.18%; every pin is checkable) |
| Failed confirmation | 1,463,827 |
| LSNs appearing in more than one deal | 1,301,569 |
| LSN duplicated within a deal | 0 |

Per-deal pin rates are 98.1–99.99% for every deal except `20AF01` (9.2%, see Limitations).
Failed confirmations concentrate in a few deal families:

| Family | Deals | Pairs | Failed confirmation |
|---|---|---|---|
| DNA | 46 | 5,954,689 | 7.2% |
| HQA | 34 | 3,315,034 | 2.8% |
| SP | 14 | 1,452,613 | 11.7% |
| FTR | 4 | 1,214,891 | 0.0% |
| DN | 7 | 775,033 | 71.4% |
| SPL | 15 | 612,216 | 0.5% |
| HQ | 5 | 441,426 | 15.0% |
| SPH | 13 | 425,162 | 0.2% |
| HRP | 4 | 419,774 | 0.0% |
| AF | 3 | 143,955 | 81.9% |
| AFH | 2 | 79,890 | 1.6% |
| COR | 1 | 35,789 | 0.9% |
| DI | 2 | 30,490 | 88.9% |

In the age-0 AF flow deals the Period predates the loans' first panel rows, so the ±1-month
window can miss structurally; modified or seasoned loans can carry deferred amounts in the
panel's `Current Actual UPB`. Why most DN and DI pins fail is not established (see
Limitations).

HRP relief-refinance deals pin at 99.3–99.7% against the standard silver: although relief
refinances are non-standard product, the loans these deals reference are in the standard SF
dataset.

### FNMA extraction (2026-10-22)

14,642,483 loan-deal pairs from one pass over the CRT downloads (`CAS_Oct13-Jun26.zip`,
`CIRT_Dec14-Jun26.zip`): CAS 10,935,165 pairs (89 pools, 95 deal names; the 2020 SBT
re-securitization pools carry composite names) and CIRT 3,707,318 (83 pools, 82 deal names,
one per named pool). No loan is in more than one pool.

291,545 keys (1.99% of 14,642,483) have a null `Deal Name` because their rows name two
deals. They make up four whole reference pools: CAS pools 5083, 5084 and 5103 (203,953 keys,
1.87% of CAS) and CIRT pool 5325 (87,592 keys, 2.36% of CIRT). SF linkage waits on the
characteristics matcher.

## Limitations

1. **Loans repeat across deals.** Seasoned SPL/SPH/SP/FTR pools re-reference flow-deal
   loans, so one loan can legitimately appear in several deals. The crosswalk grain is
   (deal, CRT loan), and such loans carry one row per deal, all resolving to the same LSN
   (1,301,569 LSNs appear in more than one deal).
2. **`20AF01` (ACIS flow) pins only 9.2%.** About 45% of its pool has no standard-dataset
   candidate even in round 3 (7 keys), and much of the rest is ambiguous. Open; the deal is
   0.7% of the CRT universe.
3. **Most DN and DI pins fail confirmation** (71% and 89%), against 3–7% in the later DNA
   and HQA flow deals. These are pins the exact joins made 1:1, so the failure is in the
   UPB check or the Period alignment for these deals, not established which.
4. **FNMA has no SF linkage yet.** The extraction gives CRT-internal membership only.
