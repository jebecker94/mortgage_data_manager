# Master Crosswalk (the HMDA spine)

The master crosswalk has one row per post-2018 HMDA loan that appears in at least one
matching edge, carrying that loan's ids in every dataset it could be linked to. It matches
nothing itself: it assembles the crosswalks the other `matching/` workflows produce, which
is why the module has no `match_` prefix. It answers "given this HMDA loan, what else do I
know about it?" in one join, instead of chaining six crosswalks by hand.

It holds ids, match rounds, coverage flags and provenance only; join to silver for loan
characteristics.

- Output: `crosswalk/master/`, hive-partitioned by `activity_year`.
- Grain: exactly one row per `HMDAIndex`, asserted by QC on every build.
- Size: 46,435,403 loans, activity years 2018–2025 (build of 2026-10-22).
- A link another method contradicts is withheld and recorded, never resolved by priority.

## Inputs

Every input is another workflow's shipped crosswalk; nothing is re-matched here.

| Edge | Source workflow | Artifact |
|---|---|---|
| `in_fha` | `match_hmda_fha` | `hmda_fha/hmda_fha_crosswalk_post2018.parquet` |
| `in_fhfa` | `match_hmda_fhfa` | `hmda_fhfa/hmda_fhfa_crosswalk_post2018.parquet` |
| `in_mbs` / `in_umbs` | `match_hmda_gse` | `hmda_gse/hmda_gse_crosswalk_chain_{fnma,fhlmc}.parquet` ∪ `hmda_gse/hmda_gse_crosswalk_direct_{year}.parquet` |
| `in_gnma` | `match_hmda_gnma` | `hmda_gnma/hmda_gnma_crosswalk_fha.parquet` + `hmda_gnma_crosswalk_va_usda.parquet` (+ `fhlb_mbs/fhlb_mbs_crosswalk_gnma.parquet` as fallback) |
| `in_fhlb` | `match_hmda_fhlb` | `hmda_fhlb/hmda_fhlb_crosswalk_post2018.parquet` |
| `in_seller_purchaser` | `match_hmda_sellers_purchasers` | `hmda_sellers_purchasers/hmda_sellers_purchasers_crosswalk_post2018/` |

`mbs_umbs/mbs_umbs_crosswalk_{fnma,fhlmc}.parquet` fills `umbs_loan_id` for direct-sourced
rows (the chain crosswalks already carry both ids). The pre-2018 HMDA-FHA crosswalk
(`hmda_fha/hmda_fha_crosswalk_pre2018.parquet`) is not an edge, but its FHA side feeds the
`fha_cross_era` conflict (see *Conflict policy*). HMDA silver is the one non-crosswalk
input: each purchase record's own `purchaser_type` orders successive owners (see
*Ownership chain*).

There is no FHLB edge through the GSEs: FHLB loans sold to Fannie Mae and those held as
AMA are disjoint ([fhlb_mbs](fhlb_mbs_matching.md#no-fannie-mae-side)). FHLB reaches the
spine only through `hmda_fhlb`.

## Assembly

Each edge is collapsed to one row per `HMDAIndex`, its lowest match round. If a loan's
best-round rows still name different targets, the build stops rather than pick one; the
GSE chain crosswalks carry no ranking, so a loan in both enterprises' chains stops it too.
The edges are then left-joined onto the union of every edge's `HMDAIndex`, giving one row
per loan with nullable downstream ids.

### The conventional edge is chain ∪ direct

Two independent paths reach the GSE loan-level data, and the spine takes both:

- chain: the `hmda_gse` chain crosswalks, HMDA → FHFA → MBS → UMBS;
- direct: the `hmda_gse` direct route, HMDA → `combined/loan_issuance` with no FHFA leg (see
  [the direct route](hmda_gse_matching.md#the-direct-route)).

The direct crosswalk labels every row rather than dropping any, and the spine applies its
admission rule (`matching_direct.admitted_row`):

| Direct `disposition` | Spine | Rows |
|---|---|---:|
| `agrees` | nothing to add; the target is already the chain's | 25,389,503 |
| `increment`, name-pinned, not FHFA-contradicted | admitted: the coverage this edge adds | 1,152,380 |
| `increment`, nameless, FHFA-corroborated, target with a UMBS identity | admitted | 11,936 |
| `increment`, nameless, otherwise | excluded | 117,529 |
| `increment`, FHFA-contradicted | excluded: held-out destination agrees on 59% (round 1) and 3% (round 2) | 1,312 |
| `contested_target` | neither id stored; recorded as `mbs_contested` | 148,046 |
| `dropped_double_claim` | excluded (record-exclusivity) | 135,189 |

Those labels describe the chain when the direct crosswalk was built, and the chain
crosswalks can be rebuilt after it. The spine re-checks every admitted `increment` row
against the current chain: one the chain now links to the same target counts as `agrees`,
one it links to a different target as `contested_target`. The build log reports both
counts; at the current build both are 0.

### Record-exclusivity is re-derived globally

A claimed-target screen scoped to one activity year cannot see a target the chain gave an
adjacent year's HMDA loan, and the matcher deliberately admits prior-year Q4 originations,
so every leak is cross-year. The direct matcher screens globally itself (`admit` over
every chain year, `pooled_unique` over every direct year), so the spine's re-screen is a
guard: at this build it removes none of the 1,164,316 admitted increment rows.
The chain itself reuses 0 targets across all eight years.

`load_direct_edge()` takes no year range on purpose. The exclusivity universe must span
every built year, not the years being rebuilt: with only 2024 loaded, a target also claimed
by a 2021 increment loan has no visible partner and survives, so a one-year rebuild would
disagree with the full build.

### Conflict policy: withhold, don't adjudicate

Where two independent methods disagree about the same fact, the spine stores neither side
and records the reason in `conflict_flags`. The test is whether the two claims exclude each
other: two matchers naming different targets for one loan cannot both be right.

| `conflict_flags` | Loans | Withheld | Why it is not adjudicable |
|---|---:|---|---|
| `mbs_contested` | 148,046 | the conventional link | 86.1% name the same enterprise and differ only on which loan inside it; most contested pairs are twins on every held-out field. For the 13.9% that differ on the enterprise, the FHFA pin cannot decide: the chain routes through FHFA, so its agreement is tautological. |
| `gse_gnma` | 746 | the GSE links (conventional, FHFA) and the GNMA link | a loan is securitized by one guarantor. An FHA endorsement beside a GSE link is not a conflict: the enterprises acquire FHA/VA/RHS-insured loans and disclose them with `federal_guarantee` set, and both FHFA matchers key on that field. |
| `gnma_disagree` | 16 | the GNMA link | the FHLB→GNMA bridge and the GNMA crosswalks name different GNMA loans. Where both name one they agree on 6,660 of 6,676 loans (99.8%). |
| `fha_cross_era` | 3,104 | the FHA link, and the FHA-program GNMA link (1,101), which is keyed on that endorsement's amount and month | the [pre-2018 HMDA-FHA crosswalk](hmda_fha_matching.md#unioning-the-two-eras) pairs the same endorsement with a 2017 loan. Both eras reach late-2017 / January-2018 endorsements and each is 1:1 only within itself, so both pairs cannot be right. |

151,912 loans (0.33% of the spine) carry at least one record. The 3,307 with every link
withheld are kept with all `in_*` false and `conflict_flags` set: a matcher reached the
loan, but no link is assertable.

Multi-purchaser seller/purchaser loans are not a conflict class. Successive buyers each
file a purchase record for the same loan, so both records are true and only the order is
open; they are stored as an ownership chain.

`policy="flag"` on `build_master_crosswalk` instead keeps the priority winner and records
the reason, for maximum coverage.

### Ownership chain

A loan sold more than once has one HMDA purchase record per buyer, each matching the
origination. The spine stores the chain:

| Column | Meaning |
|---|---|
| `sp_chain` | ordered list of purchase-record `HMDAIndex` |
| `sp_chain_n` | HMDA-visible hops (1 or 2; no loan has more) |
| `sp_chain_ordered` | whether the order is established |
| `purchaser_hmdaindex` | the first hop; null when a multi-hop chain could not be ordered, so it is never an arbitrary pick |

The order is inferred, not traversed. No purchase record is itself a seller in the
crosswalk (0), so there is no linked list; the matcher links the origination to each buyer
directly. The signal is each record's own `purchaser_type`: a record reporting an agency
buyer sold the loan on to that agency, so it is the last HMDA-visible hop. Exactly one of
the two records reports an onward agency sale for 107,475 of the 179,075 multi-hop loans
(60.0%); the rest stay unordered. Activity year cannot order them: both purchase records
share an activity year for 99.1% of multi-hop loans.

The chain stops before the agency. Fannie Mae, Freddie Mac and Ginnie Mae are not HMDA
reporters and file no purchase records; the largest purchased-loan filers of 2018–2025 are
aggregators (Wells Fargo 1.80M records, PennyMac 1.76M, AmeriHome 1.43M). The terminal
agency hop is read from `agency` / `fhfa_enterprise` / `in_gnma`, which is also why the
upstream matcher excludes originations with `purchaser_type` 1–4: a sale to an agency
never has a matching purchase record.

`resolve_ownership_chain()` returns the assembled sequence (`owners`: the origination, each
HMDA purchase record, then the agency where the spine names one):

| HMDA purchase records | Agency on the spine | `n_owners` | Loans |
|---:|---|---:|---:|
| 1 | no | 2 | 2,192,660 |
| 1 | yes | 3 | 8,352,521 |
| 2 | no | 3 | 54,609 |
| 2 | yes | 4 | 124,466 |
| Total | | | 10,724,256 |

```python
>>> import polars as pl
>>> from mortgage_data_manager.matching.master import resolve_ownership_chain
>>> resolve_ownership_chain(ordered_only=True).filter(pl.col("n_owners") == 4).head(1).collect()
# owners: 2018a_013233934 -> 2018a_003453350 -> 2018a_005073311 -> FNMA
```

Where the order is established and the spine's conventional or GNMA edge also names an
agency, the last hop's self-reported buyer and the spine agree 99.8% (FHLMC, 28,150 of
28,219), 98.0% (FNMA, 25,724 of 26,257) and 100.0% (GNMA, 34,482 of 34,493). Counting every
ordered chain whose last hop reports an agency, with a missing spine agency link as
unconfirmed, the rates are 93.5%, 89.7% and 70.8%; the gap is the agency edge's coverage,
not the ordering rule. QC records the terminal-agency distribution on every build, so a
regression in the ordering rule shows up as a drop.

## Output

38 columns: ids and rounds per edge, plus:

| Column | Description |
|---|---|
| `HMDAIndex` | the key; one row each |
| `activity_year` | partition column, also stored in-file (`include_key=True`) |
| `channel` | `conventional` (`in_fhfa` ∪ `in_mbs`), `government` (`in_fha` ∪ `in_gnma`) or `other`. A GSE-acquired FHA loan is `conventional` (the purchaser channel); use `in_fha` for the insurance. |
| `in_*` | one Boolean coverage flag per edge |
| `mbs_source` | `chain`, `direct_corroborated` or `direct_no_link` |
| `mbs_contested` | the direct matcher named a different target (link withheld) |
| `conflict_flags` | semicolon-joined withholding reasons; null when the row is clean |
| `sp_chain` / `sp_chain_n` / `sp_chain_ordered` | the ownership chain |
| `gnma_source` | `fha_direct`, `va`, `usda` or `fhlb` |

| Provenance | Loans |
|---|---:|
| `mbs_source` chain | 30,954,522 |
| `mbs_source` direct_corroborated | 556,354 |
| `mbs_source` direct_no_link | 607,962 |
| `gnma_source` fha_direct | 6,512,373 |
| `gnma_source` va | 3,451,261 |
| `gnma_source` usda | 432,990 |
| `gnma_source` fhlb | 1,233 |

```
mortgage-data match master build [--min-year] [--max-year] [--overwrite] [--no-promote] [--no-verify] [-v]
mortgage-data match master qc [--crosswalk]
mortgage-data match master info
```

`build` assembles into a private staging tree, runs QC, and only then promotes to
`crosswalk/master/`, so a failed QC never replaces a good artifact. Promotion is
partition-scoped: a one-year rebuild touches only that year's subtree. Staging QC sees only
the years just built, so the pooled invariants are re-checked over the whole artifact after
promotion (`--no-verify` skips this).

```python
from mortgage_data_manager.matching.master import (
    build_master_crosswalk, load_master_crosswalk, promote, resolve_security, run_qc,
)

spine = load_master_crosswalk(min_year=2021, columns=["in_mbs", "mbs_loan_id"])

# rebuild one year
build_master_crosswalk(min_year=2024, max_year=2024, overwrite=True)
if run_qc()["passed"]:
    promote(years=[2024])
```

`resolve_security()` is the view for "HMDA loan → its GSE security": one row per asserted
conventional link (contested links are already withheld), with `mbs_source`; `gse=` picks
an enterprise and `chain_only=True` keeps chain links only.

`info` lists the input crosswalks, the per-year coverage of each edge and the last QC.

## QC

Every check must hold to promote. Structural gates:

| Check | Current | Meaning |
|---|---:|---|
| grain | 46,435,403 = unique `HMDAIndex` | one row per loan |
| `mbs_target_collisions` | 0 of 32,118,838 | one MBS id ↔ one HMDA loan, pooled across years |
| `umbs_target_collisions` | 0 of 31,830,567 | same for UMBS ids |
| `umbs_without_mbs` | 0 | a UMBS id never appears without its MBS id |
| `agency_enterprise_mismatch` | 0 of 31,510,876 | GSE named by the matcher equals FHFA's `enterprise_flag` |
| `corroborated_without_fhfa` | 0 | an FHFA-corroborated row has its FHFA link |
| `gnma_master_overlap` | 0 | the FHA and VA/USDA GNMA masters stay disjoint |
| `mbs_without_fhfa` = `direct_no_link` | 607,962 = 607,962 | see below |

The no-conflict gates make the conflict policy verifiable: no asserted link is one another
method contradicts.

| Check | Current | Meaning |
|---|---:|---|
| `contested_with_asserted_mbs` | 0 | no contested loan carries a conventional link |
| `gse_gnma_overlap` | 0 | no loan carries both a GSE and a GNMA link (`gse_acquired_fha` = 954 reports the GSE-acquired FHA loans, which keep both links) |
| `gnma_disagree_asserted` | 0 | no GNMA link survives a bridge/master disagreement |
| `fha_cross_era_asserted` | 0 | no FHA link survives on an endorsement the pre-2018 crosswalk also claims |
| `orphan_rows` | 0 | every row is in ≥1 edge or carries a conflict record |
| `ordered_chain_missing_first_hop` | 0 | an ordered chain always names its first hop |
| `unordered_chain_asserts_first_hop` | 0 | an unordered chain never names one |
| `ordered_chain_ambiguous_first_hop` | 0 | no chain of three or more hops is marked ordered |
| `sp_chain_length_mismatch` | 0 | `sp_chain` length equals `sp_chain_n` |
| `sp_chain_duplicate_hop` | 0 | no owner appears twice in one chain |

Informational, not gates: `mbs_without_umbs` 288,271 (almost all chain rows with no UMBS
record, which is UMBS survivorship, not a defect), `mbs_contested` 148,046 and
`all_links_withheld` 3,307.

The agency/enterprise check is real cross-matcher corroboration for the direct edge: all
556,354 `direct_corroborated` rows carry an agency from the direct matcher and an
enterprise flag from a different matcher on a different dataset, and they
never disagree.

`in_mbs` is not a subset of `in_fhfa`. QC requires `mbs_without_fhfa` to equal the
`direct_no_link` count exactly: every other conventional row either chained, so its FHFA
leg exists by construction, or was FHFA-corroborated. A mismatch means a corroborated row
lost its FHFA link or a `no_link` row gained one.

## Limitations

1. **The denominator is the spine, not HMDA.** A share of the spine is not an HMDA-wide
   match rate; left-join onto HMDA silver by `HMDAIndex` for real denominators.
2. **`conventional` is not `in_fhfa`.** It is `in_fhfa` ∪ `in_mbs`, and `in_mbs` does not
   imply the chain: filter `mbs_source == "chain"` for chain-specific questions.
3. **The newest year is right-censored.** FHFA's PUDB is annual and lagged, and late-year
   originations are acquired the next year, so the newest activity year gains conventional
   links when the following release lands (on the `hmda_fhfa` edge, HMDA 2024's GSE-side
   rate rose from 96.1% to 97.2% when FHFA 2025 arrived). `CONVENTIONAL_MAX_YEAR` (2025) is
   the last year the conventional edges read; advance it with each FHFA PUDB and
   `combined/loan_issuance` release.
4. **A null link can mean withheld, not unmatched.** 151,912 loans had at least one link
   withheld because another method contradicted it. Use `conflict_flags` to tell the two
   apart, and do not count withheld links as non-matches in a match rate.
5. **Pre-2018 is out of scope.** Four pre-2018 crosswalks exist (HMDA-FHFA 2008–2017,
   HMDA-FHA 2010–2017, HMDA-FHLB 2009–2017, sellers/purchasers 2007–2017). Each is 1:1 and
   joins to HMDA directly, so they are not assembled into a spine.
