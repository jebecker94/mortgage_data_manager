"""Canonical schema + source field maps for the ``mbs_performance`` master.

The pool/security-grain paydown-and-prepayment panel (one row per pool x as-of
month) — the security analog of loan-level ``loan_performance``. This module
holds the canonical output schema and the per-agency source-column maps so the
builder body in ``builders.py`` stays a thin assembler (mapping/scaling only) and
the derivation pass (SMM/CPR) is the only bespoke logic.

Source decisions (verified against real data in the Phase-1 investigation, which
corrects the spec draft in two places):

  * **GSE monthly panel = FHLMC ``umbs/bronze/FHLMC/FD``** (the pool factor feed),
    NOT ``IS`` — ``IS`` is new-issuance-only for BOTH GSEs (every CUSIP appears
    once, at factor 1.0). ``FNM_IS`` has no per-pool monthly factor analog
    currently ingested, so the FNMA side is intentionally not built here (see the
    builder note); the FHLMC ``FD`` feed carries the security-level RPB/factor
    panel with declining factor/UPB and incrementing WALA month over month.
  * **GNMA RPB-of-record = ``factorA1`` (Ginnie I) ∪ ``factorA2`` (Ginnie II)**,
    raw fixed-point (apply the ``_gnma_*`` scalers), HMBS filtered by ``Pool
    Type``, WA block left-enriched from ``monthlySFPS`` PS (already decoded).
"""

from __future__ import annotations

from typing import Final

import polars as pl

#: Canonical output schema (ordered). ``agency`` + ``as_of_year`` are the hive
#: partition columns (dropped from the file body); everything else persists.
#: Non-nullable: ``agency``, ``pool_id``, ``as_of_date`` (and the derived
#: ``as_of_year``). All other fields are nullable.
CANONICAL_MBS_PERFORMANCE_SCHEMA: Final[dict[str, pl.DataType]] = {
    "agency": pl.String(),  # partition (NOT NULL)
    "pool_id": pl.String(),  # key (NOT NULL)
    "cusip": pl.String(),
    "as_of_date": pl.Int32(),  # YYYYMM (NOT NULL) [key]
    "as_of_year": pl.Int32(),  # as_of_date // 100 (partition; dropped from body)
    "program": pl.String(),  # {GINNIE_I, GINNIE_II, UMBS, MBS}
    "pool_type": pl.String(),  # agency-native string
    "issue_date": pl.Int32(),  # YYYYMMDD
    "maturity_date": pl.Int32(),  # YYYYMMDD
    "orig_security_upb": pl.Float64(),
    "current_security_upb": pl.Float64(),  # = RPB
    "rpb_factor": pl.Float64(),  # 0-1
    "loan_count": pl.Int64(),
    "passthrough_rate": pl.Float64(),  # net/security coupon (NOT WAC)
    "wac": pl.Float64(),
    "wala": pl.Int32(),
    "warm": pl.Int32(),
    "waolt": pl.Int32(),
    "scheduled_principal": pl.Float64(),  # derived
    "unscheduled_principal": pl.Float64(),  # derived
    "total_principal_reduction": pl.Float64(),  # derived
    "smm": pl.Float64(),  # derived 0-1
    "cpr_1m": pl.Float64(),  # derived %
    "cpr_3m": pl.Float64(),  # derived % rolling
    "involuntary_removal_count": pl.Int64(),  # GSE only
    "involuntary_removal_upb": pl.Float64(),  # GSE only
    "pool_status": pl.String(),  # {ACTIVE, TERMINATED}
    "termination_date": pl.Int32(),  # GNMA ptermmon only
    "security_status_raw": pl.String(),  # GSE verbatim
    "record_source": pl.String(),  # {original, correction}
    "_source_dataset": pl.String(),  # originating feed name
}

#: HMBS (reverse-mortgage) pool types to drop from the GNMA factor universe.
#: Cross-checked in Phase-1 against the ``hmonthly`` HMBS pool universe: these
#: types are 100% HMBS; ``RX``/``RG`` are forward-MBS despite the "R" prefix and
#: are NOT dropped.
GNMA_HMBS_POOL_TYPES: Final[tuple[str, ...]] = ("RF", "RM", "ML", "AL", "RA")
