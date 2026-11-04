"""Canonical schema and leg plans for the ``mbs_performance`` master.

The pool/security-grain paydown-and-prepayment panel (one row per pool x as-of
month) — the security analog of loan-level ``loan_performance``. This module
holds the canonical output schema and one plan table per source feed, so each
assembler in `mortgage_data_manager.combined.mbs_performance` is a filter plus a
select built from its plan, and the derivation pass (SMM/CPR) is the only bespoke
logic.

Source decisions (verified against real data, which corrected the first design in
two places):

  * **GSE monthly panel = FNMA ``umbs/bronze/FNMA/FNM_MF`` ∪ FHLMC
    ``umbs/bronze/FHLMC/FD``** (the pool factor feeds), NOT ``IS`` — ``IS`` is
    new-issuance-only for BOTH GSEs (every CUSIP appears once, at factor 1.0).
    ``FNM_MF`` is the direct analog of FHLMC ``FD`` (identical UMBS security-
    disclosure layout, 2019-06+), so both share one assembler; each carries the
    security-level RPB/factor panel with declining factor/UPB and incrementing WALA
    month over month. (``FNM_DPR_FCTR`` is a cohort aggregate at the wrong grain.)
  * **GNMA RPB-of-record = ``factorA1`` (Ginnie I) ∪ ``factorA2`` (Ginnie II)**,
    canonical decoded silver (real units; dates as ``pl.Date``), HMBS filtered by
    ``Pool Type``, WA block left-enriched from ``monthlySFPS`` PS (already decoded).
  * **Resecuritization layers.** GNMA Platinum comes from ``factorAplat`` (the RPB
    panel; the base factor files publish those securities only as null-RPB stubs),
    WA block from ``platmonPPS`` PS; Fannie's Ginnie-backed Megas come from
    ``umbs/silver/FNMA/GN_MEGA``, whose current UPB is derived (issuance UPB x
    factor) because the feed publishes no current-balance column. Both are flagged
    ``is_resecuritization`` — see `STRUCTURE_CLASSES` for the aggregation rule.
"""

from __future__ import annotations

from typing import Final

import polars as pl

from mortgage_data_manager.combined._common import LegField as F

#: Canonical output schema (ordered). ``agency`` + ``as_of_year`` are the hive
#: partition columns (dropped from the file body); everything else persists.
#: Non-nullable: ``agency``, ``pool_id``, ``as_of_month`` (and the derived
#: ``as_of_year``). All other fields are nullable.
CANONICAL_MBS_PERFORMANCE_SCHEMA: Final[dict[str, pl.DataType]] = {
    "agency": pl.String(),  # partition (NOT NULL)
    "pool_id": pl.String(),  # key (NOT NULL)
    "cusip": pl.String(),
    "as_of_month": pl.Int64(),  # YYYYMM (NOT NULL) [key]
    "as_of_year": pl.Int32(),  # as_of_month // 100 (partition; dropped from body)
    # {ginnie_i, ginnie_ii, ginnie_platinum, gn_mega, umbs, mbs} -- one lowercase
    # spelling shared with the loan grains and with ``mbs_issuance``, so the masters
    # join on it.
    "program": pl.String(),
    "pool_type": pl.String(),  # agency-native string
    "structure_class": pl.String(),  # coarse cross-agency structure (see STRUCTURE_CLASSES)
    "is_resecuritization": pl.Boolean(),  # True = backed by securities, not loans
    "issue_date": pl.Date(),
    "maturity_date": pl.Date(),
    "original_face": pl.Float64(),
    "current_face": pl.Float64(),  # = RPB
    "security_factor": pl.Float64(),  # 0-1
    "loan_count": pl.Int64(),
    "pass_through_rate": pl.Float64(),  # net/security coupon (NOT WAC)
    "wac": pl.Float64(),
    "wa_loan_age": pl.Int32(),
    "wa_rem_term": pl.Int32(),
    "wa_orig_loan_term": pl.Int32(),
    "scheduled_principal": pl.Float64(),  # derived
    "unscheduled_principal": pl.Float64(),  # derived
    "total_principal_reduction": pl.Float64(),  # derived
    "smm": pl.Float64(),  # derived 0-1
    "cpr_1m": pl.Float64(),  # derived %
    "cpr_3m": pl.Float64(),  # derived %; 3 compounded survivals, null until 3 months
    "amort_basis": pl.String(),  # {wa_block, proxy}; null => smm/cpr not computable
    "involuntary_removal_count": pl.Int64(),  # GSE only
    "involuntary_removal_upb": pl.Float64(),  # GSE only
    "pool_status": pl.String(),  # {ACTIVE, TERMINATED}
    "termination_date": pl.Date(),  # disclosed (GNMA) or synthesized first-of-month
    "termination_source": pl.String(),  # see TERMINATION_SOURCES; null when ACTIVE
    "security_status_raw": pl.String(),  # GSE verbatim
    "record_source": pl.String(),  # {original, correction}
    # Originating feed: {FNM_MF, FD, factorA1, factorA2, factorAplat, GN_MEGA}.
    "source_dataset": pl.String(),
}

#: HMBS (reverse-mortgage) pool types to drop from the GNMA factor universe.
#: Cross-checked against the ``hmonthly`` HMBS pool universe: these
#: types are 100% HMBS; ``RX``/``RG`` are forward-MBS despite the "R" prefix and
#: are NOT dropped.
GNMA_HMBS_POOL_TYPES: Final[tuple[str, ...]] = ("RF", "RM", "ML", "AL", "RA")

#: ``Issuer Number`` reserved for GNMA Platinum securities, which ``factorA1`` /
#: ``factorA2`` publish as **identifier-only stubs**: pool id, CUSIP, pool type,
#: issue/maturity date, original aggregate amount and coupon are populated, but
#: ``Remaining Security RPB`` and ``RPB Factor`` are 100% null, so the row carries no
#: paydown and every derived speed is undefined. Left in, they were 719,569 of
#: 44,053,343 GNMA pool-months (1.6%) reporting as ``ACTIVE`` with a null balance —
#: 6,871 rows and $822.6B of original face in 2026-04 alone — so they are filtered
#: out of the factorA leg.
#:
#: Three equivalent predicates identify them, verified across all 165 months of both
#: files: ``issuer_number == "9999"``, ``pool_number`` starting ``78``, and a null
#: RPB. The issuer number is the one used, because the null-RPB form also catches
#: unrelated early-vintage rows (1,655 in ``factorA2`` 2012-08) and the three
#: header/trailer records already dropped by the blank-CUSIP filter. Their pool types
#: are a disjoint set — ``SP``, ``JP``, ``AP``, ``BP``, ``EP``, ``PB``, ``PC``,
#: ``PD``, ``PF``, ``WA``, ``WB``, ``WK``, ``WL``, ``WM``, ``WN`` — the Platinum code
#: space, which ``core.reference.gnma_pool_types`` resolves with ``family="platinum"``.
#:
#: Platinum is added back to the master from ``factorAplat``, which carries the real
#: RPB panel, flagged ``is_resecuritization`` — see
#: `GNMA_PLATINUM_HMBS_INDICATOR`.
GNMA_PLATINUM_ISSUER_NUMBER: Final[str] = "9999"

#: ``Pool Indicator`` marking a HECM-backed (HMBS) Platinum security in
#: ``factorAplat`` / ``platmonPPS``. The base panel drops HMBS by ``Pool Type``
#: (`GNMA_HMBS_POOL_TYPES`), but Platinum pool types are a different code
#: space — HMBS Platinum carries ``PA``..``PF``, forward Platinum ``SP``/``JP``/
#: ``AP``/``BP``/``EP``/``WA``..``WN`` — so the indicator, not the pool type, is the
#: rule that keeps the same HMBS exclusion on the Platinum leg. Zero rows before
#: 2024; 152 of 6,871 in 2026-04.
GNMA_PLATINUM_HMBS_INDICATOR: Final[str] = "H"

#: GSE ``Security Status Indicator`` values that mean the security is finished.
#: The full 44.1M-row FNMA ``FNM_MF`` panel (2019-06..2026-06) plus the 3.07M-row
#: FHLMC ``FD`` panel (2025-12..2026-06) carry exactly three values:
#:
#:   * ``A`` — active. 43.96M / 3.06M rows; ``Security Factor`` is *never* 0 and
#:     never null on an ``A`` row.
#:   * ``P`` — paid off. 141,533 / 7,972 rows, and the equivalence with the
#:     factor rule is exact in both directions: every ``P`` row has factor == 0
#:     AND UPB == 0, and no ``A`` row does. A ``P`` row is always the pool's last
#:     observed row (no pool has two of them, none reappears afterwards).
#:   * ``D`` — deleted/dissolved. FNMA only, 38 rows over 36 pools (2020-06..
#:     2026-04); factor and UPB are null rather than 0, and the row is again the
#:     pool's last (two pools carry a valued ``D`` row followed by a null one).
#:
#: So the indicator adds real information only for ``D``: those pools end without
#: ever printing a zero factor.
GSE_TERMINAL_STATUS_INDICATORS: Final[tuple[str, ...]] = ("P", "D")

#: What established a ``TERMINATED`` row, in descending authority — and therefore
#: also where its ``termination_date`` came from. ``ptermmon`` is the only
#: *disclosed* date; ``disappearance`` synthesizes one as the first day of the first
#: month in which the pool is absent from its agency's feed; the other two leave the
#: date to whatever the pool-level rule produced (null while the pool still reports).
#:
#:   * ``status_indicator`` — a GSE row whose ``Security Status Indicator`` is in
#:     `GSE_TERMINAL_STATUS_INDICATORS`. The agency says the security is done.
#:   * ``ptermmon`` — GNMA's disclosed Termination Date. Empirically it falls in
#:     the month *after* the pool's last factor month (113,633 of 113,644 pools;
#:     the other 11 are +2 months), on business day 23-27. The synthesized
#:     convention therefore matches ptermmon's month and differs only in using
#:     ``01`` as a placeholder day.
#:   * ``disappearance`` — no disclosure says so; inferred because the pool is
#:     absent from every later month of a contiguous monthly feed.
#:   * ``zero_factor`` — the row itself is paid down to nothing (zero RPB factor or
#:     zero UPB) but the pool is still reporting at the agency frontier, so no
#:     absent month is observable yet. GNMA-only in practice: a GSE pool at zero
#:     always carries status ``P`` as well.
TERMINATION_SOURCES: Final[tuple[str, ...]] = (
    "status_indicator",
    "ptermmon",
    "disappearance",
    "zero_factor",
)

#: Coarse cross-agency structure classes for ``structure_class``.
#:
#:   * ``pass_through`` — a base pool backed directly by mortgage loans.
#:   * ``giant`` / ``mega`` — legacy (pre-UMBS) single-class resecuritizations:
#:     Freddie Giant PCs and Fannie Megas. ``mega`` covers both the Megas the FNMA
#:     factor feed marks with the seller literal and the Ginnie-backed Megas read
#:     from ``GN_MEGA``; ``program`` (``gn_mega``) and ``source_dataset`` separate
#:     the two.
#:   * ``super`` — UMBS-era single-class resecuritization (Supers), both GSEs.
#:   * ``mirror`` — Freddie's Single Security Initiative Mirror certificates: a
#:     55-day UMBS whose sole collateral is one 45-day Gold PC.
#:   * ``platinum`` — GNMA Platinum pool-of-pools, read from ``factorAplat`` (the
#:     RPB of record; ``factorA1``/``factorA2`` carry the same securities only as
#:     null-RPB stubs, which the base leg drops). Platinum nests: 8.2% of 2026-04
#:     Platinum RPB is collateralized by other Platinum securities, and a base pool
#:     can sit in as many as 63 different Platinums at fractional slices, so the
#:     wrapper layer cannot be collapsed to a single parent id.
#:   * ``remic`` — reserved. REMICs re-tranche collateral into bond classes, so
#:     their grain is (trust, class), not (security); they would need their own
#:     master, not a row here.
#:   * ``other`` — reserved fallback.
#:
#: **Aggregation rule.** This master is a union of overlapping security layers. Any
#: aggregate over balances, loan counts or pool counts MUST filter
#: ``is_resecuritization = False`` unless the question is explicitly about the
#: wrapper. In 2026-04, GNMA base RPB is $2.927T and Platinum RPB is $189.8B on top
#: of it; the GSE feeds mix Supers/Giants/Megas/Mirrors into the same month the same
#: way.
STRUCTURE_CLASSES: Final[tuple[str, ...]] = (
    "pass_through",
    "giant",
    "mega",
    "super",
    "mirror",
    "platinum",
    "remic",
    "other",
)

#: Seller/servicer literal that marks a GSE resecuritization in ``FNM_MF`` / ``FD``.
#:
#: Both feeds put a code rather than a firm in ``Seller Name`` **and** ``Servicer
#: Name`` when the security is backed by other securities: ``SCR`` (single-class
#: resecuritization) and, for FHLMC only, ``SCR - Mirror``. The marker is present in
#: every month back to the start of both feeds, and the aggregate check is decisive:
#: in 2026-06 the summed ``Loan Count`` over ``SCR`` rows is 578M (FHLMC) and 1.50B
#: (FNMA) against 4.8M / 5.8M loans in the named-seller pools — the counts are
#: roll-ups of repeatedly nested collateral, not loans.
#:
#: **Consequence: these feeds double count.** Summing ``current_face``
#: across a whole month mixes base pools with the resecuritizations that wrap them
#: — $6.37T vs $3.06T of base collateral for FHLMC 2026-06, $5.42T vs $3.49T for
#: FNMA. Filter ``is_resecuritization`` before aggregating RPB.
#:
#: Neither ``Prefix`` nor ``Security Description`` identifies the structure on its
#: own, which is why the seller literal is the rule. ``Security Description``'s
#: leading token is the *product*, not the structure — FHLMC ``FGPC`` (Gold PC) /
#: ``FEPC`` (UMBS PC) / ``FMAR``/``FGAR``/``FEAR`` (ARM), FNMA ``FNMS`` / ``FNAR``
#: — and every token appears on both base and resecuritized rows. ``Prefix`` is
#: shared the same way: the biggest FHLMC prefixes (``CL``, ``CI``, ``CT``) and the
#: FNMA ones (``CL``, ``CI``, ``CT``, ``LB``) each carry base *and* ``SCR`` rows.
#: Some prefixes do happen to be resec-only in the current data (FHLMC ``G0``,
#: ``G1``, ``G3``, ``G6``, ``84``, ``Z4``), but that is an observation about a
#: snapshot, not a documented code space, so it is not encoded here. The era split
#: below uses ``issue_date`` for the same reason.
GSE_RESEC_SELLER_PREFIX: Final[str] = "SCR"

#: FHLMC Mirror-certificate marker (a longer prefix of `GSE_RESEC_SELLER_PREFIX`;
#: compared case-insensitively because the feed spells it both ``SCR - Mirror`` and
#: ``SCR - MIRROR``).
GSE_MIRROR_SELLER_PREFIX: Final[str] = "SCR - MIRROR"

#: UMBS era boundary as ``Int32`` ``YYYYMMDD`` (the Single Security Initiative went
#: live 2019-06-03). Splits ``program`` UMBS/MBS and legacy-vs-Supers resec classes.
UMBS_ERA_START: Final[int] = 20190601

#: What the level-payment amortization in the prepayment derivation was computed from,
#: recorded per row in ``amort_basis`` so the derived speeds carry their own quality
#: flag. Null means no amortization was computed — the pool's first observed month,
#: or neither a rate nor a remaining term was available — and ``smm`` / ``cpr_1m`` /
#: ``cpr_3m`` are then null rather than 0.
#:
#:   * ``wa_block`` — the disclosed WA block: ``wac`` for the rate and the prior
#:     month's ``wa_rem_term`` for the remaining term.
#:   * ``proxy`` — at least one input was substituted: ``pass_through_rate`` for the
#:     note rate (a pass-through gap of tens of basis points), and/or months from the
#:     as-of month to ``maturity_date`` (floored at 1) for the remaining term.
#:
#: The proxy tier exists because base GNMA has no WA block before 2020-05, when
#: ``monthlySFPS`` begins — roughly half the Ginnie panel. Reporting those months
#: with ``scheduled_principal = 0``, as the first cut did, hands the whole scheduled
#: paydown to prepayment: median ``cpr_1m`` was 20.36% on WA-less GNMA months against
#: 8.09% where the block exists, a 2.5x overstatement.
AMORT_BASIS_VALUES: Final[tuple[str, ...]] = ("wa_block", "proxy")

# Validation of the resecuritization rule against loan-level collateral, 2026-04.
# A security is a genuine collateral pool if and only if its ``Security Identifier``
# appears as a pool in the loan-level disclosure for that month — FNMA ``FNM_ILLD``
# (that month's new issuance, 2,916 pools) union ``FNM_MLLD`` (the outstanding
# universe, 315,948), FHLMC ``FU`` (315,809). Everything else in the factor feed is
# backed by securities rather than loans.
#
# The seller-literal rule scores **precision 1.0000 on both GSEs**: not one security
# it flags has loan-level collateral. Its apparent recall (0.19 FNMA / 0.68 FHLMC by
# count; 93.4% / 99.8% of the absent-from-loan-level UPB) understates it, because the
# loan-level files only reach back to 2013 (FNMA) and 2006 (FHLMC). The securities
# the rule "misses" are old, tiny base pools from before that coverage — 197,336
# FNMA securities holding $135.6B at a median $136k and ~8 loans each, and 44,028
# FHLMC holding $7.4B at a median $64k and ~4 loans — against a flagged population
# whose loan counts sum to 1.45 BILLION (FNMA) and 599M (FHLMC).
#
# A rule keyed on ``Security Identifier`` prefix families instead scores the same
# perfect precision but strictly lower recall (35.05% vs 35.62% of FNMA panel UPB;
# 51.00% vs 52.08% FHLMC), and rests on a snapshot-derived list rather than a
# documented code space — so the seller literal stays the rule.


# ===================== leg plans (see `mbs_issuance_schema` for the format) ==========

#: GSE monthly factor feeds, FNMA ``FNM_MF`` and FHLMC ``FD`` (one layout). The packed
#: ``Int64`` dates are parsed by the assembler; the buyout pair took its current name at
#: FNMA in 2024-12, hence the aliases.
GSE_MBS_PERFORMANCE_PLAN: Final[list[F]] = [
    F("agency", "derived"),
    F("pool_id", "col_str", ("Security Identifier",)),
    F("cusip", "str_cast", ("CUSIP",)),
    F("as_of_month", "derived"),
    F("program", "derived"),
    F("pool_type", "str_cast", ("Prefix",)),
    F("structure_class", "derived"),
    F("is_resecuritization", "derived"),
    F("issue_date", "derived"),
    F("maturity_date", "derived"),
    F("original_face", "num", ("Issuance Investor Security UPB",)),
    F("current_face", "num", ("Current Investor Security UPB",)),
    F("security_factor", "num", ("Security Factor",)),
    F("loan_count", "int64", ("Loan Count",)),
    F("pass_through_rate", "num", ("WA Net Interest Rate",)),
    F(
        "wac",
        "num",
        ("WA Current Interest Rate", "WA Issuance Interest Rate", "WA Origination Interest Rate"),
    ),
    F("wa_loan_age", "int32", ("WA Loan Age",)),
    F("wa_rem_term", "int32", ("WA Current Remaining Months to Maturity",)),
    F("wa_orig_loan_term", "int32", ("WA Loan Term",)),
    F(
        "involuntary_removal_count",
        "int64",
        ("Involuntary Loan Removal (Loan Count)", "Delinquent Loans Purchased (Loan Count)"),
    ),
    F(
        "involuntary_removal_upb",
        "num",
        (
            "Involuntary Loan Removal (Prior Month UPB)",
            "Delinquent Loans Purchased (Prior Month UPB)",
        ),
    ),
    F("security_status_raw", "str_cast", ("Security Status Indicator",)),
    F("record_source", "derived"),
    F("source_dataset", "derived"),
]

#: GNMA ``factorA1`` / ``factorA2``, base pools only; the WA block and loan count are
#: joined from ``monthlySFPS`` PS (`GNMA_WA_PERFORMANCE_PLAN`).
GNMA_MBS_PERFORMANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="GNMA"),
    F("pool_id", "str", ("Pool Number",)),
    F("cusip", "str", ("CUSIP Number",)),
    F("as_of_month", "derived"),
    F("program", "derived"),
    F("pool_type", "str", ("Pool Type",)),
    # Base pools only, after the assembler's filters: the surviving pool-type code
    # space (see core.reference.gnma_pool_types) has no REMIC member, and the
    # issue-type field carries only X / C / M. Platinum stubs are dropped and belong
    # to the factorAplat leg; REMICs live in the REMIC factor files.
    F("structure_class", "const", value="pass_through"),
    F("is_resecuritization", "const", value=False),
    F("issue_date", "ymd", ("Pool Issue Date (MMDDYY)",)),
    F("maturity_date", "ymd", ("Pool Maturity Date (MMDDYY)",)),
    F("original_face", "gnma_num", ("Original Aggregate Amount",)),
    F("current_face", "gnma_num_keep0", ("Remaining Security RPB",)),
    F("security_factor", "num", ("RPB Factor",)),
    F("pass_through_rate", "gnma_num", ("Pool Interest Rate",)),
    F("record_source", "const", value="original"),
    F("source_dataset", "derived"),
]

#: ``monthlySFPS`` / ``platmonPPS`` PS (one layout): the WA block by pool and month.
GNMA_WA_PERFORMANCE_PLAN: Final[list[F]] = [
    F("pool_id", "str", ("Pool ID",)),
    F("as_of_month", "derived"),
    F("wac", "ps_num", ("WA Interest Rate (WAC)",)),
    F("wa_loan_age", "ps_int32", ("WA Loan Age (WALA)",)),
    F("wa_rem_term", "ps_int32", ("WA Remaining Months to Maturity (WARM)",)),
    F("wa_orig_loan_term", "ps_int32", ("WA Original Loan Term (WAOLT)",)),
    F("loan_count", "ps_int64", ("Number of loans in pool",)),
]

#: GNMA ``factorAplat``, the Platinum RPB panel; WA block from ``platmonPPS`` PS.
GNMA_PLATINUM_MBS_PERFORMANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="GNMA"),
    F("pool_id", "str", ("pool_id",)),
    F("cusip", "str", ("cusip_number",)),
    F("as_of_month", "derived"),
    F("program", "derived"),
    F("pool_type", "str", ("pool_type",)),
    F("structure_class", "const", value="platinum"),
    F("is_resecuritization", "const", value=True),
    F("issue_date", "ymd", ("pool_issue_date",)),
    F("maturity_date", "ymd", ("pool_maturity_date",)),
    F("original_face", "gnma_num", ("original_aggregate_amount",)),
    F("current_face", "gnma_num_keep0", ("remaining_security_rpb",)),
    F("security_factor", "num", ("rpb_factor",)),
    F("pass_through_rate", "gnma_num", ("pool_interest_rate",)),
    F("record_source", "const", value="original"),
    F("source_dataset", "const", value="factorAplat"),
]

#: Fannie's Ginnie-backed Megas (``umbs/silver/FNMA/GN_MEGA``). The feed has no
#: current-balance column, so ``current_face`` is derived; note the double space in
#: the first ``Issuance Investor  Security UPB`` alias, the source spelling.
GN_MEGA_PERFORMANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="FNMA"),
    F("pool_id", "col_str_strip", ("Security Identifier",)),
    F("cusip", "str", ("CUSIP",)),
    F("as_of_month", "derived"),
    F("program", "const", value="gn_mega"),
    F("pool_type", "str_cast", ("Prefix",)),
    F("structure_class", "const", value="mega"),
    F("is_resecuritization", "const", value=True),
    F("issue_date", "ymd", ("Issue Date",)),
    F("maturity_date", "ymd", ("Maturity Date",)),
    F(
        "original_face",
        "num",
        ("Issuance Investor  Security UPB", "Issuance Investor Security UPB"),
    ),
    F("current_face", "derived"),
    F("security_factor", "num", ("Security Factor",)),
    F("pass_through_rate", "num", ("WA Net Interest Rate",)),
    F("wac", "num", ("WA Current Interest Rate",)),
    F("wa_loan_age", "int32", ("WA Loan Age",)),
    F("wa_rem_term", "int32", ("WA Current Remaining Months to Maturity",)),
    F("record_source", "raw_str", ("_record_source",)),
    F("source_dataset", "const", value="GN_MEGA"),
]
