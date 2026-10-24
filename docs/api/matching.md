# Matching API Reference

`matching` holds the workflows that link records across mortgage datasets. Each
workflow's method, match rates and output schema are on its own page in the
[matching docs](../matching/README.md); this page covers the shared shape and the Python
entry points.

## Overview

Each workflow lives in `matching/match_{workflow}/`, is self-contained, and writes its
crosswalks to `crosswalk/{workflow}/{workflow}_crosswalk[_{qualifier}].parquet`. Names
put HMDA first and the MBS/UMBS side last. A workflow has `config.py`, `rounds.py`, its
matching modules (`matching.py`, or `matching_{era}.py` / `matching_{target}.py` where
eras or targets need different code), and `validation.py` where it validates. Its CLI has
`info`, `run` and `validate` (`crt-mbs` has `check` in place of `validate`). The entry
point is `run_matching`, suffixed by era or target where a workflow has several.

`rounds.py` holds a workflow's rounds as a tuple of round objects, run in order, each on
what earlier rounds left unmatched; a round's `match_round` is its position in the tuple.
Copying an entry and changing its fields adds a round, with no other edit. `info` prints
the rounds.

| Module | CLI | Links |
|---|---|---|
| `match_hmda_fha` | `match hmda-fha` | HMDA ↔ FHA endorsements |
| `match_hmda_fhfa` | `match hmda-fhfa` | HMDA ↔ FHFA PUDB |
| `match_hmda_fhlb` | `match hmda-fhlb` | HMDA ↔ FHLB AMA |
| `match_hmda_gse` | `match hmda-gse` | HMDA ↔ FNMA/FHLMC loan level: the HMDA → FHFA → MBS → UMBS chain, and the direct match |
| `match_hmda_gnma` | `match hmda-gnma` | HMDA FHA and VA/USDA ↔ GNMA loan level |
| `match_hmda_sellers_purchasers` | `match hmda-sellers-purchasers` | HMDA originations ↔ HMDA purchase records |
| `match_fhfa_mbs` | `match fhfa-mbs` | FHFA PUDB ↔ GSE loan level |
| `match_fhlb_mbs` | `match fhlb-mbs` | FHLB AMA ↔ GNMA loan level |
| `match_crt_mbs` | `match crt-mbs` | CRT reference pools ↔ GSE SF loan level |
| `match_mbs_umbs` | `match mbs-umbs` | GSE SF loan level ↔ UMBS ILLD |
| `master` | `match master` | Joins every edge above onto one HMDA spine (`build`, `qc`, `info`) |

Three helper modules live inside the one workflow that uses them rather than producing
crosswalks of their own: `match_hmda_gse.lender_names` gathers every HMDA-side name an
LEI is known by, which the direct HMDA↔issuance matcher compares to GSE seller names;
`match_hmda_fha.lender_bridge` (`match hmda-fha run --era pre2018 --step bridge`) maps
FHA mortgagee numbers to pre-2018 HMDA respondents; and `match_hmda_gnma.triangulation`
(`match hmda-gnma validate`) scores the HMDA↔GNMA crosswalks.

There is no shared key set: each workflow blocks on what its two sources carry, and
tolerances differ between workflows. The workflow's page and its `rounds.py` state the
keys and tolerances of every round.

```bash
mortgage-data match info                         # every workflow and where its output lives
mortgage-data match hmda-fha info                # one workflow's rounds and paths
mortgage-data match hmda-fha run
mortgage-data match master build
```

The [CLI Reference](../cli_reference.md#mortgage-data-match) lists every command and
option.

## Configuration

::: mortgage_data_manager.matching.config

## Shared helpers

::: mortgage_data_manager.matching.names

::: mortgage_data_manager.matching.terms

::: mortgage_data_manager.matching.resolve

::: mortgage_data_manager.matching.validation_common

## Workflow helpers

::: mortgage_data_manager.matching.match_hmda_fha.lender_bridge

::: mortgage_data_manager.matching.match_hmda_gse.lender_names

::: mortgage_data_manager.matching.match_hmda_gnma.triangulation

## HMDA-FHA matching

`run_matching` runs the post-2018 era and `run_matching_pre2018` the 2010–2017 era, each
running its tuple in `rounds.py` and returning the path of the crosswalk it wrote
(`crosswalk/hmda_fha/hmda_fha_crosswalk_post2018.parquet` or `..._pre2018.parquet`).

```python
from mortgage_data_manager.matching.match_hmda_fha import run_matching

output_file = run_matching(min_year=2020, max_year=2024)
```

::: mortgage_data_manager.matching.match_hmda_fha.matching_post2018
    options:
      members: [run_matching]

::: mortgage_data_manager.matching.match_hmda_fha.matching_pre2018
    options:
      members: [run_matching_pre2018, run_rounds_pre2018]

::: mortgage_data_manager.matching.match_hmda_fha.rounds

## FHFA-MBS matching

Matches GSE loan-level disclosures to FHFA single-family acquisitions. One engine serves
both enterprises; a `GseSpec` (`FNMA`, `FHLMC`) carries where the loan-level data lives,
how it maps onto the matching schema, the options that enterprise runs and its rounds
(`ROUNDS_FNMA`, `ROUNDS_FHLMC`). A multi-year run then drops every loan that two
acquisition years both claim, since nothing in the data identifies the true year; a
single-year run writes its crosswalk without that step.

```python
from mortgage_data_manager.matching.match_fhfa_mbs import (
    FHLMC, FNMA, match_year, run_matching, run_year,
)

fnma_pairs = run_year(2023, FNMA)                     # one year, written unresolved
fhlmc_pairs = match_year(2023, FHLMC)                 # pairs only, nothing written
run_matching("fnma", min_year=2018, max_year=2024)    # resolved crosswalks
```

A separate FHFA→UMBS ARM crosswalk (`run_matching_arm`, rounds in `ROUNDS_ARM`) covers
FHFA ARM acquisitions after the SF loan-level files stop carrying ARMs (their non-standard
releases end in 2020Q3 for Fannie and 2021Q2 for Freddie). It matches them against the
UMBS ARM disclosures, pools both enterprises and all acquisition years, and is keyed on
the UMBS loan id
(`crosswalk/fhfa_mbs/fhfa_mbs_crosswalk_arm.parquet`; `match fhfa-mbs run --arm`).

## Triangulation validator

`match_hmda_gnma.triangulation` is a DTI-independent quality gauge for any HMDA ↔ GNMA
crosswalk. The pool issuer in GNMA's `issrinfo` file is the lender that delivered the
loan; when the sellers/purchasers crosswalk links the origination to a purchase record,
the purchaser's HMDA panel name should agree with the issuer name. `triangulate()`
reports `aggregator` (share of testable pairs where the purchaser name token-matches the
issuer name) beside `originator_ctrl` (the same test against the originator, which stays
low regardless of match quality). It reads none of the fields the matchers key on, so it
can score a candidate matcher change without circularity.

It scores against a sellers/purchasers → LEI pair table that `validate` builds on first
use:

```bash
mortgage-data match hmda-gnma validate --by match_round   # both crosswalks
mortgage-data match hmda-gnma validate --overwrite        # after an s/p or HMDA rebuild
mortgage-data match hmda-gnma validate --crosswalk staged.parquet
```

## See Also

- [Matching docs](../matching/README.md): one page per workflow
- [Core API](core.md): shared utilities
- [HMDA API](hmda.md): HMDA data access
- [Configuration Guide](../configuration.md): environment variables
