"""Builders for the combined cross-source master datasets.

Each builder assembles one master dataset from the per-source silver layers
(see ``config.DATASET_MAP`` for the source wiring). ``build_loan_issuance`` is
implemented for all three agencies — the two GSEs (FNMA SF Loan-Perf ⋈ FNM_ILLD,
FHLMC SF origination ⋈ FRE_ILLD, each via its mbs_umbs crosswalk) plus GNMA (a
single ``dailyllmni`` spine, L->P CUSIP attach), as are ``build_loan_performance``,
``build_loan_terminations``, ``build_loan_outcomes``, ``build_mbs_issuance``, and
``build_mbs_performance``.

Keep these intentionally few — this package builds a small curated set of
masters, not the full vendor-style aggregation catalog.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_issuance_schema import (
    DATE_FIELDS,
    IL_FIRST_FIELDS,
    FieldSpec,
)
from mortgage_data_manager.combined.loan_performance_schema import (
    EVENT_CLASS,
    GSE_NA_CODES,
    GSE_PERF_PLAN,
    GSE_TERM_PLAN,
    PERF_NA_CODE_COLUMNS,
    SF_DELINQUENCY_COL,
    UMBS_DAYS_DELINQUENT_COL,
    ZBC_CREDIT_EVENT_CODES,
    ZBC_EVENT_TYPE,
    PerfField,
)
from mortgage_data_manager.combined.mbs_performance_schema import (
    CANONICAL_MBS_PERFORMANCE_SCHEMA,
    GNMA_HMBS_POOL_TYPES,
    GNMA_PLATINUM_HMBS_INDICATOR,
    GNMA_PLATINUM_ISSUER_NUMBER,
    GSE_MIRROR_SELLER_PREFIX,
    GSE_RESEC_SELLER_PREFIX,
    GSE_TERMINAL_STATUS_INDICATORS,
    UMBS_ERA_START,
)
from mortgage_data_manager.core.amortization import scheduled_principal, smm_to_cpr
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.dates import (
    mmddyyyy_to_date,
    month_to_date,
    yyyymmdd_to_date,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import recreate_dir, should_process_file
from mortgage_data_manager.core.reference.gnma_pool_types import (
    GNMA_POOL_TYPES,
    GNMA_PROGRAM_BY_ISSUE_TYPE,
)

logger = get_logger(__name__)

# Valid Classic-FICO range; values outside (9999/7777/0/blank) are sentinels -> null.
_FICO_LO, _FICO_HI = 300, 850
# Underwriting-ratio sentinels (FNMA: 999 = NA; DTI also -99999 = Exempt).
_RATIO_SENTINELS = (999, -99999)
# Perf-panel sentinel/junk bounds (2026-07-09 schema audit). ELTV 999 = "not available"
# (42M FHLMC rows); 99.999 = the FU frontier-month rate placeholder (re-injected each
# incremental month until the real rate discloses); loan_age -1..-3 is the benign SF
# pre-first-payment convention but a FHLMC junk cluster sits at -859..-965; RMM junk
# reaches -840 (FNMA) and 967 (GNMA) against a 480-month product ceiling.
_ELTV_SENTINEL_FLOOR = 900
_RATE_SENTINEL = 99.999
_MI_PCT_SENTINEL = 999
_LOAN_AGE_FLOOR = -24
_RMM_LO, _RMM_HI = -120, 720
# UMBS-only issuance pass: hash shards so the per-shard dedup never holds the whole
# unmatched-loan universe (~6-8M loans x ~120 ILLD cols) in memory at once.
_UMBS_ONLY_SHARDS = 8
# mbs_performance: number of (agency, pool_id)-hash shards for the windowed
# derivation, so peak memory is ~1/N of the full pool-month panel (GNMA ~40M +
# FNMA ~43M + FHLMC; each pool's whole history hashes to one shard for any N).
_MBS_PERF_SHARDS = 16


def _count_rows(tree: Path) -> int:
    """Row count of a parquet tree from the file footers, summed in Python.

    ``scan_parquet(tree).select(pl.len())`` fails past 2**32 - 1 rows (polars'
    default row index is 32-bit); per-file counts never get near that.

    Returns:
        The total row count across the tree.
    """
    return sum(
        pl.scan_parquet(str(f)).select(pl.len()).collect().item() for f in tree.rglob("*.parquet")
    )


def _coalesce_present(aliases: list[str], available: set[str]) -> pl.Expr | None:
    """Coalesce the alias columns that actually exist in a frame (vintage-robust).

    Args:
        aliases: Candidate source column names (vintage renames OR-ed together).
        available: Column names present in the frame.

    Returns:
        A coalescing expression over the present aliases, or ``None`` if none exist.
    """
    present = [a for a in aliases if a in available]
    if not present:
        return None
    return pl.coalesce([pl.col(a) for a in present]) if len(present) > 1 else pl.col(present[0])


def _fico_clean(expr: pl.Expr) -> pl.Expr:
    """Null out credit-score sentinels, keeping only valid [300, 850].

    Casts to ``Float64`` first so it is robust to ILLD's String/Int dtype drift.

    Returns:
        A Float64 expression, null outside [300, 850].
    """
    num = expr.cast(pl.Float64, strict=False)
    return pl.when(num.is_between(_FICO_LO, _FICO_HI)).then(num).otherwise(None)


def _yn_bool(expr: pl.Expr) -> pl.Expr:
    """Normalize a yes/no indicator of ANY source representation to Boolean.

    The silver layers disagree on representation after the dtype migration: SF stores
    ``Boolean``, ILLD stores ``Enum('7'/'9'/'N'/'Y')`` (7/9 = not applicable / not
    available), GNMA stores ``Y``/``N`` strings. Casting through String makes one rule
    cover all: Y/true -> True, N/false -> False, everything else (7/9/blank) -> null.

    Returns:
        A Boolean expression, null for values outside the yes/no code space.
    """
    s = expr.cast(pl.String, strict=False).str.strip_chars()
    return (
        pl.when(s.is_in(["Y", "true"]))
        .then(pl.lit(value=True))
        .when(s.is_in(["N", "false"]))
        .then(pl.lit(value=False))
        .otherwise(None)
    )


def _flag_str(expr: pl.Expr) -> pl.Expr:
    """Normalize a flag column to String, mapping Boolean true/false back to Y/N.

    For flags whose code space is richer than yes/no (e.g. FHLMC ``Modification Flag``
    carries ``P`` = modified in a prior period), where Booleanizing would lose codes:
    FNMA's Boolean silver becomes Y/N and richer letter spaces pass through unchanged.

    Returns:
        A String expression carrying the flag's own code space.
    """
    return expr.cast(pl.String, strict=False).replace({"true": "Y", "false": "N"})


#: Multi-source yes/no indicators normalized to Boolean via ``_yn_bool`` — every column
#: here mixes at least two source representations (SF Boolean / ILLD Enum-with-7-or-9 /
#: GNMA Y-N strings), so leaving them to generic dtype supertyping would produce
#: "true"-vs-"Y" mixtures in the unified output.
_BOOL_INDICATOR_FIELDS: frozenset[str] = frozenset(
    {
        "first_time_homebuyer",
        "mi_cancellation_ind",
        "interest_only_ind",
        "prepayment_penalty_ind",
        "government_insured_guarantee",
    }
)

#: Fields forced to String at emit time because the per-agency source dtypes have NO
#: polars supertype (the tri-agency canon concat would fail): property_valuation_method
#: is an Enum of letters at FNMA vs Int8 codes at FHLMC (code spaces genuinely differ —
#: kept raw per agency, just consistently typed); delinquency_status is the SF status
#: string at FNMA vs the ILLD integer cycle count at FHLMC.
#:
#: The categoricals below are Enums on BOTH sides but over DIFFERENT category sets --
#: ILLD admits the disclosure sentinels the SF file never emits (channel ``9``/``T``,
#: loan purpose ``9``/``M``/``N``, occupancy ``9``). Two Enums with unequal categories
#: have no supertype either, so ``coalesce(SF, ILLD)`` raises
#: "failed to determine supertype of enum and enum". Stringifying both sides before the
#: coalesce keeps every code, including the sentinels, and the unified enum columns are
#: derived downstream anyway (``_purpose_letter_to_enum`` / ``_channel_to_enum``), so
#: nothing that consumers read loses its type.
_STRINGIFY_FIELDS: frozenset[str] = frozenset(
    {
        "property_valuation_method",
        "delinquency_status",
        "channel",
        "loan_purpose",
        "occupancy",
        "amortization_type",
        "property_type",
        "first_time_homebuyer",
        "mi_cancellation_ind",
    }
)


def _illd_month_int_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse an ILLD M(M)YYYY month integer (e.g. ``12016`` = 2016-01) to ``pl.Date``.

    The ILLD silver date-column curation misses a few fringe date fields, which arrive
    as raw M(M)YYYY ints; out-of-range month/year -> null.

    Returns:
        A ``pl.Date`` expression at the first of the month, null when out of range.
    """
    v = expr.cast(pl.Int64, strict=False)
    month, year = v // 10000, v % 10000
    return (
        pl.when(month.is_between(1, 12) & year.is_between(1990, 2100))
        .then(pl.date(year, month, 1))
        .otherwise(None)
    )


# --- shared categorical harmonization (§4.1 / §4.5) -------------------------
# loan_purpose and channel are the two cross-agency categoricals GNMA actually
# populates, so they are unified to a self-documenting enum in BOTH the GSE and
# GNMA paths (spec §4.1 calls loan_purpose "the central mismatch"). The raw
# agency code is always retained alongside in ``{field}_raw``. GSE-only
# categoricals (occupancy, property_type, ...) are left in their raw encoding —
# GNMA is null there, so re-coding them would add churn without cross-agency
# value.


def _purpose_letter_to_enum(expr: pl.Expr) -> pl.Expr:
    """Map the GSE letter loan-purpose (``P``/``C``/``R``) to the unified enum.

    The GSE assembler has already folded FHLMC ``N`` (rate-term) into ``R`` (D-1),
    so ``R`` is the rate-term/unspecified-refi bucket here.

    Returns:
        The unified loan-purpose enum expression.
    """
    return (
        pl.when(expr == "P")
        .then(pl.lit("purchase"))
        .when(expr == "C")
        .then(pl.lit("refi_cash_out"))
        .when(expr == "R")
        .then(pl.lit("refi_rate_term"))
        .otherwise(None)
    )


def _channel_to_enum(expr: pl.Expr, *, gnma: bool) -> pl.Expr:
    """Map a channel code to the unified enum.

    GSE uses letters (``R``/``C``/``B``/``T``); GNMA ``Third-Party Origination
    Type`` uses digits (``1``/``2``/``3``). GNMA ``3`` conflates retail and
    not-third-party origination → mapped to ``retail`` (§4.5, accepting the
    documented conflation).

    Returns:
        The unified channel enum expression.
    """
    if gnma:
        return (
            pl.when(expr == "1")
            .then(pl.lit("broker"))
            .when(expr == "2")
            .then(pl.lit("correspondent"))
            .when(expr == "3")
            .then(pl.lit("retail"))
            .otherwise(None)
        )
    return (
        pl.when(expr == "R")
        .then(pl.lit("retail"))
        .when(expr == "C")
        .then(pl.lit("correspondent"))
        .when(expr == "B")
        .then(pl.lit("broker"))
        .when(expr == "T")
        .then(pl.lit("tpo"))
        .otherwise(None)
    )


def _quarter_from_date(expr: pl.Expr) -> pl.Expr:
    """Format a date as ``YYYYQn``."""
    return (expr.dt.year().cast(pl.String) + pl.lit("Q") + expr.dt.quarter().cast(pl.String)).cast(
        pl.String
    )


def _fhlmc_quarter_from_key(expr: pl.Expr) -> pl.Expr:
    """Decode Freddie's origination quarter out of the loan sequence number.

    Freddie encodes it in the key itself -- ``F{YY}Q{n}{seq}``, so ``F21Q11275813``
    is a 2021 Q1 origination. This is the only origination-timing signal Freddie's SF
    disclosure carries (it publishes no origination date, unlike Fannie), and it is
    exact: the encoded quarter matches the source file's own origination quarter on
    100.00% of the 13,352,164 loans originated 2019-2024. The usual
    ``first_payment_date`` minus two months convention lands in the right quarter only
    98.6% of the time -- seasoned and late-delivered loans miss -- so the key wins
    wherever both exist.

    Returns:
        An expression yielding the origination quarter decoded from the key.
    """
    return (
        pl.when(expr.str.contains(r"^F\d{2}Q[1-4]"))
        .then(pl.lit("20") + expr.str.slice(1, 2) + pl.lit("Q") + expr.str.slice(4, 1))
        .otherwise(None)
        .cast(pl.String)
    )


def _assemble_loan_issuance(
    plan: list[FieldSpec],
    sf: pl.LazyFrame,
    umbs: pl.LazyFrame,
    crosswalk: pl.LazyFrame,
    *,
    agency: str,
    source_dataset: str,
    sf_key_col: str,
    loan_key_kind: str,
) -> pl.LazyFrame:
    """Fuse a GSE's SF base, its UMBS/ILLD enrichment, and the 1:1 crosswalk.

    Generic over agency: ``plan`` carries the per-GSE source→unified column map and
    drives all dispositions. Harmonization follows D-1..D-6 (see the schema module).
    The two GSEs differ only in SF key column and
    credit score (FNMA = ``min(borrower, co-borrower)``; FHLMC = single score). All
    date columns are already ``pl.Date`` in the SF and ILLD silver layers (parsed
    bronze->silver via ``core.dates``), so no date decoding happens here.

    Args:
        plan: The GSE field plan (``FNMA_``/``FHLMC_LOAN_ISSUANCE_PLAN``).
        sf: SF base LazyFrame (one row per loan).
        umbs: UMBS LazyFrame (ILLD ∪ snapshot) keyed on ``loan_key_umbs`` + ``umbs_source``.
        crosswalk: Frame with ``sf_id`` ↔ ``umbs_id`` (string, 1:1).
        agency: ``"FNMA"`` / ``"FHLMC"`` (literal ``agency`` column).
        source_dataset: Provenance literal for the ``source_dataset`` column.
        sf_key_col: The SF loan-id column name.
        loan_key_kind: Namespace label for the unified ``loan_key`` (§5).

    Returns:
        The assembled combined LazyFrame at the loan-issuance grain.
    """
    sf_names = set(sf.collect_schema().names())
    il_schema = umbs.collect_schema()
    il_names = set(il_schema.names())

    # --- SF side: emit base PK + every SF-sourced unified field ---
    sf_exprs: list[pl.Expr] = [pl.col(sf_key_col).cast(pl.String).alias("loan_key_sf")]
    for spec in plan:
        name, disp = spec["name"], spec["disposition"]
        if disp in {"drop", "key", "umbs_only"} or not spec["sf"]:
            continue
        expr = _coalesce_present(spec["sf"], sf_names)
        if expr is None:
            continue
        if name in _BOOL_INDICATOR_FIELDS:  # representation-mixed y/n -> Boolean
            expr = _yn_bool(expr)
        elif name in _STRINGIFY_FIELDS:  # per-agency dtypes with no polars supertype
            expr = expr.cast(pl.String, strict=False)
        # sf_only / shared_keep / shared_harmon all land under their unified name
        sf_exprs.append(expr.alias(name))
    sf_u = sf.select(sf_exprs)
    sf_u_schema = sf_u.collect_schema()  # unified-name -> dtype, for dtype-aligning ILLD fills

    # --- UMBS side: emit umbs PK + every ILLD-sourced unified field ---
    il_exprs: list[pl.Expr] = [pl.col("loan_key_umbs"), pl.col("umbs_source")]
    for spec in plan:
        name, disp = spec["name"], spec["disposition"]
        if disp in {"drop", "key", "sf_only"} or not spec["il"]:
            continue
        expr = _coalesce_present(spec["il"], il_names)
        if expr is None:
            continue
        il_dtype = il_schema[next(a for a in spec["il"] if a in il_names)]
        if name in _BOOL_INDICATOR_FIELDS:  # Boolean on BOTH sides before the coalesce
            expr = _yn_bool(expr)
        elif name in _STRINGIFY_FIELDS:  # per-agency dtypes with no polars supertype
            expr = expr.cast(pl.String, strict=False)
        elif name == "io_first_pi_date" and il_dtype != pl.Date:
            expr = _illd_month_int_to_date(expr)  # ILLD leaves this as a raw M(M)YYYY int
        elif name == "next_rate_adjustment_date" and il_dtype != pl.Date:
            continue  # ILLD int format unverified (not M(M)YYYY / YYDDD) -> take only Date
        if disp == "umbs_only":
            il_exprs.append(expr.alias(name))
        elif name == "representative_credit_score":
            il_exprs.append(_fico_clean(expr).alias("representative_credit_score__il"))
        elif name in {"first_payment_date", "maturity_date"}:  # already pl.Date in ILLD silver
            il_exprs.append(expr.alias(f"{name}__il"))
        elif name in {"delinquency_status", "loan_payment_history"}:
            il_exprs.append(expr.alias(f"{name}_umbs_raw"))
        else:  # shared_keep + mi_percent + loan_purpose -> __il helper for post-join resolve
            il_exprs.append(expr.alias(f"{name}__il"))
    il_u = umbs.select(il_exprs)

    # --- join: SF base -> crosswalk -> UMBS ---
    combined = (
        sf_u.join(crosswalk, left_on="loan_key_sf", right_on="sf_id", how="left")
        .join(il_u, left_on="umbs_id", right_on="loan_key_umbs", how="left")
        .rename({"umbs_id": "loan_key_umbs"})
    )

    # --- post-join resolution of shared / harmonized fields ---
    cols = set(combined.collect_schema().names())
    resolve: list[pl.Expr] = [pl.col("loan_key_umbs").is_not_null().alias("has_umbs_match")]
    for spec in plan:
        name, disp = spec["name"], spec["disposition"]
        if name == "representative_credit_score":
            continue  # handled explicitly below (FNMA min vs FHLMC single)
        helper = f"{name}__il"
        has_sf, has_il = name in cols, helper in cols
        # ILLD silver drifts dtype across months; align the fill to the SF dtype.
        il_fill = (
            pl.col(helper).cast(sf_u_schema[name], strict=False)
            if has_sf and has_il
            else pl.col(helper)
        )
        if disp == "shared_keep" or name in {"first_payment_date", "maturity_date"}:
            # SF canonical and ILLD fills its nulls -- EXCEPT IL_FIRST_FIELDS, where ILLD
            # is the higher-fidelity source (SF value-masks small sellers/servicers as
            # "Other"); there ILLD wins and SF fills ILLD's nulls (pre-2019-06 history).
            if has_sf and has_il:
                order = (
                    [il_fill, pl.col(name)] if name in IL_FIRST_FIELDS else [pl.col(name), il_fill]
                )
                resolve.append(pl.coalesce(order).alias(name))
            elif has_il:
                resolve.append(pl.col(helper).alias(name))
        elif name == "mi_percent":
            if has_sf and has_il:  # D-4: ILLD-first (explicit 0 = no MI; SF null != 0).
                resolve.append(pl.coalesce([il_fill, pl.col(name)]).alias("mi_percent"))
            elif has_il:
                resolve.append(pl.col(helper).cast(pl.Float64, strict=False).alias("mi_percent"))
    # D-1: unify refi rate-term to 'R' (SF 'N' for FHLMC, ILLD 'N'); 'M' -> separate flag.
    purpose_parts = [pl.col(c) for c in ("loan_purpose", "loan_purpose__il") if c in cols]
    if purpose_parts:
        raw_purpose = pl.coalesce(purpose_parts)
        resolve.append(
            pl.when(raw_purpose == "N")
            .then(pl.lit("R"))
            .otherwise(raw_purpose)
            .alias("loan_purpose")
        )
        modified = (
            (pl.col("loan_purpose__il") == "M") if "loan_purpose__il" in cols else pl.lit(False)
        )
        resolve.append(modified.alias("is_modified_reissue"))
    # D-3: representative score. FNMA = min(SF borrower, co-borrower); FHLMC = single SF
    # score. ILLD's representative backfills unmatched loans either way.
    rep_il = (
        pl.col("representative_credit_score__il")
        if "representative_credit_score__il" in cols
        else pl.lit(None)
    )
    if {"borrower_credit_score", "co_borrower_credit_score"} <= cols:
        rep_base = pl.min_horizontal(
            _fico_clean(pl.col("borrower_credit_score")),
            _fico_clean(pl.col("co_borrower_credit_score")),
        )
    elif "representative_credit_score" in cols:
        rep_base = _fico_clean(pl.col("representative_credit_score"))
    else:
        rep_base = pl.lit(None)
    resolve.append(pl.coalesce([rep_base, rep_il]).alias("representative_credit_score"))
    combined = combined.with_columns(resolve)

    # Second pass: sentinel scrubs (kept separate so a scrubbed column is not also
    # produced by the shared-keep coalesce in the same with_columns).
    scrub: list[pl.Expr] = [
        _fico_clean(pl.col(c)).alias(c)
        for c in ("borrower_credit_score", "co_borrower_credit_score")
        if c in cols
    ]
    scrub += [
        pl.when(pl.col(r).is_in(_RATIO_SENTINELS)).then(None).otherwise(pl.col(r)).alias(r)
        for r in ("ltv", "cltv", "dti")
        if r in cols
    ]
    # (the '7'/'9' not-available codes are handled at emit time: _yn_bool nulls them for
    # the Boolean indicators, and the SF Enum silver no longer carries them for channel.)
    if scrub:
        combined = combined.with_columns(scrub)

    # --- provenance + partition key + harmonized categoricals + final order ---
    post_cols = set(combined.collect_schema().names())
    year_exprs = [
        pl.col(c).dt.year() for c in ("origination_date", "first_payment_date") if c in post_cols
    ]
    prov: list[pl.Expr] = [
        pl.lit(agency).alias("agency"),
        pl.lit(source_dataset).alias("source_dataset"),
        (pl.coalesce(year_exprs) if year_exprs else pl.lit(None)).alias("issuance_year"),
        # Unified loan key = the SF base PK (the loan-of-record spine); kind names
        # its namespace (§5). loan_key_umbs stays as the pool-spine cross-ref.
        pl.col("loan_key_sf").alias("loan_key"),
        pl.lit(loan_key_kind).alias("loan_key_kind"),
    ]
    # Origination quarter, unified across GSEs: Fannie derives it from its disclosed
    # origination date, Freddie from the loan key (which is exact where Freddie has no
    # date at all -- see _fhlmc_quarter_from_key).
    quarter_exprs: list[pl.Expr] = []
    if agency == "FHLMC":
        quarter_exprs.append(_fhlmc_quarter_from_key(pl.col("loan_key_sf")))
    if "origination_date" in post_cols:
        quarter_exprs.append(_quarter_from_date(pl.col("origination_date")))
    prov.append(
        (pl.coalesce(quarter_exprs) if quarter_exprs else pl.lit(None, dtype=pl.String)).alias(
            "orig_quarter"
        )
    )
    if "loan_purpose" in post_cols:  # retain raw letter, expose unified enum (§4.1)
        prov.append(pl.col("loan_purpose").alias("loan_purpose_raw"))
        prov.append(_purpose_letter_to_enum(pl.col("loan_purpose")).alias("loan_purpose"))
    if "channel" in post_cols:  # retain raw letter, expose unified enum (§4.5)
        prov.append(pl.col("channel").alias("channel_raw"))
        prov.append(_channel_to_enum(pl.col("channel"), gnma=False).alias("channel"))
    combined = combined.with_columns(prov)

    final: list[str] = [
        "loan_key",
        "loan_key_kind",
        "loan_key_sf",
        "loan_key_umbs",
        "has_umbs_match",
        "agency",
        "source_dataset",
        "umbs_source",
        "issuance_year",
        "orig_quarter",
    ]
    for spec in plan:
        if spec["disposition"] in {"drop", "key"}:
            continue
        final.append(spec["name"])
    final += [
        "loan_purpose_raw",
        "channel_raw",
        "is_modified_reissue",
        "delinquency_status_umbs_raw",
        "loan_payment_history_umbs_raw",
    ]
    present = combined.collect_schema().names()
    ordered = list(dict.fromkeys(c for c in final if c in present))
    return combined.select(ordered)


def _assemble_umbs_only_loan_issuance(
    plan: list[FieldSpec], umbs: pl.LazyFrame, *, agency: str, source_dataset: str
) -> pl.LazyFrame:
    """Assemble ILLD/snapshot-sourced issuance rows for loans with NO SF twin.

    The umbs-only analog of `_assemble_loan_issuance`: loans absent from the
    mbs_umbs crosswalk (Freddie modification reissues in the disjoint FU id namespace,
    ARMs/relief-refis outside the standard SF products, residual crosswalk misses, and
    fresh issuances ahead of the SF release frontier) get an issuance row keyed by their
    UMBS id — ``loan_key_kind='umbs_seq'``, matching the perf panel's umbs_seq rows so
    statics join by ``loan_key`` for the whole panel. Only il-sourced plan fields
    populate; SF-only columns stay null via the canon reindex. ``has_umbs_match`` is
    ``False`` (its meaning stays "a crosswalked SF↔UMBS pair exists").

    Args:
        plan: The GSE field plan (drives the il-source column map).
        umbs: One deduped shard of the materialized UMBS side (anti-joined vs crosswalk).
        agency: ``"FNMA"`` / ``"FHLMC"``.
        source_dataset: Provenance literal (``{gse}_umbs_only``).

    Returns:
        The assembled LazyFrame at the loan-issuance grain (pre-reindex).
    """
    il_schema = umbs.collect_schema()
    il_names = set(il_schema.names())
    exprs: list[pl.Expr] = [pl.col("loan_key_umbs"), pl.col("umbs_source")]
    for spec in plan:
        name, disp = spec["name"], spec["disposition"]
        if disp in {"drop", "key"} or not spec["il"]:
            continue
        expr = _coalesce_present(spec["il"], il_names)
        if expr is None:
            continue
        il_dtype = il_schema[next(a for a in spec["il"] if a in il_names)]
        # same dtype/sentinel normalizations as the SF-path il side
        if name in _BOOL_INDICATOR_FIELDS:  # representation-mixed y/n -> Boolean (7/9 -> null)
            expr = _yn_bool(expr)
        elif name in _STRINGIFY_FIELDS:
            expr = expr.cast(pl.String, strict=False)
        elif name == "representative_credit_score":
            expr = _fico_clean(expr)
        elif name == "mi_percent":
            expr = expr.cast(pl.Float64, strict=False)
        elif name in DATE_FIELDS:  # defensive vs ILLD String/Date dtype drift
            expr = expr.cast(pl.Date, strict=False)
        elif name == "io_first_pi_date" and il_dtype != pl.Date:
            expr = _illd_month_int_to_date(expr)  # raw M(M)YYYY int in ILLD silver
        elif name == "next_rate_adjustment_date" and il_dtype != pl.Date:
            continue  # unverified int format -> take only Date (matches the SF path)
        # unified name mirrors the SF path's dispositions: umbs_only fields land under
        # their own name; the two shared_harmon delinquency fields keep the _umbs_raw
        # helper name so FNMA rows stay consistent with the crosswalked spine.
        if disp != "umbs_only" and name in {"delinquency_status", "loan_payment_history"}:
            exprs.append(expr.alias(f"{name}_umbs_raw"))
        else:
            exprs.append(expr.alias(name))
    out = umbs.select(exprs)
    cols = set(out.collect_schema().names())

    # Same scrubs as the SF path: ratio sentinels, plus the ILLD channel '9' = not
    # available (scrubbed before the prov pass so channel_raw is born clean; the
    # cast-through-String condition is Enum-safe).
    scrub: list[pl.Expr] = [
        pl.when(pl.col(r).is_in(_RATIO_SENTINELS)).then(None).otherwise(pl.col(r)).alias(r)
        for r in ("ltv", "cltv", "dti")
        if r in cols
    ]
    if "channel" in cols:
        scrub.append(
            pl.when(pl.col("channel").cast(pl.String, strict=False).is_in(list(GSE_NA_CODES)))
            .then(None)
            .otherwise(pl.col("channel"))
            .alias("channel")
        )
    if scrub:
        out = out.with_columns(scrub)

    out_schema = out.collect_schema()
    # orig_fpd_restated is an unparsed int in ILLD silver -> only Date-typed fallbacks.
    # ILLD carries a handful of junk FPDs (years out to 2304) -> clamp so no junk hive
    # partition is created; out-of-window rows are dropped (and logged) by the caller.
    year_exprs = [
        pl.col(c).dt.year().pipe(lambda y: pl.when(y.is_between(1990, 2035)).then(y))
        for c in ("first_payment_date", "orig_fpd_restated")
        if out_schema.get(c) == pl.Date
    ]
    prov: list[pl.Expr] = [
        pl.lit(agency).alias("agency"),
        pl.lit(source_dataset).alias("source_dataset"),
        (pl.coalesce(year_exprs) if year_exprs else pl.lit(None)).alias("issuance_year"),
        pl.col("loan_key_umbs").alias("loan_key"),
        pl.lit("umbs_seq").alias("loan_key_kind"),
        pl.lit(value=False).alias("has_umbs_match"),
    ]
    if "loan_purpose" in cols:  # D-1 fold + enum + M flag, mirroring the SF path
        purpose_str = pl.col("loan_purpose").cast(pl.String, strict=False)  # Enum-safe
        folded = pl.when(purpose_str == "N").then(pl.lit("R")).otherwise(purpose_str)
        prov.append(folded.alias("loan_purpose_raw"))
        prov.append(_purpose_letter_to_enum(folded).alias("loan_purpose"))
        prov.append(
            (pl.col("loan_purpose") == "M").fill_null(value=False).alias("is_modified_reissue")
        )
    if "channel" in cols:
        prov.append(pl.col("channel").alias("channel_raw"))
        prov.append(_channel_to_enum(pl.col("channel"), gnma=False).alias("channel"))
    return out.with_columns(prov)


@dataclass(frozen=True)
class _GseSpec:
    """Per-GSE wiring for the loan-issuance build."""

    agency: str  # "FNMA" / "FHLMC"
    source_dataset: str  # provenance literal
    plan: list[FieldSpec]  # field plan for this GSE
    sf_dir: Path  # SF base directory (one parquet per quarter / vintage year)
    illd_dir: Path  # UMBS ILLD silver directory
    crosswalk_file: Path  # mbs_umbs crosswalk for this GSE
    snapshot_gse: str  # MBSUMBSMatchingConfig.get_umbs_snapshot_file() key ("fnma"/"fhlmc")
    sf_key_col: str  # SF loan-id column name
    loan_key_kind: str  # namespace of the unified loan_key (§5)
    #: SF releases published outside `sf_dir`, as (file, source_dataset) pairs. Fannie's
    #: HARP population is one such file, and it cannot simply widen `sf_dir`'s glob: the
    #: harp directory also holds the performance/terminations/loan_mapping siblings.
    sf_extra: tuple[tuple[Path, str], ...] = ()


def _sf_sources(spec: _GseSpec) -> list[tuple[Path, str]]:
    """Every SF file feeding a GSE's issuance build, with each file's provenance tag.

    Args:
        spec: The GSE spec.

    Returns:
        ``(file, source_dataset)`` pairs — the `sf_dir` glob at the spec's own tag,
        followed by any separately-published releases at theirs.
    """
    return [(f, spec.source_dataset) for f in sorted(spec.sf_dir.glob("**/*.parquet"))] + [
        (f, tag) for f, tag in spec.sf_extra if f.exists()
    ]


def _gse_specs() -> list[_GseSpec]:
    """Build the FNMA + FHLMC loan-issuance specs (paths resolved at call time)."""
    from mortgage_data_manager.combined.loan_issuance_schema import (
        FHLMC_LOAN_ISSUANCE_PLAN,
        FNMA_LOAN_ISSUANCE_PLAN,
    )

    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
    cw = MortgageDataConfig.CROSSWALK_DIR / "mbs_umbs"
    return [
        _GseSpec(
            agency="FNMA",
            source_dataset="fnma_sf_perf",
            plan=FNMA_LOAN_ISSUANCE_PLAN,
            sf_dir=MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances",
            illd_dir=umbs / "FNMA" / "ILLD",
            crosswalk_file=cw / "mbs_umbs_crosswalk_fnma.parquet",
            snapshot_gse="fnma",
            sf_key_col="Loan Identifier",
            loan_key_kind="gse_numeric",
            sf_extra=(
                (
                    MortgageDataConfig.get_subpackage_data_dir("fnma")
                    / "silver"
                    / "harp"
                    / "issuances.parquet",
                    "gse_harp_disclosure",
                ),
            ),
        ),
        _GseSpec(
            agency="FHLMC",
            source_dataset="fhlmc_sf_orig",
            plan=FHLMC_LOAN_ISSUANCE_PLAN,
            sf_dir=MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "silver" / "origination",
            illd_dir=umbs / "FHLMC" / "ILLD",
            crosswalk_file=cw / "mbs_umbs_crosswalk_fhlmc.parquet",
            snapshot_gse="fhlmc",
            sf_key_col="Loan Sequence Number",
            loan_key_kind="sf_seq",
        ),
    ]


#: Rank of each UMBS disclosure of one loan, lower wins (`_one_umbs_row_per_loan`). An
#: original ILLD row is the issuance disclosure. The first monthly snapshot comes next:
#: it predates every purpose-M reissue and, for a loan whose only ILLD rows are
#: reissues, its primary fields equal the reissue's own ``Origination *`` fields on 93%
#: of loans (the rest were already modified once by the snapshot month), so it is the
#: disclosure closest to origination. A purpose-M ILLD row re-discloses an already-issued
#: loan after a modification, with post-modification amount, rate, FPD and term in the
#: primary fields and LTV / credit score / channel left empty, so it ranks last.
_UMBS_RANK_ILLD = 0
_UMBS_RANK_SNAPSHOT = 1
_UMBS_RANK_MODIFICATION = 2


def _materialize_umbs(spec: _GseSpec, dest: Path) -> None:
    """Materialize a GSE's UMBS side (ILLD silver + first monthly snapshot) to parquet.

    Streams the concatenation to ``dest`` WITHOUT a global dedup (a blocking
    ``unique`` over ~28M rows OOMs on a small machine). Per-loan dedup — by disclosure
    rank, see `_one_umbs_row_per_loan` — is done cheaply per chunk in the builder,
    after the crosswalk has narrowed each chunk to ~1 quarter of loans.

    ILLD silver drifts dtype across months (e.g. ``Index`` String vs Int64), so the
    per-file scans are concatenated ``diagonal_relaxed`` (column union + supertypes).

    Args:
        spec: The GSE spec (plan + ILLD dir + snapshot key).
        dest: Output parquet path (single file).
    """
    from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig

    il_src_cols = sorted({c for fs in spec.plan for c in fs["il"]})

    def _select_one(path: str, label: str, priority: int) -> pl.LazyFrame:
        scan = pl.scan_parquet(path)
        schema = scan.collect_schema()
        names = set(schema.names())
        sel = [pl.col("Loan Identifier").cast(pl.String).alias("loan_key_umbs")]
        for c in il_src_cols:
            if c not in names or c == "Loan Identifier":
                continue
            # The FNM_MLLD snapshot stores its primary dates as raw M(M)YYYY ints while
            # ILLD silver is pl.Date; left mixed, the diagonal_relaxed concat supertypes
            # the whole column to Int64 (Date degrades to epoch-days) and every date
            # derived downstream is garbage. Normalize to Date at selection time.
            if c in ("First Payment Date", "Maturity Date") and schema[c].is_integer():
                sel.append(_illd_month_int_to_date(pl.col(c)).alias(c))
            else:
                sel.append(pl.col(c))
        rank = pl.lit(priority, dtype=pl.Int8)
        if label == "illd" and "Loan Purpose" in names:
            # A purpose-M row re-discloses an already-issued loan after a modification;
            # the loan's origination facts belong to its original disclosure.
            rank = (
                pl.when(pl.col("Loan Purpose").cast(pl.String) == "M")
                .then(pl.lit(_UMBS_RANK_MODIFICATION, dtype=pl.Int8))
                .otherwise(rank)
            )
        return scan.select(*sel, pl.lit(label).alias("umbs_source"), rank.alias("_umbs_priority"))

    frames = [
        _select_one(str(p), "illd", _UMBS_RANK_ILLD)
        for p in sorted(spec.illd_dir.rglob("*.parquet"))
    ]
    snapshot_file = MBSUMBSMatchingConfig.get_umbs_snapshot_file(spec.snapshot_gse)  # D-6 recovery
    if snapshot_file is not None:
        frames.append(_select_one(str(snapshot_file), "snapshot", _UMBS_RANK_SNAPSHOT))
    else:
        logger.warning(
            "No %s snapshot found; pre-2019-06 UMBS coverage will be missing.", spec.agency
        )
    pl.concat(frames, how="diagonal_relaxed").sink_parquet(dest)


def _reindex_to_schema(df: pl.DataFrame, schema: pl.Schema) -> pl.DataFrame:
    """Conform a chunk to a canonical schema so all written files are identical.

    Per-quarter chunks carry only their vintage's columns; this adds any missing
    columns as typed nulls, casts present ones to the canonical dtype, and orders
    them, so a single ``scan_parquet`` over the whole dataset succeeds.

    Args:
        df: The assembled chunk.
        schema: Canonical output schema (the cross-vintage union).

    Returns:
        The chunk with exactly ``schema``'s columns, dtypes, and order.
    """
    have = set(df.columns)
    return df.select(
        [
            pl.col(name).cast(dtype, strict=False)
            if name in have
            else pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in schema.items()
        ]
    )


def _write_chunk(df: pl.DataFrame, out_dir: Path, agency: str, tag: str) -> int:
    """Write one quarter's combined rows into the ``agency=/issuance_year=`` hive layout.

    Args:
        df: Assembled chunk (carries an ``issuance_year`` column).
        out_dir: Dataset root.
        agency: Agency partition value.
        tag: Quarter label used as the file stem (unique per agency × quarter).

    Returns:
        Number of rows written.
    """
    if df.is_empty():
        return 0
    for key, part in df.partition_by("issuance_year", as_dict=True).items():
        year = key[0] if isinstance(key, tuple) else key
        part_dir = out_dir / f"agency={agency}" / f"issuance_year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part.drop("agency", "issuance_year").write_parquet(part_dir / f"{tag}.parquet")
    return df.height


def _gse_crosswalk(spec: _GseSpec) -> pl.LazyFrame:
    """Scan a GSE crosswalk as ``sf_id`` ↔ ``umbs_id`` (both string)."""
    return pl.scan_parquet(spec.crosswalk_file).select(
        pl.col("Loan Sequence Number (MBS)").cast(pl.String).alias("sf_id"),
        pl.col("Loan Sequence Number (UMBS)").cast(pl.String).alias("umbs_id"),
    )


def _gse_canon_schema(spec: _GseSpec, tmp_umbs: Path) -> pl.Schema:
    """Resolve a GSE's full output schema (schema-only; no data read).

    Uses the cross-vintage SF column union so the schema includes every vintage's
    columns (a single glob scan would anchor on the first file and drop late ones).

    Returns:
        That GSE's full output schema.
    """
    sf_files = [f for f, _ in _sf_sources(spec)]
    sf_union = pl.concat([pl.scan_parquet(str(f)) for f in sf_files], how="diagonal_relaxed")
    return _assemble_loan_issuance(
        spec.plan,
        sf_union,
        pl.scan_parquet(tmp_umbs),
        _gse_crosswalk(spec),
        agency=spec.agency,
        source_dataset=spec.source_dataset,
        sf_key_col=spec.sf_key_col,
        loan_key_kind=spec.loan_key_kind,
    ).collect_schema()


def _one_umbs_row_per_loan(lf: pl.LazyFrame) -> pl.LazyFrame:
    """One UMBS row per loan: its best-ranked disclosure, or none when those disagree.

    ``_umbs_priority`` ranks an original ILLD disclosure above the first monthly snapshot
    above a purpose-M modification reissue of the same loan. Byte-identical rows at a
    loan's best rank collapse to one. A loan whose best-ranked rows still differ (FNMA
    discloses some loans in two securities) is excluded rather than given either
    disclosure's values: which one is right is unknowable.

    Args:
        lf: Materialized UMBS rows (``loan_key_umbs``, ``_umbs_priority``, ILLD columns).

    Returns:
        At most one row per ``loan_key_umbs``.
    """
    best = pl.col("_umbs_priority") == pl.col("_umbs_priority").min().over("loan_key_umbs")
    return lf.filter(best).unique().unique(subset=["loan_key_umbs"], keep="none")


def _build_gse_loan_issuance(
    spec: _GseSpec, out_dir: Path, canon: pl.Schema, tmp_umbs: Path
) -> int:
    """Build one GSE's loan-issuance rows, chunked by SF file, into ``out_dir``.

    Each SF file's loans narrow the crosswalk and the UMBS side to the same loans
    before joining, so every join's build side and the per-loan dedup stay small.
    A final sharded anti-join pass emits ``umbs_seq`` rows for UMBS-only loans (no
    crosswalk match — mod reissues, ARMs, fresh frontier), keyed to match the perf
    panel. Chunks are reindexed to ``canon`` (the cross-GSE union) so the whole
    dataset scans as one uniform table.

    Args:
        spec: The GSE spec.
        out_dir: Dataset root.
        canon: Canonical (cross-GSE) output schema.
        tmp_umbs: Path to this GSE's materialized UMBS parquet.

    Returns:
        Rows written for this GSE.
    """
    crosswalk = _gse_crosswalk(spec)
    sf_sources = _sf_sources(spec)
    logger.info("  %s: %d SF files", spec.agency, len(sf_sources))
    total = 0
    for i, (sf_file, source_dataset) in enumerate(sf_sources):
        # Index-based tag: SF filenames are not unique across partitions (every
        # FHLMC vintage file is `data.parquet`), which would collide in shared
        # issuance_year dirs and overwrite rows.
        tag = f"part{i:03d}"
        sf_chunk = pl.scan_parquet(str(sf_file))
        sf_ids = sf_chunk.select(pl.col(spec.sf_key_col).cast(pl.String).alias("sf_id"))
        cw_chunk = crosswalk.join(sf_ids, on="sf_id", how="semi")
        umbs_ids = cw_chunk.select("umbs_id")
        umbs_chunk = _one_umbs_row_per_loan(
            pl.scan_parquet(tmp_umbs).join(
                umbs_ids, left_on="loan_key_umbs", right_on="umbs_id", how="semi"
            )
        )
        chunk = _assemble_loan_issuance(
            spec.plan,
            sf_chunk,
            umbs_chunk,
            cw_chunk,
            agency=spec.agency,
            source_dataset=source_dataset,
            sf_key_col=spec.sf_key_col,
            loan_key_kind=spec.loan_key_kind,
        ).collect(engine="streaming")
        n = _write_chunk(_reindex_to_schema(chunk, canon), out_dir, spec.agency, tag)
        total += n
        logger.debug("    %s/%s (%s): %d rows", spec.agency, tag, sf_file.stem, n)

    # UMBS-only loans (no crosswalk match -> no SF row anywhere) get umbs_seq issuance
    # rows so the perf panel's umbs_seq rows can join statics by loan_key. Sharded by
    # loan-id hash so each shard's anti-join + dedup stays bounded (~1M loans/shard).
    cw_umbs_ids = crosswalk.select("umbs_id").unique()
    umbs_only_total = 0
    for i in range(_UMBS_ONLY_SHARDS):
        shard = _one_umbs_row_per_loan(
            pl.scan_parquet(tmp_umbs)
            .join(cw_umbs_ids, left_on="loan_key_umbs", right_on="umbs_id", how="anti")
            .filter(pl.col("loan_key_umbs").hash(seed=0) % _UMBS_ONLY_SHARDS == i)
        )
        chunk = _assemble_umbs_only_loan_issuance(
            spec.plan,
            shard,
            agency=spec.agency,
            source_dataset=f"{spec.agency.lower()}_umbs_only",
        ).collect(engine="streaming")
        no_year = chunk.filter(pl.col("issuance_year").is_null()).height
        if no_year:  # keeps the hive partition key clean; expected ~0
            logger.warning(
                "  %s umbs-only shard %d: dropped %d rows with no derivable issuance year",
                spec.agency,
                i,
                no_year,
            )
            chunk = chunk.filter(pl.col("issuance_year").is_not_null())
        n = _write_chunk(_reindex_to_schema(chunk, canon), out_dir, spec.agency, f"umbsonly{i:02d}")
        umbs_only_total += n
        total += n
    logger.info("  %s: %d umbs-only (umbs_seq) issuance rows", spec.agency, umbs_only_total)
    return total


# ===================== GNMA loan_issuance (single dailyllmni spine) =========
# GNMA is NOT a two-spine fusion: dailyllmni carries one loan-level row per new
# issuance (the loan as it appeared at pool issuance), so there is no SF/UMBS
# crosswalk join. Each monthly L file IS one issuance month; pool-level CUSIP /
# program / pool_type are attached from the same file's P record (L->P on Pool
# ID, §5, validated 100%). All GNMA silver fields are raw fixed-point strings and
# must be scaled manually (§6). Open questions cleared with the spec's
# recommended defaults: #2 (Ginnie I/II both flow through dailyllmni, split by
# the P-record Issue Type -> ``program``), #4 (purpose from the (Loan Purpose,
# Refinance Type) pair), #5 (gov MIP kept separate from GSE private mi_percent),
# #6 (TPO 3 -> retail), #7 (ARM iff index non-blank or margin>0), #8 (LTV/CLTV/
# DTI 0/blank -> null). GNMA Loan Purpose 3/4/5 are non-purchase/non-refi and the
# exact 3/4/5 labels are not authoritatively documented (sources disagree), so
# 3 -> construction (original V1.0 code) and 4/5 -> other, with the raw digit
# retained in ``loan_purpose_raw`` for lossless downstream reclassification.

#: GNMA dailyllmni L-record source columns (long descriptive silver names).
_G: dict[str, str] = {
    "pool_id": "Pool ID",
    "dsn": "Disclosure Sequence Number (A sequence number unique to loan level)",
    "issuer_id": "Issuer ID (including for loan packages in MIP pool)",
    "agency_loan_type": "Agency (Agency Loan Type FHA, VA, RD, NA)",
    "purpose": "Loan Purpose",
    "refi_type": "Refinance Type",
    "first_payment": "First Payment Date (First Scheduled Installment)",
    "maturity": "Maturity Date of Loan (Last Scheduled Installment)",
    "orig_date": "Loan Origination Date",
    "rate": "Loan Interest Rate (current interest rate)",
    "opb": "Original Principal Balance (OPB at pool issuance)",
    "upb_issuance": "UPB at Issuance (UPB of the loan at pool issuance)",
    "term": "Original Loan Term, in Months",
    "age": "Loan Age, in Months",
    "rmm": "Remaining Loan Term (Remaining Maturity), in Months",
    "margin": "Loan Gross Margin (ARM Loans only)",
    "ltv": "Loan To Value (LTV)",
    "cltv": "Combined LTV (CLTV)",
    "dti": "Total Debt Expense Ratio Percent",
    "credit_score": "Credit Score",
    "dpa": "Down Payment Assistance (Yes or No)",
    "buydown": "Buy Down Status (Loan Status) (Yes or No)",
    "upfront_mip": "Upfront MIP (insurance premium rate)",
    "annual_mip": "Annual MIP (insurance premium rate)",
    "num_borrowers": "Number of Borrowers",
    "fthb": "First Time Home Buyer (Yes or No)",
    "units": "Property Type (Number of Living Units)",
    "state": "State (2 character State Code)",
    "msa": "MSA",
    "tpo": "Third-Party Origination Type",
    "seller_issuer_id": "Seller Issuer ID",
    "index_type": "Index Type",
}

#: Same L fields keyed to their canonical-schema snake_case ids
#: so this builder reads either legacy verbose
#: silver or canonical silver — column resolution, date typing, and fixed-point decode are
#: all canonical-safe (GNMA silver is canonical/decoded as of the 2026-07 rebuild).
_G_CANONICAL: dict[str, str] = {
    "pool_id": "pool_id",
    "dsn": "disclosure_sequence_number",
    "issuer_id": "issuer_id",
    "agency_loan_type": "agency",
    "purpose": "loan_purpose",
    "refi_type": "refinance_type",
    "first_payment": "first_payment_date",
    "maturity": "maturity_date_of_loan",
    "orig_date": "loan_origination_date",
    "rate": "loan_interest_rate",
    "opb": "original_principal_balance",
    "upb_issuance": "upb_at_issuance",
    "term": "original_loan_term_in_months",
    "age": "loan_age_in_months",
    "rmm": "remaining_loan_term_in_months",
    "margin": "loan_gross_margin",
    "ltv": "loan_to_value",
    "cltv": "combined_ltv",
    "dti": "total_debt_expense_ratio_percent",
    "credit_score": "credit_score",
    "dpa": "down_payment_assistance",
    "buydown": "buy_down_status",
    "upfront_mip": "upfront_mip",
    "annual_mip": "annual_mip",
    "num_borrowers": "number_of_borrowers",
    "fthb": "first_time_home_buyer",
    "units": "property_type",
    "state": "state",
    "msa": "msa",
    "tpo": "third_party_origination_type",
    "seller_issuer_id": "seller_issuer_id",
    "index_type": "index_type",
}


def _g_norm(name: str) -> str:
    """Normalize a column name (drop all whitespace, lowercase) for robust matching.

    Pre-2021 GNMA files carry whitespace drift anywhere in the name (e.g. the
    ``Disclosure Sequence Number (...level )`` space before the paren, or
    ``Record Type L=...`` vs ``L = ...``), so all whitespace is removed before
    matching the canonical ``_G`` names — the descriptive phrases stay distinct.

    Returns:
        The whitespace-stripped, lower-cased name.
    """
    return "".join(name.split()).lower()


def _g_raw(key: str, lookup: dict[str, str], schema: pl.Schema) -> pl.Expr | None:
    """Resolve a GNMA L field to a trimmed-string expr, or None if absent this vintage.

    Reads either legacy verbose silver or canonical snake_case silver: tries
    the whitespace-normalized verbose name,
    then the canonical id. Canonical silver is already typed, so a ``date32`` column is
    rendered back to the ``YYYYMMDD`` string the date/decode helpers expect; numeric columns
    stringify to the same tokens the legacy string silver carried (so the downstream helpers
    are unchanged — including the still-pending fixed-point decode).

    Late-added fields (origination date V1.6+, seller issuer id V1.6+, index type
    V1.7+) are simply missing from early schemas — callers null-fill via the
    ``_opt_*`` / scaling helpers, which all accept ``None``.

    Returns:
        A trimmed-string expression, or None when the field is absent this vintage.
    """
    actual = lookup.get(_g_norm(_G[key])) or lookup.get(_G_CANONICAL.get(key, ""))
    if actual is None:
        return None
    if schema[actual] == pl.Date:  # canonical typed date -> YYYYMMDD string for the helpers
        return pl.col(actual).dt.strftime("%Y%m%d")
    return pl.col(actual).cast(pl.String).str.strip_chars()


def _g_blank_to_null(expr: pl.Expr) -> pl.Expr:
    """Map empty strings to null."""
    return pl.when(expr.str.len_chars() == 0).then(None).otherwise(expr)


def _opt_str(expr: pl.Expr | None) -> pl.Expr:
    """A blank-stripped String expr, or a typed null if the source column is absent."""
    return _g_blank_to_null(expr) if expr is not None else pl.lit(None, dtype=pl.String)


def _opt_int(expr: pl.Expr | None) -> pl.Expr:
    """An Int64 expr (sentinels -> null), or a typed null if absent."""
    return expr.cast(pl.Int64, strict=False) if expr is not None else pl.lit(None, dtype=pl.Int64)


# All GNMA silver is canonical (decoded at build): rates arrive as
# real percent, UPB/OPB as real USD, LTV/CLTV/DTI as real percent, and packed dates as pl.Date.
# The `_dec` helpers below take those already-decoded values and only map 0/blank -> null (no
# scaling).
def _gnma_rate_dec(expr: pl.Expr | None) -> pl.Expr:
    """A rate from CANONICAL (already-decoded) silver as percent; 0/blank -> null (no scaling)."""
    if expr is None:
        return pl.lit(None, dtype=pl.Float64)
    v = expr.cast(pl.Float64, strict=False)
    return pl.when(v > 0).then(v).otherwise(None)


def _gnma_ratio_dec(expr: pl.Expr | None) -> pl.Expr:
    """An LTV/CLTV/DTI from CANONICAL silver as int percent; 0/blank -> null (no scaling)."""
    if expr is None:
        return pl.lit(None, dtype=pl.Int64)
    v = expr.cast(pl.Float64, strict=False)
    return pl.when(v > 0).then(v.round(0).cast(pl.Int64)).otherwise(None)


def _gnma_upb_dec(expr: pl.Expr | None) -> pl.Expr:
    """A UPB/OPB from CANONICAL silver in USD; 0/blank -> null (no scaling)."""
    if expr is None:
        return pl.lit(None, dtype=pl.Float64)
    v = expr.cast(pl.Float64, strict=False)
    return pl.when(v > 0).then(v).otherwise(None)


def _gnma_date(expr: pl.Expr | None) -> pl.Expr:
    """Parse a GNMA CCYYMMDD date string to ``pl.Date``; blank/sentinel/absent -> null."""
    if expr is None:
        return pl.lit(None, dtype=pl.Date)
    return yyyymmdd_to_date(expr)


def _gnma_purpose(purpose: pl.Expr, refi: pl.Expr | None) -> pl.Expr:
    """Unify GNMA (Loan Purpose, Refinance Type) to the loan_purpose enum (§4.1).

    Codes per Ginnie's loan-level layout, identical in every version V1.0-V1.8: Loan Purpose
    1 purchase, 2 refinance, 3 / 4 loan modification (HAMP / non-HAMP), 5 re-performing;
    Refinance Type 1 not streamlined and not cash-out, 2 cash-out, 3 streamlined.
    """
    refi_e = refi if refi is not None else pl.lit(None, dtype=pl.String)
    is_refi = purpose == "2"
    return (
        pl.when(purpose == "1")
        .then(pl.lit("purchase"))
        .when(is_refi & (refi_e == "2"))
        .then(pl.lit("refi_cash_out"))
        .when(is_refi & refi_e.is_in(["1", "3"]))  # a streamline takes no cash out
        .then(pl.lit("refi_rate_term"))
        .when(is_refi)
        .then(pl.lit("refi_unspecified"))
        # 3 / 4 / 5 are re-pooled loans, not originations: null here, carried by
        # ``is_modified_reissue`` exactly as the GSE ``M`` purpose is.
        .otherwise(None)
    )


def _gnma_gov_insurer(expr: pl.Expr | None) -> pl.Expr:
    """Map GNMA Agency loan type to the government_insurer enum (§4.10a)."""
    if expr is None:
        return pl.lit(None, dtype=pl.String)
    return (
        pl.when(expr == "F")
        .then(pl.lit("FHA"))
        .when(expr == "V")
        .then(pl.lit("VA"))
        .when(expr == "R")
        .then(pl.lit("RD"))
        .when(expr == "P")
        .then(pl.lit("PIH"))
        .otherwise(pl.lit("none"))
    )


def _gnma_program(expr: pl.Expr) -> pl.Expr:
    """Map P-record Issue Type to Ginnie I / Ginnie II (§OQ#2)."""
    e = expr.cast(pl.String).str.strip_chars()
    return (
        pl.when(e == "X")
        .then(pl.lit("ginnie_i"))
        .when(e.is_in(["C", "M"]))
        .then(pl.lit("ginnie_ii"))
        .otherwise(None)
    )


def _gnma_arm_index(expr: pl.Expr | None) -> pl.Expr:
    """Normalize GNMA ``Index Type`` to the controlled ARM-index vocab (§4.12)."""
    if expr is None:
        return pl.lit(None, dtype=pl.String)
    e = expr.str.to_uppercase()
    return (
        pl.when(e.str.len_chars() == 0)
        .then(None)
        .when(e.str.contains("CMT"))
        .then(pl.lit("CMT"))
        .when(e.str.contains("LIBOR"))
        .then(pl.lit("LIBOR"))
        .when(e.str.contains("SOFR"))
        .then(pl.lit("SOFR"))
        .when(e.str.contains("COFI"))
        .then(pl.lit("COFI"))
        .when(e.str.contains("TREAS"))
        .then(pl.lit("TREASURY"))
        .otherwise(pl.lit("OTHER"))
    )


def _gnma_l_dir() -> Path:
    """GNMA dailyllmni L-record (loan-level) silver directory."""
    return MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / "dailyllmni" / "L"


def _gnma_p_dir() -> Path:
    """GNMA dailyllmni P-record (pool header) silver directory."""
    return MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / "dailyllmni" / "P"


def _assemble_gnma_loan_issuance(month: str) -> pl.LazyFrame:
    """Assemble one GNMA issuance month from its dailyllmni L (+ P) silver files.

    Args:
        month: The issuance month ``YYYYMM`` (the L file's date; all rows in a
            dailyllmni month file are new issuances of that month).

    Returns:
        A LazyFrame of unified loan-issuance rows for ``month`` (pre-reindex).
    """
    year = int(month[:4])
    lf = pl.scan_parquet(str(_gnma_l_dir() / f"dailyllmni_{month}_L.parquet"))

    # Whitespace-insensitive name resolution (first occurrence wins).
    schema = lf.collect_schema()
    lookup: dict[str, str] = {}
    for actual in schema.names():
        lookup.setdefault(_g_norm(actual), actual)

    def g(key: str) -> pl.Expr | None:
        return _g_raw(key, lookup, schema)

    pool_key, dsn = g("pool_id"), g("dsn")
    if pool_key is None or dsn is None:
        raise ValueError(f"GNMA {month}: missing Pool ID / Disclosure Sequence Number")
    margin, idx, tpo = g("margin"), g("index_type"), g("tpo")
    margin_pos = (
        (margin.cast(pl.Float64, strict=False) > 0).fill_null(False)
        if margin is not None
        else pl.lit(False)  # noqa: FBT003
    )
    idx_present = _opt_str(idx).is_not_null()
    channel = _channel_to_enum(tpo, gnma=True) if tpo is not None else pl.lit(None, dtype=pl.String)

    out = lf.select(
        # --- keys & provenance ---
        # loan_key = Disclosure Sequence Number alone: verified globally unique (across
        # Ginnie I/II, no cross-pool/cross-program collisions), stable month-to-month, and
        # identical between dailyllmni issuance and llmon performance (99.96%), so the old
        # Pool-ID prefix was redundant. pool_id is retained as its own column.
        pool_key.alias("_pool_key"),
        dsn.alias("loan_key"),
        pl.lit("gnma_seq").alias("loan_key_kind"),
        pl.lit("GNMA").alias("agency"),
        pl.lit("gnma_dailyllmni").alias("source_dataset"),
        pl.lit(int(month), dtype=pl.Int64).alias("issuance_month"),
        pl.lit(year, dtype=pl.Int64).alias("issuance_year"),
        pool_key.alias("pool_id"),
        _opt_str(g("issuer_id")).alias("issuer_id"),
        # NOTE: ``Seller Issuer ID`` (V1.6+) is deliberately NOT emitted — GNMA publishes
        # the slot but it is blank in every dailyllmni month (2026-07-09 audit; the guard
        # test on PRUNED_ALWAYS_NULL_FIELDS alerts if GNMA ever starts populating it).
        # --- dates ---
        _gnma_date(g("first_payment")).alias("first_payment_date"),
        _gnma_date(g("maturity")).alias("maturity_date"),
        _gnma_date(g("orig_date")).alias("origination_date"),
        _quarter_from_date(_gnma_date(g("orig_date"))).alias("orig_quarter"),
        # --- amounts / terms / rate ---
        _gnma_upb_dec(g("opb")).alias("original_upb"),
        _gnma_upb_dec(g("upb_issuance")).alias("issuance_investor_upb"),
        _opt_int(g("term")).alias("loan_term"),
        _opt_int(g("age")).alias("loan_age"),
        _opt_int(g("rmm")).alias("remaining_months_to_maturity"),
        _gnma_rate_dec(g("rate")).alias("note_rate"),
        # --- underwriting ---
        _gnma_ratio_dec(g("ltv")).alias("ltv"),
        _gnma_ratio_dec(g("cltv")).alias("cltv"),
        _gnma_ratio_dec(g("dti")).alias("dti"),
        _fico_clean(_opt_str(g("credit_score"))).alias("representative_credit_score"),
        _opt_int(g("num_borrowers")).alias("number_of_borrowers"),
        _opt_int(g("units")).alias("number_of_units"),
        # --- categoricals (unified enum + raw) ---
        _gnma_purpose(_opt_str(g("purpose")), g("refi_type")).alias("loan_purpose"),
        _opt_str(g("purpose")).alias("loan_purpose_raw"),
        _opt_str(g("purpose"))
        .is_in(["3", "4", "5"])
        .fill_null(value=False)
        .alias("is_modified_reissue"),
        _opt_str(g("refi_type")).alias("refi_type"),
        channel.alias("channel"),
        _opt_str(tpo).alias("channel_raw"),
        pl.when(idx_present | margin_pos)
        .then(pl.lit("ARM"))
        .otherwise(pl.lit("FRM"))
        .alias("amortization_type"),
        _yn_bool(_opt_str(g("fthb"))).alias("first_time_homebuyer"),
        _opt_str(g("state")).alias("property_state"),
        _opt_str(g("msa")).alias("msa"),
        _gnma_gov_insurer(g("agency_loan_type")).alias("government_insurer"),
        # --- GNMA-only extensions ---
        _gnma_rate_dec(g("upfront_mip")).alias("gov_upfront_mip_rate"),
        _gnma_rate_dec(g("annual_mip")).alias("gov_annual_mip_rate"),
        _opt_str(g("dpa")).alias("down_payment_assistance"),
        _opt_str(g("buydown")).alias("buydown"),
        _gnma_arm_index(g("index_type")).alias("arm_index"),
        _opt_str(g("index_type")).alias("arm_index_raw"),
    )

    # --- attach pool-level CUSIP / program / pool_type via L->P on Pool ID ---
    p_path = _gnma_p_dir() / f"dailyllmni_{month}_P.parquet"
    if p_path.exists():
        p_scan = pl.scan_parquet(str(p_path))
        p_names = p_scan.collect_schema().names()

        def pcol(verbose: str, canonical_id: str) -> str:
            # Resolve a P-record column across legacy verbose and canonical snake_case silver.
            if canonical_id in p_names:
                return canonical_id
            return next((c for c in p_names if c.startswith(verbose)), verbose)

        p = p_scan.select(
            pl.col(pcol("Pool ID", "pool_id")).cast(pl.String).str.strip_chars().alias("_pool_key"),
            pl.col(pcol("CUSIP Number", "cusip_number"))
            .cast(pl.String)
            .str.strip_chars()
            .alias("cusip"),
            _gnma_program(pl.col(pcol("Issue Type", "issue_type"))).alias("program"),
            pl.col(pcol("Pool Type", "pool_type"))
            .cast(pl.String)
            .str.strip_chars()
            .alias("pool_type"),
        ).unique()
        # one P record per pool: a pool whose P records disagree gets none of them
        p = p.unique(subset=["_pool_key"], keep="none")
        out = out.join(p, on="_pool_key", how="left")
    else:
        logger.warning("GNMA %s: no P record file; cusip/program/pool_type null.", month)
        out = out.with_columns(
            pl.lit(None, dtype=pl.String).alias("cusip"),
            pl.lit(None, dtype=pl.String).alias("program"),
            pl.lit(None, dtype=pl.String).alias("pool_type"),
        )
    return out.drop("_pool_key")


def _gnma_canon_schema() -> pl.Schema:
    """Resolve GNMA's output schema (schema-only; the column set is constant)."""
    months = sorted(p.stem.split("_")[1] for p in _gnma_l_dir().glob("dailyllmni_*_L.parquet"))
    return _assemble_gnma_loan_issuance(months[-1]).collect_schema()


def _build_gnma_loan_issuance(out_dir: Path, canon: pl.Schema) -> int:
    """Build GNMA's loan-issuance rows, one month file at a time, into ``out_dir``.

    Args:
        out_dir: Dataset root.
        canon: Canonical (tri-agency union) output schema.

    Returns:
        Rows written for GNMA.
    """
    l_files = sorted(_gnma_l_dir().glob("dailyllmni_*_L.parquet"))
    logger.info("  GNMA: %d dailyllmni L month files", len(l_files))
    total = 0
    for f in l_files:
        month = f.stem.split("_")[1]
        chunk = _assemble_gnma_loan_issuance(month).collect(engine="streaming")
        n = _write_chunk(_reindex_to_schema(chunk, canon), out_dir, "GNMA", f"gnma_{month}")
        total += n
        logger.debug("    GNMA/%s: %d rows", month, n)
    return total


def build_loan_issuance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the tri-agency master loan-level issuance dataset (GNMA+FNMA+FHLMC).

    For each GSE, fuses its two non-ID-joinable loan-level spines — the SF
    credit-research / origination spine (base) and the ILLD UMBS pool-disclosure
    spine (enrichment, ∪ the first monthly snapshot) — bridged by the strictly-1:1
    ``crosswalk/mbs_umbs/mbs_umbs_crosswalk_{gse}.parquet``. Harmonization follows D-1..D-6
    (see the schema module). GNMA is a SINGLE
    ``dailyllmni`` spine (no crosswalk; one row per new issuance), harmonized to
    the same unified columns with pool-level CUSIP/program/pool_type attached via
    an L->P join. Output is hive-partitioned by ``agency`` and ``issuance_year``
    under ``data/combined/loan_issuance``.

    Processing is CHUNKED by source file: each GSE chunk narrows the crosswalk and
    the UMBS side to the same loans before joining (a single-shot join OOMs on a
    small machine); GNMA is chunked by monthly L file. All three agencies are
    reindexed to one canonical schema so the dataset scans uniformly.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("loan_issuance")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("loan_issuance already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)

    specs = _gse_specs()
    # Materialize each GSE's UMBS side once (streams; no blocking dedup).
    temps: dict[str, Path] = {}
    for spec in specs:
        tmp = CombinedConfig.COMBINED_DATA_DIR / f"_tmp_{spec.agency.lower()}_umbs.parquet"
        logger.info("Materializing %s UMBS side -> %s", spec.agency, tmp)
        _materialize_umbs(spec, tmp)
        temps[spec.agency] = tmp

    # One canonical schema = union of all three agencies' output schemas, so every
    # written file (any agency) is identical and the dataset scans as one table.
    per_agency = [_gse_canon_schema(spec, temps[spec.agency]) for spec in specs]
    per_agency.append(_gnma_canon_schema())
    canon = pl.concat(
        [pl.DataFrame(schema=s).lazy() for s in per_agency], how="diagonal_relaxed"
    ).collect_schema()

    logger.info("Building combined loan_issuance (GNMA+FNMA+FHLMC) -> %s", out_dir)
    total = 0
    for spec in specs:
        total += _build_gse_loan_issuance(spec, out_dir, canon, temps[spec.agency])
        temps[spec.agency].unlink(missing_ok=True)
    total += _build_gnma_loan_issuance(out_dir, canon)

    logger.info("loan_issuance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


# ===================== loan_performance (loan x month grain) ================
# v1 = GNMA only (single disclosure spine; its perf silver already exists). GNMA
# perf = the llmon L record (loan x reporting-month): llmon1 = Ginnie I, llmon2 =
# Ginnie II (UNION both, split by `program`; llmon2 ~95% of volume). One row per
# (loan, As-of month); the time-varying fields only — origination-snapshot
# covariates (purpose/LTV/FICO/...) live on `loan_issuance` and join by `loan_key`
# (§3.2). All GNMA silver fields are raw fixed-point strings, scaled manually (§6,
# reusing the issuance helpers): UPB ÷100, rate ÷1000. `Months Delinquent` is
# already the 0-6 capped cycle count (6 = 6+). Events: `Current Month Liquidation
# Flag` Y/N + `Removal Reason` -> unified `event_type` (§4.8); credit event =
# Removal Reason in {2,3} (DQ buyout / foreclosure-with-claim, §4.8 resolution).
# Pool-level CUSIP/pool_type attach via L->P on Pool ID (same file). The GSE spines
# (SF-perf base ⋈ MLLD/FU enrichment, fused on the 1:1 crosswalk + reporting_period,
# the alignment measured 2026-06-25) are the v2 follow-up.

#: GNMA llmon L-record performance source columns (long descriptive silver names).
_GP: dict[str, str] = {
    "pool_id": "Pool ID",
    "dsn": "Disclosure Sequence Number (A sequence number unique to loan level)",
    "issuer_id": "Issuer ID (including for loan packages in MIP pool)",
    "as_of": "As of Date (CCYYMM)",
    "upb": "Unpaid Principal Balance (UPB of the loan)",
    "rate": "Loan Interest Rate (current interest rate)",
    "age": "Loan Age, in Months",
    "rmm": "Remaining Loan Term (Remaining Maturity), in Months",
    "months_delinquent": (
        "Months Delinquent (1,2,3,4,5,6 where 6 means 6+ Scheduled installments due but not paid)"
    ),
    "months_prepaid": (
        "Months Pre-Paid (1,2,3,4,5,6 where 6 means 6+ Future scheduled "
        "installments have been paid)"
    ),
    "liquidation_flag": "Current Month Liquidation Flag (Yes or No)",
    "removal_reason": "Removal Reason",
    "state": "State (2 character State Code)",
}

#: Same llmon L fields keyed to canonical snake_case ids (llmon silver reuses dailyllmni.json),
#: so loan_performance reads either legacy verbose or canonical silver.
_GP_CANONICAL: dict[str, str] = {
    "pool_id": "pool_id",
    "dsn": "disclosure_sequence_number",
    "issuer_id": "issuer_id",
    "as_of": "as_of_date",
    "upb": "unpaid_principal_balance",
    "rate": "loan_interest_rate",
    "age": "loan_age_in_months",
    "rmm": "remaining_loan_term_in_months",
    "months_delinquent": "months_delinquent",
    "months_prepaid": "months_pre_paid",
    "liquidation_flag": "current_month_liquidation_flag",
    "removal_reason": "removal_reason",
    "state": "state",
}

#: GNMA Removal Reason -> unified event_type (§4.8; official V1.8 codes).
_GNMA_REMOVAL_REASON: dict[str, str] = {
    "1": "voluntary_prepay",
    "2": "dq_buyout",
    "3": "foreclosure_reo",
    "4": "loss_mit_workout",
    "5": "substitution",
    "6": "other_removal",
}
#: Removal Reasons that are credit/default terminal events (bucket 3 WITH 2, §4.8).
_GNMA_CREDIT_EVENT_CODES: frozenset[str] = frozenset({"2", "3"})

#: GNMA llmon file families -> unified program value (§3.2 / OQ#2).
_GNMA_LLMON_PROGRAMS: tuple[tuple[str, str], ...] = (
    ("llmon1", "ginnie_i"),
    ("llmon2", "ginnie_ii"),
)


def _gnma_event_type(rr: pl.Expr) -> pl.Expr:
    """Map a (stripped) GNMA Removal Reason code to the unified event_type (§4.8).

    Non-liquidated rows (null/blank Removal Reason) and any unrecognized code map
    to null via the ``default``.

    Returns:
        The unified ``event_type`` expression, null for non-liquidated rows.
    """
    return rr.replace_strict(
        list(_GNMA_REMOVAL_REASON.keys()),
        list(_GNMA_REMOVAL_REASON.values()),
        default=None,
        return_dtype=pl.String,
    )


def _gnma_llmon_l_dir(family: str) -> Path:
    """GNMA llmon L-record (loan x month) silver directory for a file family."""
    return MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / family / "L"


def _gnma_llmon_p_dir(family: str) -> Path:
    """GNMA llmon P-record (pool header) silver directory for a file family."""
    return MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / family / "P"


def _assemble_gnma_loan_performance(family: str, program: str, month: str) -> pl.LazyFrame:
    """Assemble one GNMA llmon L month into unified loan_performance rows.

    Args:
        family: The llmon file family (``llmon1`` = Ginnie I, ``llmon2`` = Ginnie II).
        program: The unified ``program`` value for this family.
        month: The reporting month ``YYYYMM`` (the L file's As-of month).

    Returns:
        A LazyFrame of unified loan-performance rows for ``month`` (pre-reindex).
    """
    year = int(month[:4])
    lf = pl.scan_parquet(str(_gnma_llmon_l_dir(family) / f"{family}_{month}_L.parquet"))

    schema = lf.collect_schema()
    lookup: dict[str, str] = {}
    for actual in schema.names():
        lookup.setdefault(_g_norm(actual), actual)

    def g(key: str) -> pl.Expr | None:
        actual = lookup.get(_g_norm(_GP[key])) or lookup.get(_GP_CANONICAL.get(key, ""))
        if actual is None:
            return None
        if schema[actual] == pl.Date:  # canonical typed date -> YYYYMMDD string for the helpers
            return pl.col(actual).dt.strftime("%Y%m%d")
        return pl.col(actual).cast(pl.String).str.strip_chars()

    pool_key, dsn = g("pool_id"), g("dsn")
    if pool_key is None or dsn is None:
        raise ValueError(f"GNMA {family} {month}: missing Pool ID / Disclosure Sequence Number")
    rr = _opt_str(g("removal_reason"))
    liq = g("liquidation_flag")

    out = lf.select(
        # --- keys & provenance ---
        # loan_key = Disclosure Sequence Number alone (Pool-ID prefix dropped as redundant;
        # see _assemble_gnma_loan_issuance). Matches the issuance loan_key 1:1 for joins.
        pool_key.alias("_pool_key"),
        dsn.alias("loan_key"),
        pl.lit("gnma_seq").alias("loan_key_kind"),
        pl.lit("GNMA").alias("agency"),
        pl.lit("gnma_disclosure").alias("source_universe"),
        pl.lit(program).alias("program"),
        pl.lit(int(month), dtype=pl.Int64).alias("reporting_period"),
        pl.lit(year, dtype=pl.Int64).alias("reporting_year"),
        pool_key.alias("pool_id"),
        _opt_str(g("issuer_id")).alias("issuer_id"),
        # --- time-varying balances / rate / seasoning ---
        _gnma_upb_dec(g("upb")).alias("current_upb"),
        _gnma_rate_dec(g("rate")).alias("current_interest_rate"),
        _opt_int(g("age")).alias("loan_age"),
        # RMM junk reaches 967 months against a 480-month product ceiling -> window scrub.
        _opt_int(g("rmm"))
        .pipe(lambda c: pl.when(c.is_between(_RMM_LO, _RMM_HI)).then(c).otherwise(None))
        .alias("remaining_months_to_maturity"),
        # --- delinquency (already 0-6 capped; 6 = 6+) ---
        _opt_int(g("months_delinquent")).alias("delinquency_months"),
        _opt_str(g("months_delinquent")).alias("delinquency_raw"),
        _opt_int(g("months_prepaid")).alias("months_prepaid"),
        # --- event / termination ---
        (liq == "Y").fill_null(value=False).alias("event_flag")
        if liq is not None
        else pl.lit(False).alias("event_flag"),  # noqa: FBT003
        _gnma_event_type(rr).alias("event_type"),
        _event_class_expr(_gnma_event_type(rr)).alias("event_class"),
        rr.alias("event_raw_code"),
        rr.is_in(list(_GNMA_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
        # --- geography ---
        _opt_str(g("state")).alias("property_state"),
    )

    # --- attach pool-level CUSIP / pool_type via L->P on Pool ID ---
    p_path = _gnma_llmon_p_dir(family) / f"{family}_{month}_P.parquet"
    if p_path.exists():
        p_scan = pl.scan_parquet(str(p_path))
        p_names = p_scan.collect_schema().names()

        def pcol(verbose: str, canonical_id: str) -> str:
            if canonical_id in p_names:
                return canonical_id
            return next((c for c in p_names if c.startswith(verbose)), verbose)

        p = p_scan.select(
            pl.col(pcol("Pool ID", "pool_id")).cast(pl.String).str.strip_chars().alias("_pool_key"),
            pl.col(pcol("CUSIP Number", "cusip_number"))
            .cast(pl.String)
            .str.strip_chars()
            .alias("cusip"),
            pl.col(pcol("Pool Type", "pool_type"))
            .cast(pl.String)
            .str.strip_chars()
            .alias("pool_type"),
        ).unique()
        # one P record per pool: a pool whose P records disagree gets none of them
        p = p.unique(subset=["_pool_key"], keep="none")
        out = out.join(p, on="_pool_key", how="left")
    else:
        logger.warning("GNMA %s %s: no P record file; cusip/pool_type null.", family, month)
        out = out.with_columns(
            pl.lit(None, dtype=pl.String).alias("cusip"),
            pl.lit(None, dtype=pl.String).alias("pool_type"),
        )
    return out.drop("_pool_key")


def _write_perf_chunk(df: pl.DataFrame, out_dir: Path, agency: str, tag: str) -> int:
    """Write one month's perf rows into the ``agency=/reporting_year=`` hive layout.

    Args:
        df: Assembled chunk (carries a ``reporting_year`` column).
        out_dir: Dataset root.
        agency: Agency partition value.
        tag: File stem (unique per agency × program × month).

    Returns:
        Number of rows written.
    """
    if df.is_empty():
        return 0
    for key, part in df.partition_by("reporting_year", as_dict=True).items():
        year = key[0] if isinstance(key, tuple) else key
        part_dir = out_dir / f"agency={agency}" / f"reporting_year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part.drop("agency", "reporting_year").write_parquet(part_dir / f"{tag}.parquet")
    return df.height


def _gnma_perf_canon_schema() -> pl.Schema:
    """Resolve GNMA's loan_performance output schema (constant column set)."""
    months = sorted(
        p.stem.split("_")[1] for p in _gnma_llmon_l_dir("llmon2").glob("llmon2_*_L.parquet")
    )
    return _assemble_gnma_loan_performance("llmon2", "ginnie_ii", months[-1]).collect_schema()


def _build_gnma_loan_performance(out_dir: Path, canon: pl.Schema) -> int:
    """Build GNMA's loan_performance rows, one llmon L month file at a time.

    Unions Ginnie I (``llmon1``) + Ginnie II (``llmon2``), split by ``program``;
    each month file streams independently so peak memory stays one month wide.

    Args:
        out_dir: Dataset root.
        canon: Canonical output schema.

    Returns:
        Rows written for GNMA.
    """
    total = 0
    for family, program in _GNMA_LLMON_PROGRAMS:
        l_files = sorted(_gnma_llmon_l_dir(family).glob(f"{family}_*_L.parquet"))
        logger.info("  GNMA %s (%s): %d llmon L month files", family, program, len(l_files))
        for f in l_files:
            month = f.stem.split("_")[1]
            chunk = _assemble_gnma_loan_performance(family, program, month).collect(
                engine="streaming"
            )
            n = _write_perf_chunk(
                _reindex_to_schema(chunk, canon), out_dir, "GNMA", f"{family}_{month}"
            )
            total += n
            logger.debug("    GNMA/%s/%s: %d rows", family, month, n)
    return total


# ===================== GSE loan_performance (SF-perf ⋈ MLLD/FU fusion) =======
# v2 GSE spine. Base = SF Loan-Performance monthly panel (vintage-partitioned;
# carries the explicit ZBC/loss waterfall and the longer pre-2019 history).
# Enrichment + recency tail = the UMBS MLLD/FU monthly factor (month-partitioned;
# investor UPB / net rate / pool CUSIP / updated score, and ~9 months fresher than
# the SF release). Bridged by the strictly-1:1 mbs_umbs crosswalk and joined on
# reporting_period via a FULL-OUTER month join, so a matched loan keeps both its
# SF-only early months (UMBS discloses ~2-3mo after issuance) and its UMBS-only
# recent months (SF release lags). ``month_source`` tags each row both/sf_only/
# umbs_only. UMBS-only LOANS (no SF twin yet — fresh issuance, ARMs) are emitted in
# a final anti-join pass. Only the genuinely time-varying columns are carried (the
# field-volatility allow-list, GSE_PERF_PLAN); static covariates live on
# loan_issuance and join by loan_key. Terminal/loss fields go to loan_terminations.


@dataclass(frozen=True)
class _GsePerfSpec:
    """Per-GSE wiring for the loan-performance fusion."""

    agency: str  # "FNMA" / "FHLMC"
    sf_perf_dir: Path  # SF Loan-Performance silver (one parquet per vintage quarter)
    umbs_perf_dir: Path  # UMBS MLLD/FU monthly-factor silver (one parquet per month)
    crosswalk_file: Path  # mbs_umbs crosswalk for this GSE
    sf_key_col: str  # SF loan-id column name
    loan_key_kind: str  # namespace of the unified loan_key for matched/sf_only rows
    source_universe: str = "gse_disclosure"  # provenance tag (perf + terminations rows)


def _gse_perf_specs() -> list[_GsePerfSpec]:
    """Build the FNMA + FHLMC loan-performance specs (paths resolved at call time)."""
    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
    fnma = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver"
    fhlmc = MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "silver"
    cw = MortgageDataConfig.CROSSWALK_DIR / "mbs_umbs"
    return [
        _GsePerfSpec(
            agency="FNMA",
            sf_perf_dir=fnma / "performance",
            umbs_perf_dir=umbs / "FNMA" / "MLLD",
            crosswalk_file=cw / "mbs_umbs_crosswalk_fnma.parquet",
            sf_key_col="Loan Identifier",
            loan_key_kind="gse_numeric",
        ),
        _GsePerfSpec(
            agency="FHLMC",
            sf_perf_dir=fhlmc / "performance",
            umbs_perf_dir=umbs / "FHLMC" / "FU",
            crosswalk_file=cw / "mbs_umbs_crosswalk_fhlmc.parquet",
            sf_key_col="Loan Sequence Number",
            loan_key_kind="sf_seq",
        ),
    ]


def _perf_crosswalk(path: Path) -> pl.LazyFrame:
    """Scan a GSE crosswalk as ``sf_id`` ↔ ``umbs_id`` (both string, 1:1)."""
    return pl.scan_parquet(path).select(
        pl.col("Loan Sequence Number (MBS)").cast(pl.String).alias("sf_id"),
        pl.col("Loan Sequence Number (UMBS)").cast(pl.String).alias("umbs_id"),
    )


def _zbc_norm(expr: pl.Expr) -> pl.Expr:
    """Normalize a Zero Balance Code (FNMA str / FHLMC int) to a zero-padded 2-char key."""
    s = expr.cast(pl.String, strict=False).str.strip_chars()
    return pl.when(s.str.len_chars() > 0).then(s.str.zfill(2)).otherwise(None)


def _event_type_expr(zbc: pl.Expr) -> pl.Expr:
    """Map a normalized Zero Balance Code to the unified event_type enum."""
    return zbc.replace_strict(
        list(ZBC_EVENT_TYPE.keys()),
        list(ZBC_EVENT_TYPE.values()),
        default=None,
        return_dtype=pl.String,
    )


def _event_class_expr(event_type: pl.Expr) -> pl.Expr:
    """Map a detailed per-agency ``event_type`` to the coarse tri-agency ``event_class``.

    The two detailed vocabularies (GSE ZBC-derived, GNMA removal-reason-derived) are
    disjoint, so one map covers both agencies (schema module ``EVENT_CLASS``).

    Returns:
        The coarse ``event_class`` expression.
    """
    return event_type.replace_strict(
        list(EVENT_CLASS.keys()),
        list(EVENT_CLASS.values()),
        default=None,
        return_dtype=pl.String,
    )


def _period_from_date(col: str) -> pl.Expr:
    """``Monthly Reporting Period`` (pl.Date) -> ``reporting_period`` Int64 YYYYMM."""
    d = pl.col(col)
    return (d.dt.year() * 100 + d.dt.month()).cast(pl.Int64)


def _umbs_perf_source_cols() -> list[str]:
    """Every MLLD/FU source column the perf plan may draw on (+ the day-count)."""
    return sorted({a for fld in GSE_PERF_PLAN for a in fld["umbs"]} | {UMBS_DAYS_DELINQUENT_COL})


def _materialize_umbs_perf(spec: _GsePerfSpec, dest: Path, months: set[str] | None = None) -> None:
    """Stream the UMBS monthly-factor panel (perf cols + reporting_period) to one parquet.

    Concatenates all MLLD/FU month files (schema drifts -> ``diagonal_relaxed``),
    keyed by ``loan_key_umbs`` with ``reporting_period`` from the filename. ``months``
    (a set of ``YYYYMM``) restricts the window for slice validation.
    """
    files = sorted(spec.umbs_perf_dir.glob("*.parquet"))
    if months is not None:
        files = [f for f in files if f.stem.split("_")[1] in months]
    want = _umbs_perf_source_cols()
    frames: list[pl.LazyFrame] = []
    for f in files:
        month = f.stem.split("_")[1]  # MLLD_YYYYMM / FU_YYYYMM
        present = set(pl.scan_parquet(f).collect_schema().names())
        keep = [c for c in want if c in present]
        frames.append(
            pl.scan_parquet(f)
            .select(
                pl.col("Loan Identifier").cast(pl.String).alias("loan_key_umbs"),
                *[pl.col(c) for c in keep],
            )
            .with_columns(pl.lit(int(month), dtype=pl.Int64).alias("reporting_period"))
        )
    pl.concat(frames, how="diagonal_relaxed").sink_parquet(dest)


def _sf_perf_helpers(spec: _GsePerfSpec, sf: pl.LazyFrame) -> pl.LazyFrame:
    """Select the SF side of the fusion: key + period + ``sf::`` helpers + delinquency + ZBC."""
    names = set(sf.collect_schema().names())
    exprs: list[pl.Expr] = [
        pl.col(spec.sf_key_col).cast(pl.String).alias("loan_key_sf"),
        _period_from_date("Monthly Reporting Period").alias("reporting_period"),
        pl.lit(value=True).alias("_sf_present"),
    ]
    for fld in GSE_PERF_PLAN:
        expr = _coalesce_present(fld["sf"], names)
        if expr is None:
            continue
        if fld["name"] == "mi_cancellation_indicator":  # Boolean SF vs Enum-7NY UMBS
            expr = _yn_bool(expr)
        elif fld["name"] == "modification_flag":  # FNMA Boolean vs FHLMC Y/P letters
            expr = _flag_str(expr)
        exprs.append(expr.alias(f"sf::{fld['name']}"))
    if SF_DELINQUENCY_COL in names:  # status string -> int months; "00"/"0" = current, "XX" -> null
        raw = pl.col(SF_DELINQUENCY_COL).cast(pl.String).str.strip_chars()
        exprs.append(raw.alias("delinquency_raw"))
        exprs.append(raw.cast(pl.Int64, strict=False).alias("delinquency_months"))
    if "Zero Balance Code" in names:
        exprs.append(_zbc_norm(pl.col("Zero Balance Code")).alias("zero_balance_code"))
    if "Zero Balance Effective Date" in names:
        exprs.append(pl.col("Zero Balance Effective Date").alias("zero_balance_effective_date"))
    return sf.select(exprs)


def _umbs_perf_helpers(umbs_k: pl.LazyFrame) -> pl.LazyFrame:
    """Select the UMBS side (already cw-joined to carry ``loan_key_sf``): ``umbs::`` helpers."""
    names = set(umbs_k.collect_schema().names())
    exprs: list[pl.Expr] = [
        pl.col("loan_key_sf"),
        pl.col("loan_key_umbs"),
        pl.col("reporting_period"),
        pl.lit(value=True).alias("_umbs_present"),
    ]
    for fld in GSE_PERF_PLAN:
        expr = _coalesce_present(fld["umbs"], names)
        if expr is None:
            continue
        if fld["name"] == "mi_cancellation_indicator":  # Boolean on BOTH sides pre-coalesce
            expr = _yn_bool(expr)
        exprs.append(expr.alias(f"umbs::{fld['name']}"))
    if UMBS_DAYS_DELINQUENT_COL in names:
        exprs.append(
            pl.col(UMBS_DAYS_DELINQUENT_COL)
            .cast(pl.Int64, strict=False)
            .alias("days_delinquent_umbs")
        )
    return umbs_k.select(exprs)


def _finalize_gse_perf(lf: pl.LazyFrame, spec: _GsePerfSpec) -> pl.LazyFrame:
    """Resolve ``sf::``/``umbs::`` helpers into the unified perf columns + provenance.

    Works for all three row kinds (matched both-month, sf_only, umbs_only) — after a
    ``diagonal_relaxed`` concat every helper exists (null where absent), so the same
    resolution applies. ``loan_key`` falls back to the UMBS id for umbs_only loans.

    Returns:
        The frame with the unified perf columns and provenance resolved.
    """
    cols = set(lf.collect_schema().names())

    def pick(name: str) -> pl.Expr:
        c = name if name in cols else None
        return pl.col(c) if c else pl.lit(None)

    def resolve(fld: PerfField) -> pl.Expr:
        sfh, umbsh = f"sf::{fld['name']}", f"umbs::{fld['name']}"
        has_sf, has_umbs = sfh in cols, umbsh in cols
        if fld["resolve"] == "sf":
            return pick(sfh).alias(fld["name"])
        if fld["resolve"] == "umbs":
            return pick(umbsh).alias(fld["name"])
        first, second = (sfh, umbsh) if fld["resolve"] == "sf_first" else (umbsh, sfh)
        if has_sf and has_umbs:
            return pl.coalesce([pl.col(first), pl.col(second)]).alias(fld["name"])
        return pick(sfh if has_sf else umbsh).alias(fld["name"])

    has_sf = (pl.col("_sf_present") if "_sf_present" in cols else pl.lit(value=False)).fill_null(
        value=False
    )
    has_umbs = (
        pl.col("_umbs_present") if "_umbs_present" in cols else pl.lit(value=False)
    ).fill_null(value=False)
    loan_key_sf = pick("loan_key_sf")
    # String-typed null when absent (umbs_only frame) so the event-taxonomy
    # is_in / replace_strict reconcile with the string ZBC codes.
    zbc = (
        pl.col("zero_balance_code")
        if "zero_balance_code" in cols
        else pl.lit(None, dtype=pl.String)
    )
    out = lf.with_columns(
        pl.coalesce([loan_key_sf, pick("loan_key_umbs")]).alias("loan_key"),
        pl.when(loan_key_sf.is_null())
        .then(pl.lit("umbs_seq"))
        .otherwise(pl.lit(spec.loan_key_kind))
        .alias("loan_key_kind"),
        pl.lit(spec.agency).alias("agency"),
        pl.lit(spec.source_universe).alias("source_universe"),
        pl.when(has_umbs).then(pl.lit("umbs")).otherwise(None).alias("program"),
        pl.when(has_sf & has_umbs)
        .then(pl.lit("both"))
        .when(has_sf)
        .then(pl.lit("sf_only"))
        .otherwise(pl.lit("umbs_only"))
        .alias("month_source"),
        (pl.col("reporting_period") // 100).alias("reporting_year"),
        *[resolve(fld) for fld in GSE_PERF_PLAN],
        zbc.alias("event_raw_code"),
        _event_type_expr(zbc).alias("event_type"),
        _event_class_expr(_event_type_expr(zbc)).alias("event_class"),
        zbc.is_not_null().alias("event_flag"),
        zbc.is_in(list(ZBC_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
    )
    # Sentinel / junk scrub (2026-07-09 audit) — the source disclosures leak these through
    # the raw coalesce above. Kept AFTER resolve so the scrub sees the winning value.
    out = out.with_columns(
        pl.when(pl.col("estimated_ltv") >= _ELTV_SENTINEL_FLOOR)
        .then(None)
        .otherwise(pl.col("estimated_ltv"))
        .alias("estimated_ltv"),
        pl.when(pl.col("current_interest_rate") == _RATE_SENTINEL)
        .then(None)
        .otherwise(pl.col("current_interest_rate"))
        .alias("current_interest_rate"),
        pl.when(pl.col("loan_age") < _LOAN_AGE_FLOOR)
        .then(None)
        .otherwise(pl.col("loan_age"))
        .alias("loan_age"),
        pl.when(pl.col("remaining_months_to_maturity").is_between(_RMM_LO, _RMM_HI))
        .then(pl.col("remaining_months_to_maturity"))
        .otherwise(None)
        .alias("remaining_months_to_maturity"),
        # MLLD/FU discloses MI percent as a string with a 999 sentinel -> numeric + null.
        pl.col("mortgage_insurance_percent")
        .cast(pl.Float64, strict=False)
        .pipe(lambda c: pl.when(c == _MI_PCT_SENTINEL).then(None).otherwise(c))
        .alias("mortgage_insurance_percent"),
        _fico_clean(pl.col("updated_credit_score")).alias("updated_credit_score"),
        # '7'/'9' = "not available / not applicable" mixed into the plan-letter space
        # (the mi-cancellation indicator is handled at emit time by _yn_bool). The
        # cast-through-String condition stays Enum-safe.
        *[
            pl.when(pl.col(c).cast(pl.String, strict=False).is_in(list(GSE_NA_CODES)))
            .then(None)
            .otherwise(pl.col(c))
            .alias(c)
            for c in PERF_NA_CODE_COLUMNS
        ],
    )
    final = [
        "loan_key",
        "loan_key_kind",
        "loan_key_umbs",
        "agency",
        "source_universe",
        "program",
        "reporting_period",
        "reporting_year",
        "month_source",
        *[fld["name"] for fld in GSE_PERF_PLAN],
        "delinquency_months",
        "delinquency_raw",
        "days_delinquent_umbs",
        "event_flag",
        "event_type",
        "event_class",
        "event_raw_code",
        "is_credit_event",
        "zero_balance_effective_date",
    ]
    present = out.collect_schema().names()
    # delinquency/zbc helpers may be absent on a umbs_only-only frame -> add typed nulls
    out = out.with_columns(
        [pl.lit(None).alias(c) for c in final if c not in present and c not in GSE_PERF_PLAN]
    )
    ordered = [c for c in final if c in out.collect_schema().names()]
    return out.select(ordered)


def _fuse_gse_perf(
    spec: _GsePerfSpec, sf: pl.LazyFrame, umbs_chunk: pl.LazyFrame, cw_chunk: pl.LazyFrame
) -> pl.LazyFrame:
    """Full-outer fuse one SF chunk with its UMBS rows on (loan, reporting_period)."""
    sf_h = _sf_perf_helpers(spec, sf)
    sf_keyed = sf_h.join(cw_chunk, left_on="loan_key_sf", right_on="sf_id", how="left")
    matched = sf_keyed.filter(pl.col("umbs_id").is_not_null()).drop("umbs_id")
    sf_only = sf_keyed.filter(pl.col("umbs_id").is_null()).drop("umbs_id")
    # UMBS side: attach loan_key_sf via the crosswalk (inner -> matched loans only).
    umbs_k = umbs_chunk.join(
        cw_chunk, left_on="loan_key_umbs", right_on="umbs_id", how="inner"
    ).rename({"sf_id": "loan_key_sf"})
    umbs_h = _umbs_perf_helpers(umbs_k)
    fused = matched.join(umbs_h, on=["loan_key_sf", "reporting_period"], how="full", coalesce=True)
    return pl.concat([fused, sf_only], how="diagonal_relaxed")


def _gse_perf_canon_schema(spec: _GsePerfSpec, tmp_umbs: Path) -> pl.Schema:
    """Resolve the GSE perf output schema (schema-only; SF column union across vintages)."""
    sf_files = sorted(spec.sf_perf_dir.glob("*.parquet"))
    sf_union = pl.concat([pl.scan_parquet(str(f)) for f in sf_files], how="diagonal_relaxed")
    cw = _perf_crosswalk(spec.crosswalk_file)
    fused = _fuse_gse_perf(spec, sf_union, pl.scan_parquet(tmp_umbs), cw)
    return _finalize_gse_perf(fused, spec).collect_schema()


def _conform_lazy(lf: pl.LazyFrame, schema: pl.Schema) -> pl.LazyFrame:
    """Lazily conform a frame to a canonical schema (cast present cols, add missing as null).

    The streaming-sink equivalent of `_reindex_to_schema` -- keeps the plan lazy so
    the partitioned sink never materializes the whole chunk in memory.

    Returns:
        The frame conformed to ``schema``, still lazy.
    """
    have = set(lf.collect_schema().names())
    return lf.select(
        [
            pl.col(name).cast(dtype, strict=False)
            if name in have
            else pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in schema.items()
        ]
    )


def _perf_partition(out_dir: Path, tag: str) -> pl.PartitionBy:
    """A streaming hive sink (``agency=/reporting_year=``) with per-chunk unique filenames.

    ``file_path_provider`` names each file ``{tag}_{idx}.parquet`` so separate per-vintage
    sinks never collide in a shared ``reporting_year`` partition.

    Returns:
        The partitioning object to hand to ``sink_parquet``.
    """

    def provider(ctx: object) -> Path:
        agency, year = ctx.partition_keys.row(0)  # type: ignore[attr-defined]
        part = out_dir / f"agency={agency}" / f"reporting_year={year}"
        part.mkdir(parents=True, exist_ok=True)
        return part / f"{tag}_{ctx.index_in_partition}.parquet"  # type: ignore[attr-defined]

    return pl.PartitionBy(
        str(out_dir),
        key=["agency", "reporting_year"],
        file_path_provider=provider,
        include_key=False,
    )


def _build_gse_loan_performance(
    spec: _GsePerfSpec, out_dir: Path, canon: pl.Schema, tmp_umbs: Path, *, max_sf_files: int | None
) -> None:
    """Fuse one GSE's SF-perf ⋈ UMBS panel, chunked by SF vintage file, into ``out_dir``.

    Each vintage chunk STREAMS to the partitioned layout via ``sink_parquet`` -- never
    ``collect``-ing the whole cohort, which OOMs on the big refi vintages (the per-vintage
    join build side stays bounded; only the materialized output did not).
    """
    cw = _perf_crosswalk(spec.crosswalk_file)
    sf_files = sorted(spec.sf_perf_dir.glob("*.parquet"))
    if max_sf_files:
        sf_files = sf_files[:max_sf_files]
    logger.info("  %s: %d SF-perf vintage files (streaming sink)", spec.agency, len(sf_files))
    for i, sf_file in enumerate(sf_files):
        sf = pl.scan_parquet(str(sf_file))
        sf_ids = sf.select(pl.col(spec.sf_key_col).cast(pl.String).alias("sf_id")).unique()
        cw_chunk = cw.join(sf_ids, on="sf_id", how="semi")
        umbs_ids = cw_chunk.select("umbs_id").unique()
        umbs_chunk = pl.scan_parquet(tmp_umbs).join(
            umbs_ids, left_on="loan_key_umbs", right_on="umbs_id", how="semi"
        )
        fused = _finalize_gse_perf(_fuse_gse_perf(spec, sf, umbs_chunk, cw_chunk), spec)
        _conform_lazy(fused, canon).sink_parquet(
            _perf_partition(out_dir, f"{spec.agency.lower()}_part{i:03d}"), mkdir=True
        )
        logger.debug("    %s/part%03d (%s): sunk", spec.agency, i, sf_file.stem)
    # UMBS-only loans (no SF twin: fresh issuance not yet in SF, ARMs) -> umbs_only rows.
    umbs_ids_all = cw.select("umbs_id").unique()
    umbs_only = pl.scan_parquet(tmp_umbs).join(
        umbs_ids_all, left_on="loan_key_umbs", right_on="umbs_id", how="anti"
    )
    umbs_only_h = _umbs_perf_helpers(
        umbs_only.with_columns(pl.lit(None, dtype=pl.String).alias("loan_key_sf"))
    )
    _conform_lazy(_finalize_gse_perf(umbs_only_h, spec), canon).sink_parquet(
        _perf_partition(out_dir, f"{spec.agency.lower()}_umbs_only"), mkdir=True
    )
    logger.debug("    %s/umbs_only: sunk", spec.agency)


def _harp_perf_spec() -> _GsePerfSpec:
    """The FNMA HARP wiring: the FNMA SF spec re-pointed at ``silver/harp``.

    Same 113-field Primary layout and loan-id namespace (the HARP population is
    verified disjoint from Primary — zero shared ids — so nothing double-counts).
    HARP ids never appear in the mbs_umbs crosswalk, so rows are SF-only by
    construction; ``source_universe`` distinguishes them from the standard SF panel.

    Returns:
        The HARP performance spec.
    """
    fnma_spec = next(s for s in _gse_perf_specs() if s.agency == "FNMA")
    harp_dir = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "harp"
    return replace(fnma_spec, sf_perf_dir=harp_dir, source_universe="gse_harp_disclosure")


def _harp_perf_file() -> Path:
    """The single HARP monthly-performance silver file.

    Addressed by name, not globbed — the harp dir also holds the
    issuances/terminations/loan_mapping siblings.

    Returns:
        The HARP monthly-performance silver file path.
    """
    return _harp_perf_spec().sf_perf_dir / "performance.parquet"


def _build_harp_loan_performance(out_dir: Path, canon: pl.Schema) -> None:
    """Stream the FNMA HARP panel (SF-only rows, no UMBS twin) into ``out_dir``.

    Selection caveat carried by construction: every HARP loan is an underwater
    refi (Loan Purpose R, LTV > 80) — a program population, not a sample.
    """
    perf_file = _harp_perf_file()
    if not perf_file.exists():
        logger.warning("  FNMA HARP: %s absent; skipping (run `fnma silver -t harp`)", perf_file)
        return
    spec = _harp_perf_spec()
    logger.info("  FNMA HARP: streaming %s", perf_file)
    sf = pl.scan_parquet(str(perf_file))
    fused = _finalize_gse_perf(_sf_perf_helpers(spec, sf), spec)
    _conform_lazy(fused, canon).sink_parquet(_perf_partition(out_dir, "fnma_harp"), mkdir=True)
    logger.debug("    FNMA/harp: sunk")


# ===================== GSE loan_terminations (sparse event waterfall) ========


def _assemble_gse_terminations(spec: _GsePerfSpec, sf: pl.LazyFrame) -> pl.LazyFrame:
    """Extract one SF chunk's terminal (Zero-Balance) rows into the events grain."""
    names = set(sf.collect_schema().names())
    key = pl.col(spec.sf_key_col).cast(pl.String).alias("loan_key")
    zbc = _zbc_norm(pl.col("Zero Balance Code"))
    exprs: list[pl.Expr] = [
        key,
        pl.lit(spec.loan_key_kind).alias("loan_key_kind"),
        pl.lit(spec.agency).alias("agency"),
        pl.lit(spec.source_universe).alias("source_dataset"),
        zbc.alias("zero_balance_code"),
        _event_type_expr(zbc).alias("event_type"),
        _event_class_expr(_event_type_expr(zbc)).alias("event_class"),
        zbc.is_in(list(ZBC_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
        pl.col("Zero Balance Effective Date").alias("zero_balance_effective_date"),
        pl.col("Zero Balance Effective Date").dt.year().cast(pl.Int64).alias("zero_balance_year"),
    ]
    for fld in GSE_TERM_PLAN:
        expr = _coalesce_present(fld["sf"], names)
        if expr is not None:
            exprs.append(expr.alias(fld["name"]))
    return sf.filter(pl.col("Zero Balance Code").is_not_null()).select(exprs)


def _gse_term_canon_schema(spec: _GsePerfSpec) -> pl.Schema:
    """Resolve the terminations output schema (schema-only; SF column union)."""
    sf_files = sorted(spec.sf_perf_dir.glob("*.parquet"))
    sf_union = pl.concat([pl.scan_parquet(str(f)) for f in sf_files], how="diagonal_relaxed")
    return _assemble_gse_terminations(spec, sf_union).collect_schema()


def _write_term_chunk(df: pl.DataFrame, out_dir: Path, agency: str, tag: str) -> int:
    """Write one chunk's termination rows into the ``agency=/zero_balance_year=`` layout."""
    if df.is_empty():
        return 0
    df = df.filter(pl.col("zero_balance_year").is_not_null())
    for key, part in df.partition_by("zero_balance_year", as_dict=True).items():
        year = key[0] if isinstance(key, tuple) else key
        part_dir = out_dir / f"agency={agency}" / f"zero_balance_year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part.drop("agency", "zero_balance_year").write_parquet(part_dir / f"{tag}.parquet")
    return df.height


def _build_gse_loan_terminations(
    spec: _GsePerfSpec, out_dir: Path, canon: pl.Schema, *, max_sf_files: int | None
) -> int:
    """Extract one GSE's terminal events from the SF-perf panel into ``out_dir``."""
    sf_files = sorted(spec.sf_perf_dir.glob("*.parquet"))
    if max_sf_files:
        sf_files = sf_files[:max_sf_files]
    total = 0
    for i, sf_file in enumerate(sf_files):
        # Fannie's Exclusion files (``{quarter}EXCL``) hold a different population: the
        # ARM, Alt-A and other loans left out of the standard dataset. Keep them separable.
        file_spec = (
            replace(spec, source_universe="fnma_exclusion")
            if sf_file.stem.endswith("EXCL")
            else spec
        )
        chunk = _assemble_gse_terminations(file_spec, pl.scan_parquet(str(sf_file))).collect(
            engine="streaming"
        )
        n = _write_term_chunk(
            _reindex_to_schema(chunk, canon), out_dir, spec.agency, f"part{i:03d}"
        )
        total += n
    logger.info("  %s: %d termination events", spec.agency, total)
    return total


def _build_harp_loan_terminations(out_dir: Path, canon: pl.Schema) -> int:
    """Extract the FNMA HARP panel's terminal events (``source_dataset`` tags them)."""
    perf_file = _harp_perf_file()
    if not perf_file.exists():
        logger.warning("  FNMA HARP: %s absent; skipping (run `fnma silver -t harp`)", perf_file)
        return 0
    spec = _harp_perf_spec()
    chunk = _assemble_gse_terminations(spec, pl.scan_parquet(str(perf_file))).collect(
        engine="streaming"
    )
    n = _write_term_chunk(_reindex_to_schema(chunk, canon), out_dir, spec.agency, "harp")
    logger.info("  FNMA HARP: %d termination events", n)
    return n


def build_loan_performance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the master loan-level performance panel (loan x month).

    Tri-agency (GNMA + FNMA + FHLMC). One row per (loan, reporting month) carrying
    only the genuinely time-varying fields (the field-volatility allow-list:
    balance/rate/seasoning/delinquency/servicer/forbearance/event); origination-
    snapshot covariates live on ``loan_issuance`` and join by ``loan_key`` (§3.2).

    GNMA is a single disclosure spine (llmon L: Ginnie I ``llmon1`` ∪ Ginnie II
    ``llmon2``, split by ``program``). Each GSE FUSES its SF Loan-Performance panel
    (base) with the UMBS MLLD/FU monthly factor (enrichment + ~9-month recency tail)
    via the strictly-1:1 mbs_umbs crosswalk on ``reporting_period`` — a full-outer
    month join that retains a matched loan's SF-only early months and UMBS-only
    recent months, plus UMBS-only loans with no SF twin yet; ``month_source`` tags
    each row both/sf_only/umbs_only. All three agencies reindex to one canonical
    schema. The FNMA HARP panel (1.04M underwater-refi loans, disjoint from the
    Primary dataset) is unioned as SF-only rows tagged
    ``source_universe='gse_harp_disclosure'`` — filter on ``source_universe`` when
    the standard-acquisition universe is required (HARP is a program population,
    not a sample). Output is hive-partitioned by ``agency`` and ``reporting_year``
    under ``data/combined/loan_performance``. Terminal/loss fields are NOT here —
    see `build_loan_terminations`.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("loan_performance")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("loan_performance already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)

    specs = _gse_perf_specs()
    temps: dict[str, Path] = {}
    for spec in specs:
        tmp = CombinedConfig.COMBINED_DATA_DIR / f"_tmp_{spec.agency.lower()}_umbs_perf.parquet"
        logger.info("Materializing %s UMBS perf side -> %s", spec.agency, tmp)
        _materialize_umbs_perf(spec, tmp)
        temps[spec.agency] = tmp

    # Canonical schema = union of GNMA + both GSE perf output schemas.
    per_agency = [_gnma_perf_canon_schema()]
    per_agency += [_gse_perf_canon_schema(spec, temps[spec.agency]) for spec in specs]
    canon = pl.concat(
        [pl.DataFrame(schema=s).lazy() for s in per_agency], how="diagonal_relaxed"
    ).collect_schema()

    logger.info("Building combined loan_performance (GNMA+FNMA+FHLMC+HARP) -> %s", out_dir)
    _build_gnma_loan_performance(out_dir, canon)
    for spec in specs:
        _build_gse_loan_performance(spec, out_dir, canon, temps[spec.agency], max_sf_files=None)
        temps[spec.agency].unlink(missing_ok=True)
    _build_harp_loan_performance(out_dir, canon)
    # GSE chunks stream-sink (no row count returned); tally from the parquet footers,
    # per file -- a single len() over the tree overflows polars' 32-bit row index
    # (the panel passed 2**32 rows once the full GSE histories were in).
    total = _count_rows(out_dir)
    logger.info("loan_performance built: %d rows (GNMA+FNMA+FHLMC+HARP) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


def build_loan_terminations(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the GSE loan termination / loss-waterfall events master (one row per event).

    The sparse counterpart to `build_loan_performance`: each loan's terminal
    Zero-Balance row (prepay / third-party sale / short sale / REO / repurchase /
    note sale) with the loss waterfall (proceeds, costs, recoveries, write-offs).
    SF-sourced (SF carries the explicit ZBC; UMBS corroborates by disappearance),
    kept OUT of the dense monthly panel because it is populated for ~1 month in ~42.
    Several FNMA CRT loss fields are absent in the standard SF product (null here).
    FNMA HARP terminal events are included, tagged
    ``source_dataset='gse_harp_disclosure'``.
    Output is hive-partitioned by ``agency`` and ``zero_balance_year`` under
    ``data/combined/loan_terminations``. GNMA terminations stay inline on
    ``loan_performance`` (``event_type``) — Ginnie has no comparable loss waterfall.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("loan_terminations")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("loan_terminations already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)

    specs = _gse_perf_specs()
    per_agency = [_gse_term_canon_schema(spec) for spec in specs]
    canon = pl.concat(
        [pl.DataFrame(schema=s).lazy() for s in per_agency], how="diagonal_relaxed"
    ).collect_schema()

    logger.info("Building combined loan_terminations (FNMA+FHLMC+HARP) -> %s", out_dir)
    total = 0
    for spec in specs:
        total += _build_gse_loan_terminations(spec, out_dir, canon, max_sf_files=None)
    total += _build_harp_loan_terminations(out_dir, canon)
    logger.info("loan_terminations built: %d events (FNMA+FHLMC+HARP) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


# ===================== loan_outcomes (one row per loan) =======================

# loan_outcomes: number of loan_key-hash shards for the reduce step, so peak memory
# is ~1/N of an agency's per-file partials (themselves ~10x smaller than the panel).
_LOAN_OUTCOMES_SHARDS = 8

#: Modification-flag letters that mean "modified" (FNMA Y/N Boolean-as-text, FHLMC
#: Y = modified / P = prior modification, both null when not modified).
_MODIFIED_FLAGS: tuple[str, ...] = ("Y", "P")
#: Borrower-assistance plan letters that mean a plan was in effect (F forbearance,
#: R repayment, T trial-period modification); N = none, 7/9 are scrubbed upstream.
_ASSISTANCE_PLANS: tuple[str, ...] = ("F", "R", "T")

#: Outcome columns that are only meaningful where the agency discloses the input.
#: Each maps to the partial-aggregate flag saying "this loan carried the input at
#: least once"; an agency with no such loan gets the whole column nulled (GNMA has
#: no modification flag or assistance plan). Within a disclosing agency, absence
#: means no -- the GSEs leave the flag null on unmodified loans rather than 'N'.
_OUTCOME_INFO_GATES: dict[str, str] = {
    "ever_modified": "_mod_info",
    "ever_assistance_plan": "_plan_info",
    "ever_forbearance": "_plan_info",
}


def _only(values: pl.Expr, n: pl.Expr) -> pl.Expr:
    """``values``' single element when ``n`` is exactly 1, else null -- never a pick among ties."""
    # tie-safe: taken only when exactly one row qualifies
    return pl.when(n == 1).then(values.first()).otherwise(None)


def _sole_value(col: str) -> pl.Expr:
    """``col``'s value when the group carries exactly one distinct value, else null."""
    # tie-safe: taken only when the group holds a single distinct value
    return pl.when(pl.col(col).n_unique() == 1).then(pl.col(col).first()).otherwise(None)


def loan_outcome_partial_aggs() -> list[pl.Expr]:
    """Map step: per-loan partial aggregates over ONE ``loan_performance`` file.

    Every panel file holds one agency x reporting_year slice, so a loan's history
    is spread over several files; these partials are all decomposable (min/max/
    any/len, "value at the latest month", "value on the event row") and
    `loan_outcome_reduce_aggs` folds them per loan. Applied inside
    ``group_by("loan_key")``.

    Returns:
        The partial aggregate expressions, to apply per loan per file.
    """
    period = pl.col("reporting_period")
    dq = pl.col("delinquency_months")
    mod_flag = pl.col("modification_flag").cast(pl.String).str.to_uppercase()
    n_mods = pl.col("number_of_modifications")
    plan = pl.col("borrower_assistance_plan")
    ev = pl.col("event_flag").fill_null(value=False)

    def last(col: str) -> pl.Expr:
        # the value on the loan's latest month; null if two rows share that month
        at_latest = period == period.max()
        return _only(pl.col(col).filter(at_latest), at_latest.sum())

    def at_event(col: str) -> pl.Expr:
        at_latest_event = ev & (period == period.filter(ev).max())
        return _only(pl.col(col).filter(at_latest_event), at_latest_event.sum())

    return [
        _sole_value("loan_key_kind").alias("loan_key_kind"),
        _sole_value("source_universe").alias("source_universe"),
        period.min().alias("first_reporting_period"),
        period.max().alias("last_reporting_period"),
        pl.len().cast(pl.Int32).alias("n_months_observed"),
        last("current_upb").alias("last_upb"),
        last("loan_age").alias("last_loan_age"),
        last("delinquency_months").alias("last_delinquency_months"),
        last("servicer_name").alias("last_servicer_name"),
        dq.max().alias("peak_delinquency_months"),
        period.filter(dq >= 3).min().alias("first_90_period"),
        (mod_flag.is_in(list(_MODIFIED_FLAGS)).any() | (n_mods > 0).any()).alias("_modified"),
        (mod_flag.is_not_null().any() | n_mods.is_not_null().any()).alias("_mod_info"),
        plan.is_in(list(_ASSISTANCE_PLANS)).any().alias("_plan"),
        (plan == "F").any().alias("_forbearance"),
        plan.is_not_null().any().alias("_plan_info"),
        ev.any().alias("terminated"),
        period.filter(ev).max().alias("termination_period"),
        at_event("event_type").alias("event_type"),
        at_event("event_class").alias("event_class"),
        at_event("is_credit_event").alias("is_credit_event"),
        at_event("zero_balance_effective_date").alias("zero_balance_effective_date"),
        at_event("loan_age").alias("loan_age_at_termination"),
    ]


def loan_outcome_reduce_aggs() -> list[pl.Expr]:
    """Reduce step: fold a loan's per-file partials into one outcome row.

    Applied inside ``group_by("loan_key")`` over the output of
    `loan_outcome_partial_aggs`. "Latest" columns follow the partial with the
    greatest ``last_reporting_period``; event columns take the single non-null
    partial (a loan has at most one terminal row).

    Returns:
        The reduce expressions, folding a loan's partials into one outcome row.
    """
    last_period = pl.col("last_reporting_period")

    def latest(col: str) -> pl.Expr:
        # the partial with the greatest last_reporting_period; null if two partials tie
        at_latest = last_period == last_period.max()
        return _only(pl.col(col).filter(at_latest), at_latest.sum())

    def event(col: str) -> pl.Expr:
        # the single non-null partial value; null if the partials disagree
        values = pl.col(col).drop_nulls()
        return _only(values, values.n_unique())

    return [
        _sole_value("loan_key_kind").alias("loan_key_kind"),
        _sole_value("source_universe").alias("source_universe"),
        pl.col("first_reporting_period").min().alias("first_reporting_period"),
        last_period.max().alias("last_reporting_period"),
        pl.col("n_months_observed").sum().cast(pl.Int32).alias("n_months_observed"),
        latest("last_upb").alias("last_upb"),
        latest("last_loan_age").alias("last_loan_age"),
        latest("last_delinquency_months").alias("last_delinquency_months"),
        latest("last_servicer_name").alias("last_servicer_name"),
        pl.col("peak_delinquency_months").max().alias("peak_delinquency_months"),
        pl.col("first_90_period").min().alias("first_90_period"),
        pl.col("_modified").any().alias("ever_modified"),
        pl.col("_mod_info").any().alias("_mod_info"),
        pl.col("_plan").any().alias("ever_assistance_plan"),
        pl.col("_forbearance").any().alias("ever_forbearance"),
        pl.col("_plan_info").any().alias("_plan_info"),
        pl.col("terminated").any().alias("terminated"),
        pl.col("termination_period").max().alias("termination_period"),
        event("event_type").alias("event_type"),
        event("event_class").alias("event_class"),
        event("is_credit_event").alias("is_credit_event"),
        event("zero_balance_effective_date").alias("zero_balance_effective_date"),
        event("loan_age_at_termination").alias("loan_age_at_termination"),
    ]


def _finalize_loan_outcomes(lf: pl.LazyFrame, agency_info: dict[str, bool]) -> pl.LazyFrame:
    """Derive the delinquency flags, gate agency-undisclosed outcomes to null, drop helpers."""
    peak = pl.col("peak_delinquency_months")
    gated = [
        (pl.col(col) if agency_info[gate] else pl.lit(None, dtype=pl.Boolean)).alias(col)
        for col, gate in _OUTCOME_INFO_GATES.items()
    ]
    return lf.with_columns(
        (peak >= 1).alias("ever_30"),
        (peak >= 2).alias("ever_60"),
        (peak >= 3).alias("ever_90"),
        (peak >= 6).alias("ever_180"),
        *gated,
    ).drop("_mod_info", "_plan_info")


def _fhlmc_rpl_map() -> pl.LazyFrame:
    """Freddie's SF loan id -> seasoned-deal (SCRT/SLST) loan id map, standard + non-standard."""
    rpl_dir = MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "bronze" / "reperforming"
    files = sorted(rpl_dir.glob("SFLLD_RPL_ID_Match*.parquet"))
    if not files:
        logger.warning("No FHLMC RPL id map under %s; is_rpl will be null", rpl_dir)
        return pl.LazyFrame(
            schema={
                "loan_key": pl.String,
                "rpl_deal_loan_id": pl.String,
                "rpl_deal_type": pl.String,
            }
        )
    return (
        pl.concat([pl.scan_parquet(f) for f in files], how="diagonal_relaxed")
        .select(
            pl.col("id_loan").alias("loan_key"),
            pl.col("loan_identifier").alias("rpl_deal_loan_id"),
            pl.col("loan_identifier").str.slice(4, 4).alias("rpl_deal_type"),
        )
        # one RPL deal id per loan; a loan mapped to two different ids gets neither
        .unique()
        .unique(subset="loan_key", keep="none")
    )


def _months_between(later: pl.Expr, earlier: pl.Expr) -> pl.Expr:
    """Whole months from an ``earlier`` Date to a ``later`` YYYYMM int period."""
    return (later // 100 * 12 + later % 100) - (earlier.dt.year() * 12 + earlier.dt.month())


def _loan_outcomes_partials(agency_dir: Path, tmp_dir: Path) -> list[Path]:
    """Map step over one agency: one partial parquet per panel file."""
    files = sorted(agency_dir.glob("reporting_year=*/*.parquet"))
    out: list[Path] = []
    for i, f in enumerate(files):
        part = tmp_dir / f"{agency_dir.name}_{i:04d}.parquet"
        pl.scan_parquet(str(f)).group_by("loan_key").agg(loan_outcome_partial_aggs()).sink_parquet(
            part
        )
        out.append(part)
    return out


def build_loan_outcomes(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the loan-grain outcome summary (one row per loan, tri-agency).

    Collapses each loan's ``loan_performance`` history to the facts most analyses
    want without touching the ~4B-row panel: observation window, last-observed
    balance/age/status/servicer, peak delinquency and the ``ever_30/60/90/180``
    flags, modification and assistance-plan flags, and the terminal event (type,
    class, credit flag, period, loan age at termination). The GSE loss waterfall is
    NOT repeated here -- ``loan_terminations`` carries it and joins on ``loan_key``;
    only ``zero_balance_code``, ``upb_at_removal`` and ``actual_loss`` ride along.
    ``first_payment_date`` / ``original_upb`` come from ``loan_issuance`` and give
    ``months_to_termination``; Freddie loans later sold into a seasoned deal carry
    ``is_rpl`` plus the SCRT/SLST deal loan id from the RPL id map.

    Two-level aggregation: every panel file (one agency x reporting_year slice) is
    collapsed to per-loan partials once, then the partials are folded per loan in
    hash shards -- the panel is read exactly once. Output is hive-partitioned by
    ``agency`` and ``orig_year`` (first-payment year, else first observed year).

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("loan_outcomes")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("loan_outcomes already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))
    perf_dir = CombinedConfig.dataset_path("loan_performance")
    term_dir = CombinedConfig.dataset_path("loan_terminations")
    iss_dir = CombinedConfig.dataset_path("loan_issuance")
    for required in (perf_dir, term_dir, iss_dir):
        if not required.exists():
            raise FileNotFoundError(f"loan_outcomes needs {required.name} built first: {required}")

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)
    tmp_dir = CombinedConfig.COMBINED_DATA_DIR / "_tmp_loan_outcomes"
    recreate_dir(tmp_dir)

    terminations = pl.scan_parquet(str(term_dir), hive_partitioning=True).select(
        "agency", "loan_key", "zero_balance_code", "upb_at_removal", "actual_loss"
    )
    issuance = pl.scan_parquet(str(iss_dir), hive_partitioning=True).select(
        "agency", "loan_key", "first_payment_date", "original_upb"
    )
    rpl = _fhlmc_rpl_map()

    agency_dirs = sorted(perf_dir.glob("agency=*"))
    logger.info(
        "Building combined loan_outcomes (%s) -> %s",
        "+".join(d.name.split("=", 1)[1] for d in agency_dirs),
        out_dir,
    )
    total = 0
    for agency_dir in agency_dirs:
        agency = agency_dir.name.split("=", 1)[1]
        partial_files = _loan_outcomes_partials(agency_dir, tmp_dir)
        partials = pl.scan_parquet([str(p) for p in partial_files])
        agency_info = (
            partials.select(pl.col("_mod_info").any(), pl.col("_plan_info").any())
            .collect(engine="streaming")
            .row(0, named=True)
        )
        logger.info(
            "  %s: %d partial files; discloses modification=%s assistance=%s",
            agency,
            len(partial_files),
            agency_info["_mod_info"],
            agency_info["_plan_info"],
        )
        for shard in range(_LOAN_OUTCOMES_SHARDS):
            reduced = (
                partials.filter(pl.col("loan_key").hash(seed=0) % _LOAN_OUTCOMES_SHARDS == shard)
                .group_by("loan_key")
                .agg(loan_outcome_reduce_aggs())
                .with_columns(pl.lit(agency).alias("agency"))
            )
            frame = (
                _finalize_loan_outcomes(reduced, agency_info)
                .join(terminations, on=["agency", "loan_key"], how="left")
                .join(issuance, on=["agency", "loan_key"], how="left")
                .join(rpl, on="loan_key", how="left")
                .with_columns(
                    pl.when(pl.col("agency") == "FHLMC")
                    .then(pl.col("rpl_deal_loan_id").is_not_null())
                    .otherwise(None)
                    .alias("is_rpl"),
                    _months_between(pl.col("termination_period"), pl.col("first_payment_date"))
                    .cast(pl.Int32)
                    .alias("months_to_termination"),
                    pl.coalesce(
                        pl.col("first_payment_date").dt.year().cast(pl.Int64),
                        pl.col("first_reporting_period") // 100,
                    ).alias("orig_year"),
                )
                .collect(engine="streaming")
            )
            for key, part in frame.partition_by("agency", "orig_year", as_dict=True).items():
                ag, year = key
                part_dir = out_dir / f"agency={ag}" / f"orig_year={year}"
                part_dir.mkdir(parents=True, exist_ok=True)
                part.drop("agency", "orig_year").write_parquet(
                    part_dir / f"{ag.lower()}_{year}_s{shard}.parquet"
                )
            total += frame.height
            logger.debug("    %s/shard %d: %d loans", agency, shard, frame.height)
        for p in partial_files:
            p.unlink(missing_ok=True)
        logger.info("  %s: done", agency)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("loan_outcomes built: %d loans -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


# ===================== mbs_issuance (security / pool grain) =================
# One row per security/pool at issuance. GSE spine = umbs/silver/{FNMA,FHLMC}/IS
# (new-issuance-only; FNMA & FHLMC share an identical 99-col schema, as-published
# dollars/percent — no scaling). GNMA spine = gnma/silver/nimonSFPS PS record
# (already decoded; only DTI needs x100, a fraction), extended back to 2012-02 by
# the legacy nissues D pool header (single-family scope + the pre-2017 Platinum
# pool-of-pools rows) and joined by Platinum first observations from factorAplat.
# CUSIP is the universal key. The clean tri-agency window is 2020-01+ (GSE alone
# reaches 2019-06; GNMA alone reaches 2012-02) — filter `_source_dataset` to pick a
# homogeneous window. Spec §6 corrections verified against silver: both GSEs' Issue
# Date is MMDDYYYY (not FHLMC=MMYYYY), and GNMA PS Original Aggregate Amount is
# already USD (not /100).

#: GNMA Pool Type codes that denote an ARM (security carries a margin).
_GNMA_ARM_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.is_arm
)
#: GNMA Pool Type codes outside the single-family scope of the modern nimonSFPS PS
#: feed. The legacy nissues D record covers the whole Ginnie universe, so the
#: multifamily project and construction families are dropped to keep one scope
#: across the seam (they are 20,131 rows / $225.1B, and a multifamily issuance
#: master is separate work). Derived from the family axis rather than a literal set
#: so a new project code is picked up automatically.
_GNMA_PROJECT_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.family in ("project", "construction")
)
#: GNMA Platinum (pool-of-pools) codes — a code space of their own, disjoint from
#: the base forward-MBS pool types. Rows carrying one are resecuritizations.
_GNMA_PLATINUM_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.family == "platinum"
)
#: GNMA Pool Indicator -> issue_type enum (Appendix III-6 instr. 15).
_GNMA_ISSUE_TYPE_ENUM: dict[str, str] = {"X": "single_issuer", "C": "custom", "M": "multi_issuer"}
#: ``program`` value for the Platinum layer (the lowercase cross-master vocabulary).
_GNMA_PLATINUM_PROGRAM: str = "ginnie_platinum"
#: Fixed-point 9-fills that survive canonical decoding of the legacy nissues D
#: record and must be scrubbed to null by the assembler. The AOLS family has TWO
#: forms because D has two fixed-width eras: an 8-character field through 2015-03
#: and a 10-character one from 2015-04.
_NISSUES_RATE_SENTINEL: float = 99.999  # 5-char x1000 rates: security rate, WAC, WAGM
_NISSUES_SIZE_SENTINELS: tuple[float, float] = (999999.99, 99999999.99)  # AOLS family
_NISSUES_MONTHS_SENTINEL: int = 999  # 3-char ints: WARM, WALA, WAOLT, LTV, CLTV
_NISSUES_SCORE_SENTINEL: int = 999  # 3-char int: U-record credit score
_NISSUES_DTI_SENTINEL: float = 9.999  # 4-char x1000 fraction: U-record DTI


def _canon_slug(name: str) -> str:
    """Canonical snake_case id for a verbose GNMA column (drop parens; matches the schema id)."""
    s = re.sub(r"\([^)]*\)", " ", name)
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


def _pick(names: list[str], available: set[str], dtype: pl.DataType | type[pl.DataType]) -> pl.Expr:
    """Coalesce the present source columns, or a typed null if none exist (vintage-robust).

    Falls back to the canonical snake_case ids (parens dropped) ONLY when none of the verbose
    names are present — so legacy verbose silver (GSE/factorA) is unaffected, while canonical
    GNMA silver (nimonSFPS/monthlySFPS, snake_case) resolves via the slug.

    Returns:
        A coalescing expression, or a typed null when no source column is present.
    """
    present = [n for n in names if n in available]
    if not present:
        present = [s for n in names if (s := _canon_slug(n)) in available]
    if not present:
        return pl.lit(None, dtype=dtype)
    return pl.coalesce([pl.col(n) for n in present]) if len(present) > 1 else pl.col(present[0])


def _gse_program(issue_date_ymd: pl.Expr) -> pl.Expr:
    """GSE ``program`` from the security's issue date: UMBS from 2019-06, else MBS.

    The Single Security Initiative went live 2019-06-03, so a GSE security issued on
    or after it is a UMBS and everything earlier (Fannie MBS, Freddie Gold PCs) is
    not. One rule for both security-grain masters, so they agree row for row.

    Returns:
        A String expression yielding ``UMBS`` or ``MBS``.
    """
    return (
        pl.when(issue_date_ymd >= UMBS_ERA_START)
        .then(pl.lit("umbs"))
        .otherwise(pl.lit("mbs"))
        .alias("program")
    )


def _gse_structure_flags(
    av: set[str], *, agency: str, issue_date_ymd: pl.Expr
) -> tuple[pl.Expr, pl.Expr]:
    """Classify a GSE security from the seller/servicer resecuritization marker.

    Both GSE feeds put a code rather than a firm in ``Seller Name`` **and**
    ``Servicer Name`` when a security is backed by other securities — see
    ``GSE_RESEC_SELLER_PREFIX`` for the evidence and the double-count warning it
    carries. Shared by ``mbs_issuance`` and ``mbs_performance`` so one rule produces
    one vocabulary on both grains.

    The marker sits in either name field (occasionally only the servicer side), so
    they are OR-ed. A row that blanks the whole descriptive block — a paid-off or
    cancelled security — yields NULL rather than a false ``pass_through``; the
    performance builder fills those from the pool's other months, and at the
    issuance grain there is no other month, so the null stands.

    Args:
        av: Column names available on the source frame.
        agency: ``FNMA`` or ``FHLMC`` (selects the legacy resec class name).
        issue_date_ymd: The security's issue date as ``Int32`` ``YYYYMMDD``.

    Returns:
        ``(structure_class, is_resecuritization)`` expressions, unaliased.
    """
    names = [
        _pick([col], av, pl.String).cast(pl.String).str.strip_chars().str.to_uppercase()
        for col in ("Seller Name", "Servicer Name")
    ]
    block_present = pl.any_horizontal([n.is_not_null() & (n != "") for n in names])
    is_resec = pl.any_horizontal(
        [n.str.starts_with(GSE_RESEC_SELLER_PREFIX) for n in names]
    ).fill_null(value=False)
    is_mirror = pl.any_horizontal(
        [n.str.starts_with(GSE_MIRROR_SELLER_PREFIX) for n in names]
    ).fill_null(value=False)
    legacy_resec_class = "mega" if agency == "FNMA" else "giant"
    structure_class = (
        # Mirrors carry the underlying Gold PC's issue date (mostly 2018), so they
        # are classified by the marker itself, ahead of the era split.
        pl.when(~block_present)
        .then(pl.lit(None, dtype=pl.String))
        .when(is_mirror)
        .then(pl.lit("mirror"))
        .when(is_resec & (issue_date_ymd >= UMBS_ERA_START))
        .then(pl.lit("super"))
        .when(is_resec)
        .then(pl.lit(legacy_resec_class))
        .otherwise(pl.lit("pass_through"))
    )
    return structure_class, pl.when(block_present).then(is_resec)


def _assemble_gse_mbs_issuance(lf: pl.LazyFrame, *, agency: str, month: str) -> pl.LazyFrame:
    """Map one GSE IS file (FNMA or FHLMC — identical schema) to the unified grain.

    GSE IS date columns are already ``pl.Date`` in the umbs silver layer (parsed
    bronze->silver via ``core.dates``), so they are read straight through.

    ``_source_dataset`` distinguishes the two physical feeds folded into this silver:
    the ``IS`` new-issuance file and its ``RIS`` republication, which the silver marks
    on ``_record_source``. ``record_source`` keeps its {original, correction} meaning.

    Returns:
        The file mapped to the unified ``mbs_issuance`` grain.
    """
    av = set(lf.collect_schema().names())
    issue_date = _pick(["Issue Date"], av, pl.Date)
    issue_date_ymd = _date_to_yyyymmdd(issue_date)
    structure_class, is_resec = _gse_structure_flags(
        av, agency=agency, issue_date_ymd=issue_date_ymd
    )
    record_source = _pick(["_record_source"], av, pl.String)
    margin = _pick(["WA Mortgage Margin"], av, pl.Float64).cast(pl.Float64, strict=False)
    index_present = _pick(["Index"], av, pl.String).cast(pl.String).str.strip_chars()
    return lf.select(
        pl.lit(agency).alias("agency"),
        pl.col("Security Identifier").cast(pl.String).alias("pool_id"),
        pl.col("CUSIP").cast(pl.String).alias("cusip"),
        _pick(["Prefix"], av, pl.String).alias("prefix"),
        _gse_program(issue_date_ymd),
        _pick(["Subtype"], av, pl.String).alias("pool_type_raw"),
        structure_class.alias("structure_class"),
        pl.lit(None, dtype=pl.String).alias("issue_type"),
        issue_date.alias("issue_date"),
        _pick(["Maturity Date"], av, pl.Date).alias("maturity_date"),
        pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
        _pick(["Loan Count"], av, pl.Int64).cast(pl.Int64, strict=False).alias("loan_count"),
        _pick(["Issuance Investor Security UPB"], av, pl.Float64).alias("original_face"),
        _pick(["Current Investor Security UPB"], av, pl.Float64).alias("current_face"),
        pl.lit(None, dtype=pl.Float64).alias("pool_upb"),
        _pick(["Security Factor"], av, pl.Float64).alias("security_factor"),
        _pick(["WA Net Interest Rate"], av, pl.Float64).alias("pass_through_rate"),
        _pick(["WA Issuance Interest Rate", "WA Current Interest Rate"], av, pl.Float64).alias(
            "wac"
        ),
        _pick(["WA Net Accrual Interest Rate"], av, pl.Float64).alias("wa_net_rate"),
        _pick(
            ["Average Origination Mortgage Loan Amount", "Average Mortgage Loan Amount"],
            av,
            pl.Float64,
        ).alias("avg_loan_size"),
        _pick(
            ["WA Origination Mortgage Loan Amount", "WA Mortgage Loan Amount"], av, pl.Float64
        ).alias("wa_loan_size"),
        _pick(["WA Origination Loan Term", "WA Loan Term"], av, pl.Float64).alias(
            "wa_orig_loan_term"
        ),
        _pick(["WA Issuance Remaining Months to Maturity"], av, pl.Float64).alias("wa_rem_term"),
        _pick(["WA Loan Age"], av, pl.Float64).alias("wa_loan_age"),
        _pick(
            ["WA Origination Loan-To-Value (LTV)", "WA Loan-To-Value (LTV)"], av, pl.Float64
        ).alias("wa_ltv"),
        _pick(
            ["WA Origination Combined Loan-To-Value (CLTV)", "WA Combined Loan-To-Value (CLTV)"],
            av,
            pl.Float64,
        ).alias("wa_cltv"),
        _pick(
            ["WA Origination Debt-To-Income (DTI)", "WA Debt-To-Income (DTI)"], av, pl.Float64
        ).alias("wa_dti"),
        _pick(["WA Origination Classic FICO", "WA Classic FICO"], av, pl.Float64).alias(
            "wa_credit_score"
        ),
        _pick(["WA Origination VS4", "WA VS4"], av, pl.Float64).alias("wa_vs4"),
        margin.alias("wa_margin"),
        index_present.alias("index_type"),
        ((index_present.str.len_chars() > 0) | (margin > 0)).fill_null(value=False).alias("is_arm"),
        (_pick(["Interest Only Security Indicator"], av, pl.String) == "Y").alias(
            "is_interest_only"
        ),
        _pick(
            [
                "Origination Third Party Origination UPB Percent",
                "Third Party Origination UPB Percent",
            ],
            av,
            pl.Float64,
        ).alias("tpo_upb_pct"),
        pl.lit(None, dtype=pl.String).alias("issuer_id"),
        _pick(["Issuer"], av, pl.String).alias("issuer_name"),
        _pick(["Seller Name"], av, pl.String).alias("seller_name"),
        _pick(["Servicer Name"], av, pl.String).alias("servicer_name"),
        # Booleanized on both sides: the GSE silver stores these as Boolean and GNMA
        # as Y/N strings, so leaving them to dtype supertyping persists "true"/"false"
        # text beside "Y"/"N".
        _yn_bool(_pick(["Social Indicator"], av, pl.String)).alias("social_indicator"),
        _yn_bool(_pick(["Green Indicator"], av, pl.String)).alias("green_indicator"),
        is_resec.alias("is_resecuritization"),
        record_source.alias("record_source"),
        pl.when(record_source == "correction")
        .then(pl.lit("RIS"))
        .otherwise(pl.lit("IS"))
        .alias("_source_dataset"),
        issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
    )


def _assemble_gnma_mbs_issuance(lf: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one GNMA nimonSFPS PS file to the unified mbs_issuance grain.

    Single-family, base pools only: the feed carries no multifamily and no Platinum,
    so every row is ``pass_through`` / ``is_resecuritization = False``. The pre-2020
    history comes from the legacy ``nissues`` D record instead.

    Returns:
        The file mapped to the unified ``mbs_issuance`` grain.
    """
    av = set(lf.collect_schema().names())

    def num(names: list[str]) -> pl.Expr:
        return _pick(names, av, pl.String).cast(pl.Float64, strict=False)

    pind = _pick(["Pool Indicator"], av, pl.String).cast(pl.String).str.strip_chars()
    ptype = _pick(["Pool Type"], av, pl.String).cast(pl.String).str.strip_chars()
    issue_date = yyyymmdd_to_date(_pick(["Issue Date"], av, pl.String))
    wagm = num(["WA Gross Margin (WAGM)"])
    return lf.select(
        pl.lit("GNMA").alias("agency"),
        _pick(["Pool ID"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        _pick(["CUSIP Number"], av, pl.String).cast(pl.String).str.strip_chars().alias("cusip"),
        pl.lit(None, dtype=pl.String).alias("prefix"),
        pind.replace_strict(GNMA_PROGRAM_BY_ISSUE_TYPE, default=None).alias("program"),
        ptype.alias("pool_type_raw"),
        pl.lit("pass_through").alias("structure_class"),
        pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None).alias("issue_type"),
        issue_date.alias("issue_date"),
        yyyymmdd_to_date(_pick(["Maturity Date"], av, pl.String)).alias("maturity_date"),
        pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
        num(["Number of loans in pool"]).cast(pl.Int64, strict=False).alias("loan_count"),
        num(["Original Aggregate Amount"]).alias("original_face"),  # already USD (not /100)
        num(["Remaining Security RPB"]).alias("current_face"),
        num(["Pool UPB"]).alias("pool_upb"),
        num(["RPB Factor"]).alias("security_factor"),
        num(["Security Interest Rate"]).alias("pass_through_rate"),
        # The "at Issuance" twin is NOT read for the rate. In PS silver it is populated
        # only for 2020-01..2021-02 and there holds the SECURITY rate, not the WAC
        # (equal to `security_interest_rate` on 100% of rows in each of those 14
        # months); from 2021-03 it is 100% null. Coalescing it recorded
        # wac == pass_through_rate on 32,891 rows, a ~44-50bp understatement of the
        # gross note rate, so it is dropped rather than demoted to a fallback — as a
        # fallback it still injected the security rate on the 45 rows where the real
        # column is null. The WALA/WARM/WAOLT twins below are null in every month, so
        # their coalesce order is harmless and is left as published.
        num(["WA Interest Rate (WAC)"]).alias("wac"),
        pl.lit(None, dtype=pl.Float64).alias("wa_net_rate"),
        num(["Average Original Loan Size (AOLS)"]).alias("avg_loan_size"),
        num(["WA Original Loan Size"]).alias("wa_loan_size"),
        num(["WA Original Loan Term (WAOLT) at Issuance", "WA Original Loan Term (WAOLT)"]).alias(
            "wa_orig_loan_term"
        ),
        num(
            [
                "WA Remaining Months to Maturity (WARM) at Issuance",
                "WA Remaining Months to Maturity (WARM)",
            ]
        ).alias("wa_rem_term"),
        num(["WA Loan Age (WALA) at Issuance", "WA Loan Age (WALA)"]).alias("wa_loan_age"),
        num(["WA Loan to Value (LTV)"]).alias("wa_ltv"),
        num(["WA Combined Loan to Value (CLTV)"]).alias("wa_cltv"),
        (num(["WA Debt to Income"]) * 100.0).alias("wa_dti"),  # GNMA PS DTI is a fraction
        num(["WA Credit Score"]).alias("wa_credit_score"),
        pl.lit(None, dtype=pl.Float64).alias("wa_vs4"),
        wagm.alias("wa_margin"),
        pl.lit(None, dtype=pl.String).alias("index_type"),
        (ptype.is_in(list(_GNMA_ARM_POOL_TYPES)) | (wagm > 0))
        .fill_null(value=False)
        .alias("is_arm"),
        pl.lit(None, dtype=pl.Boolean).alias("is_interest_only"),
        pl.lit(None, dtype=pl.Float64).alias("tpo_upb_pct"),
        _pick(["Issuer Number"], av, pl.String)
        .cast(pl.String)
        .str.strip_chars()
        .alias("issuer_id"),
        _pick(["Issuer Name"], av, pl.String).alias("issuer_name"),
        pl.lit(None, dtype=pl.String).alias("seller_name"),
        pl.lit(None, dtype=pl.String).alias("servicer_name"),
        _yn_bool(_pick(["Social Indicator"], av, pl.String)).alias("social_indicator"),
        pl.lit(None, dtype=pl.Boolean).alias("green_indicator"),
        pl.lit(value=False).alias("is_resecuritization"),
        pl.lit("original").alias("record_source"),  # GNMA has no reissue twin
        pl.lit("nimonSFPS_PS").alias("_source_dataset"),
        issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
    )


def _nissues_u_enrichment(u_file: Path) -> pl.LazyFrame:
    """Scan one ``nissues`` U file to the credit-score / DTI enrichment keyed on pool.

    The D (Pool Details) record carries no credit score and no DTI; the U record
    carries both for the same pool and month. Scored on the 16-month overlap with the
    modern feed: the credit score agrees with ``nimonSFPS`` PS **exactly** (max
    absolute difference 0.0 over 36,646 pairs), so it is carried without reservation.
    The DTI agrees to 0.05pp on 89.2% of pairs and to 5pp on 99.2% — close but not
    identical, because U reports the issuance-month weighted average and PS the
    current-month one, so it is carried as approximate rather than exact.

    Args:
        u_file: Path to one ``nissues_YYYYMM_U`` file.

    Returns:
        A LazyFrame keyed on ``pool_id`` with ``wa_credit_score`` / ``wa_dti``.
    """
    scan = pl.scan_parquet(str(u_file))
    av = set(scan.collect_schema().names())
    score = _pick(["weighted_average_credit_score"], av, pl.Float64).cast(pl.Float64, strict=False)
    dti = _pick(["weighted_average_debt_income_ratio"], av, pl.Float64).cast(
        pl.Float64, strict=False
    )
    return (
        scan.select(
            _pick(["pool_number"], av, pl.String)
            .cast(pl.String)
            .str.strip_chars()
            .alias("pool_id"),
            pl.when(score != _NISSUES_SCORE_SENTINEL).then(score).alias("wa_credit_score"),
            # U stores DTI as a fraction; x100 gives the percent the PS assembler emits.
            pl.when(dti != _NISSUES_DTI_SENTINEL).then(dti * 100.0).alias("wa_dti"),
        )
        .unique()
        .unique(subset=["pool_id"], keep="none")
    )


def _assemble_gnma_legacy_mbs_issuance(
    lf: pl.LazyFrame, *, month: str, u_file: Path | None = None
) -> pl.LazyFrame:
    """Map one legacy GNMA ``nissues`` D file to the unified mbs_issuance grain.

    ``nissues`` D is the pool-at-issuance header GNMA published before ``nimonSFPS``
    PS (2012-02 -> 2021-04, one row per newly-issued pool, no cross-month
    re-reporting). Its canonical silver is already decoded — real USD, percent and
    ``pl.Date`` — so the work here is sentinel scrubbing, scope and vocabulary.

    Scope: D covers the whole Ginnie universe, PS only single-family, so the
    multifamily project and construction families are dropped
    (`_GNMA_PROJECT_POOL_TYPES`). The Platinum pool-of-pools rows (``SP``/``JP``
    ``78xxxx`` pools, issuer 9999, zero loan count and pool UPB, gone from the feed
    after 2017-07) are KEPT, flagged as resecuritizations — they are the pre-2019
    Platinum issuance history that ``factorAplat`` cannot reach.

    Field agreement against PS over the 16-month overlap: ``original_face``,
    ``loan_count``, ``pass_through_rate``, ``wa_ltv``, ``wa_cltv``, ``avg_loan_size``,
    ``wa_loan_size``, ``issue_date``, ``maturity_date``, ``pool_type_raw``,
    ``issue_type``, ``program``, ``cusip`` and ``issuer_id`` are exact on 100.000% of
    37,023 pairs; ``pool_upb`` is within $1 on 100%; the term/WARM/WALA trio is within
    one month on >=99.99%.

    Args:
        lf: A scan over one ``nissues_YYYYMM_D`` file.
        month: The as-of ``YYYYMM`` (from the filename).
        u_file: Optional sibling ``nissues_YYYYMM_U`` file supplying credit score and
            DTI, which the D record does not carry.

    Returns:
        A LazyFrame at the canonical mbs_issuance grain.
    """
    av = set(lf.collect_schema().names())

    def col(name: str, dtype: pl.DataType | type[pl.DataType] = pl.Float64) -> pl.Expr:
        return _pick([name], av, dtype)

    def rate(name: str) -> pl.Expr:
        v = col(name).cast(pl.Float64, strict=False)
        return pl.when(v != _NISSUES_RATE_SENTINEL).then(v)

    def months(name: str) -> pl.Expr:
        v = col(name).cast(pl.Float64, strict=False)
        return pl.when(v != _NISSUES_MONTHS_SENTINEL).then(v)

    def size(name: str) -> pl.Expr:
        v = col(name).cast(pl.Float64, strict=False)
        return pl.when(~v.is_in(list(_NISSUES_SIZE_SENTINELS))).then(v)

    ptype = col("pool_type", pl.String).cast(pl.String).str.strip_chars()
    pind = col("pool_indicator", pl.String).cast(pl.String).str.strip_chars()
    is_platinum = ptype.is_in(list(_GNMA_PLATINUM_POOL_TYPES))
    wagm = rate("wagm")
    issue_date = col("pool_issue_date", pl.Date)
    loans = col("number_of_loans_in_pool", pl.Int64).cast(pl.Int64, strict=False)
    pool_upb = col("pool_upb")
    mapped = (
        lf.filter(~ptype.is_in(list(_GNMA_PROJECT_POOL_TYPES)))
        .select(
            pl.lit("GNMA").alias("agency"),
            col("pool_number", pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
            col("cusip_number", pl.String).cast(pl.String).str.strip_chars().alias("cusip"),
            pl.lit(None, dtype=pl.String).alias("prefix"),
            pl.when(is_platinum)
            .then(pl.lit(_GNMA_PLATINUM_PROGRAM))
            .otherwise(pind.replace_strict(GNMA_PROGRAM_BY_ISSUE_TYPE, default=None))
            .alias("program"),
            ptype.alias("pool_type_raw"),
            pl.when(is_platinum)
            .then(pl.lit("platinum"))
            .otherwise(pl.lit("pass_through"))
            .alias("structure_class"),
            pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None).alias("issue_type"),
            issue_date.alias("issue_date"),
            col("pool_maturity_date", pl.Date).alias("maturity_date"),
            pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
            # 0 is the "not applicable" fill, and only Platinum rows carry it.
            pl.when(loans > 0).then(loans).alias("loan_count"),
            col("original_aggregate_amount").alias("original_face"),
            col("original_aggregate_amount").alias("current_face"),  # factor 1.0 at issuance
            pl.when(pool_upb > 0).then(pool_upb).alias("pool_upb"),
            pl.lit(1.0, dtype=pl.Float64).alias("security_factor"),
            rate("security_interest_rate").alias("pass_through_rate"),
            rate("wac").alias("wac"),
            pl.lit(None, dtype=pl.Float64).alias("wa_net_rate"),
            size("average_original_loan_size").alias("avg_loan_size"),
            size("weighted_average_original_loan_size").alias("wa_loan_size"),
            months("waolt").alias("wa_orig_loan_term"),
            months("warm").alias("wa_rem_term"),
            months("wala").alias("wa_loan_age"),
            months("weighted_average_original_ltv").alias("wa_ltv"),
            months("weighted_average_original_cltv").alias("wa_cltv"),
            pl.lit(None, dtype=pl.Float64).alias("wa_vs4"),
            wagm.alias("wa_margin"),
            pl.lit(None, dtype=pl.String).alias("index_type"),  # lives in the N record (2017-12+)
            (ptype.is_in(list(_GNMA_ARM_POOL_TYPES)) | (wagm > 0))
            .fill_null(value=False)
            .alias("is_arm"),
            pl.lit(None, dtype=pl.Boolean).alias("is_interest_only"),
            pl.lit(None, dtype=pl.Float64).alias("tpo_upb_pct"),
            col("issuer_number", pl.String).cast(pl.String).str.strip_chars().alias("issuer_id"),
            col("issuer_name", pl.String).cast(pl.String).str.strip_chars().alias("issuer_name"),
            pl.lit(None, dtype=pl.String).alias("seller_name"),
            pl.lit(None, dtype=pl.String).alias("servicer_name"),
            pl.lit(None, dtype=pl.Boolean).alias("social_indicator"),  # GNMA social starts 2024-09
            pl.lit(None, dtype=pl.Boolean).alias("green_indicator"),
            is_platinum.alias("is_resecuritization"),
            pl.lit("original").alias("record_source"),  # GNMA has no reissue twin
            pl.lit("nissues_D").alias("_source_dataset"),
            issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
            # 2015-11 carries one byte-identical duplicate row (pool 784062).
        )
        .unique()
        .unique(subset=["pool_id", "as_of_month"], keep="none")
    )
    if u_file is not None and u_file.exists():
        return mapped.join(_nissues_u_enrichment(u_file), on="pool_id", how="left")
    return mapped.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("wa_credit_score"),
        pl.lit(None, dtype=pl.Float64).alias("wa_dti"),
    )


def _gnma_platinum_wa_issuance(pps_file: Path, month: str) -> pl.LazyFrame:
    """Scan one ``platmonPPS`` PS file to the Platinum WA block, keyed on pool x month.

    ``platmonPPS`` PS is the Platinum analog of ``monthlySFPS`` PS and is already
    decoded. Its four "at Issuance" WA columns are unpopulated by design (null in
    every month bar a 2021-01/02 artifact that just repeats the security coupon), so
    the current-month block is what is read — which IS the issuance block here,
    because a Platinum's first ``factorAplat`` observation is its issue month.

    Args:
        pps_file: Path to one ``platmonPPS_YYYYMM_PS`` file.
        month: The as-of ``YYYYMM`` (the join key ``as_of_month``).

    Returns:
        A LazyFrame keyed on ``(pool_id, as_of_month)`` with the WA columns.
    """
    scan = pl.scan_parquet(str(pps_file))
    av = set(scan.collect_schema().names())

    def num(name: str) -> pl.Expr:
        return _pick([name], av, pl.Float64).cast(pl.Float64, strict=False)

    return (
        scan.select(
            _pick(["pool_id"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
            pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
            num("pool_upb").alias("pool_upb"),
            num("average_original_loan_size_weighted").alias("avg_loan_size"),
            num("wa_original_loan_size").alias("wa_loan_size"),
            num("wa_interest_rate").alias("wac"),
            num("wa_original_loan_term").alias("wa_orig_loan_term"),
            num("wa_remaining_months_to_maturity").alias("wa_rem_term"),
            num("wa_loan_age").alias("wa_loan_age"),
            num("wa_loan_to_value").alias("wa_ltv"),
            num("wa_combined_loan_to_value").alias("wa_cltv"),
            num("wa_credit_score").alias("wa_credit_score"),
            (num("wa_debt_to_income") * 100.0).alias("wa_dti"),  # a fraction in the source
            num("wa_gross_margin").alias("wa_margin"),
        )
        .unique()
        .unique(subset=["pool_id", "as_of_month"], keep="none")
    )


def _assemble_gnma_platinum_mbs_issuance(lf: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one ``factorAplat`` file to the Platinum leg of the mbs_issuance grain.

    GNMA publishes no Platinum new-issuance file, so a Platinum issuance record is a
    FIRST-OBSERVATION record — which the data licenses: for all 3,700 Platinum
    securities issued inside the ``factorAplat`` window the lag between the pool's
    issue month and the first month it appears is 0 months, 100.0% of the time, and
    the static fields (issue/maturity date, original face, coupon, pool type) do not
    move month to month. The caller reduces the concatenated months to the first
    observation per pool; this assembler only maps one month.

    HMBS Platinum is excluded by ``Pool Indicator`` rather than pool type
    (``GNMA_PLATINUM_HMBS_INDICATOR``), because Platinum pool types are a separate
    code space from the base-panel HMBS set. The WA block is left-joined from
    ``platmonPPS`` by the caller and so is not emitted here.

    Args:
        lf: A scan over one ``factorAplat_YYYYMM_ALL`` file.
        month: The as-of ``YYYYMM`` (from the filename).

    Returns:
        A LazyFrame at the canonical grain, minus the joined WA columns.
    """
    av = set(lf.collect_schema().names())
    cusip = _pick(["cusip_number"], av, pl.String).cast(pl.String).str.strip_chars()
    ptype = _pick(["pool_type"], av, pl.String).cast(pl.String).str.strip_chars()
    pind = _pick(["pool_indicator"], av, pl.String).cast(pl.String).str.strip_chars()
    issue_date = _pick(["pool_issue_date"], av, pl.Date)
    return lf.filter(
        (cusip != "") & (pind != GNMA_PLATINUM_HMBS_INDICATOR)
    ).select(
        pl.lit("GNMA").alias("agency"),
        _pick(["pool_id"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        cusip.alias("cusip"),
        pl.lit(None, dtype=pl.String).alias("prefix"),
        pl.lit(_GNMA_PLATINUM_PROGRAM).alias("program"),
        ptype.alias("pool_type_raw"),
        pl.lit("platinum").alias("structure_class"),
        pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None).alias("issue_type"),
        issue_date.alias("issue_date"),
        _pick(["pool_maturity_date"], av, pl.Date).alias("maturity_date"),
        pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
        pl.lit(None, dtype=pl.Int64).alias("loan_count"),  # not disclosed for Platinum
        _gnma_upb_dec(_pick(["original_aggregate_amount"], av, pl.Float64)).alias("original_face"),
        _gnma_upb_dec(_pick(["remaining_security_rpb"], av, pl.Float64)).alias("current_face"),
        # Left as published: RPB factor exceeds 1 on a handful of securities a month
        # (a publisher artifact), and clipping would silently alter those rows.
        _pick(["rpb_factor"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("security_factor"),
        _gnma_rate_dec(_pick(["pool_interest_rate"], av, pl.Float64)).alias("pass_through_rate"),
        pl.lit(None, dtype=pl.Float64).alias("wa_net_rate"),
        pl.lit(None, dtype=pl.Float64).alias("wa_vs4"),
        pl.lit(None, dtype=pl.String).alias("index_type"),
        ptype.is_in(list(_GNMA_ARM_POOL_TYPES)).fill_null(value=False).alias("is_arm"),
        pl.lit(None, dtype=pl.Boolean).alias("is_interest_only"),
        pl.lit(None, dtype=pl.Float64).alias("tpo_upb_pct"),
        _pick(["issuer_number"], av, pl.String)
        .cast(pl.String)
        .str.strip_chars()
        .alias("issuer_id"),
        _pick(["issuer_name"], av, pl.String)
        .cast(pl.String)
        .str.strip_chars()
        .alias("issuer_name"),
        pl.lit(None, dtype=pl.String).alias("seller_name"),
        pl.lit(None, dtype=pl.String).alias("servicer_name"),
        pl.lit(None, dtype=pl.Boolean).alias("social_indicator"),
        pl.lit(None, dtype=pl.Boolean).alias("green_indicator"),
        pl.lit(value=True).alias("is_resecuritization"),
        pl.lit("original").alias("record_source"),
        pl.lit("factorAplat").alias("_source_dataset"),
        issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
    )


def _build_gnma_platinum_mbs_issuance(
    plat_dir: Path, pps_dir: Path, exclude_pools: set[str]
) -> pl.DataFrame:
    """Reduce the ``factorAplat`` panel to one issuance row per Platinum security.

    ``factorAplat`` is a portfolio file, not a new-issuance file, so the whole panel
    is read and collapsed to each security's first observed month, then enriched with
    that month's ``platmonPPS`` WA block.

    Securities whose first observation is NOT their issue month are pre-window
    (``factorAplat`` starts 2019-06): their static fields are still exact, but their
    balance, factor and WA block would be a mid-life snapshot — median factor 0.062 —
    so those point-in-time fields are nulled rather than persisted into an issuance
    master.

    Args:
        plat_dir: ``factorAplat/ALL`` silver directory.
        pps_dir: ``platmonPPS/PS`` silver directory.
        exclude_pools: Pool ids already carried by the legacy ``nissues`` D leg, which
            holds the true at-issuance record for the Platinum securities it covers.

    Returns:
        One row per Platinum security, at the canonical grain.
    """
    frames = [
        _assemble_gnma_platinum_mbs_issuance(pl.scan_parquet(str(f)), month=f.stem.split("_")[-2])
        for f in sorted(plat_dir.glob("*.parquet"))
    ]
    if not frames:
        return pl.DataFrame()
    first = (
        pl.concat(frames, how="diagonal_relaxed")
        .filter(~pl.col("pool_id").is_in(list(exclude_pools)))
        # each pool's first observation; a pool with two differing rows that month gets none
        .filter(pl.col("as_of_month") == pl.col("as_of_month").min().over("pool_id"))
        .unique()
        .unique(subset=["pool_id"], keep="none")
        .collect(engine="streaming")
    )
    wa_files = sorted(pps_dir.glob("*.parquet"))
    wa = (
        pl.concat(
            [_gnma_platinum_wa_issuance(f, f.stem.split("_")[-2]) for f in wa_files],
            how="diagonal_relaxed",
        ).collect(engine="streaming")
        if wa_files
        else None
    )
    if wa is not None:
        first = first.join(wa, on=["pool_id", "as_of_month"], how="left")
    issue_month = pl.col("issue_date").dt.year().cast(pl.Int64) * 100 + pl.col(
        "issue_date"
    ).dt.month().cast(pl.Int64)
    at_issuance = pl.col("as_of_month") == issue_month
    point_in_time = [
        c for c in ("current_face", "security_factor", "pool_upb") if c in first.columns
    ]
    return first.with_columns(
        [pl.when(at_issuance).then(pl.col(c)).alias(c) for c in point_in_time]
    )


def _write_mbs_chunk(df: pl.DataFrame, out_dir: Path, agency: str, tag: str) -> int:
    """Write one file's mbs_issuance rows into the ``agency=/issue_year=`` hive layout."""
    if df.is_empty():
        return 0
    for key, part in df.partition_by("issue_year", as_dict=True).items():
        year = key[0] if isinstance(key, tuple) else key
        part_dir = out_dir / f"agency={agency}" / f"issue_year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part.drop("agency", "issue_year").write_parquet(part_dir / f"{tag}.parquet")
    return df.height


def _log_gnma_issuance_seam(nissues_dir: Path, ps_dir: Path, ps_first: int) -> None:
    """Log whether the modern GNMA feed still covers everything the legacy one reports.

    The two feeds are joined by a month cutoff, not a row-level dedup, which is only
    sound while ``nimonSFPS`` PS is a superset of ``nissues`` D's single-family scope
    over their overlap — it was, on 37,023 of 37,023 pools by both keys. This logs the
    containment on every build so a coverage regression surfaces as a warning instead
    of silently duplicating pools if the cutoff ever moves.
    """
    overlap = [
        f for f in sorted(nissues_dir.glob("*.parquet")) if int(f.stem.split("_")[1]) >= ps_first
    ]
    if not overlap:
        return
    out_of_scope = list(_GNMA_PROJECT_POOL_TYPES | _GNMA_PLATINUM_POOL_TYPES)
    legacy = (
        pl.scan_parquet([str(f) for f in overlap])
        .filter(~pl.col("pool_type").str.strip_chars().is_in(out_of_scope))
        .select(pl.col("pool_number").str.strip_chars().alias("pool_id"))
        .unique()
    )
    months = {f.stem.split("_")[1] for f in overlap}
    ps_files = [f for f in sorted(ps_dir.glob("*.parquet")) if f.stem.split("_")[-2] in months]
    if not ps_files:
        return
    modern = (
        pl.scan_parquet([str(f) for f in ps_files])
        .select(pl.col("pool_id").cast(pl.String).str.strip_chars())
        .unique()
    )
    missing = (
        legacy.join(modern, on="pool_id", how="anti").select(pl.len()).collect(engine="streaming")
    ).item()
    if missing:
        logger.warning(
            "  GNMA seam: %d single-family pools in legacy nissues D over the %d-month overlap "
            "are absent from nimonSFPS PS; the month cutoff now loses them.",
            missing,
            len(months),
        )
    else:
        logger.info(
            "  GNMA seam: nimonSFPS PS covers every legacy single-family pool over the "
            "%d-month overlap (month cutoff is sufficient).",
            len(months),
        )


def build_mbs_issuance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the tri-agency master MBS/pool issuance dataset (GNMA+FNMA+FHLMC).

    Spine = the per-agency single-class issuance records: GSE ``umbs/silver/
    {FNMA,FHLMC}/IS`` (new-issuance-only; identical 99-col schema) and GNMA
    ``gnma/silver/nimonSFPS`` PS pool headers (already decoded). One row per
    security/pool at issuance, keyed on CUSIP. Output is hive-partitioned by
    ``agency`` and ``issue_year`` under ``data/combined/mbs_issuance``.

    Two GNMA legs extend that spine, both tagged on ``_source_dataset`` so a
    homogeneous window can be selected:

      * **``nissues`` D** — the legacy pool-at-issuance header, 2012-02 -> the month
        before ``nimonSFPS`` PS begins. Single-family scope plus the pre-2017-08
        Platinum pool-of-pools rows. The cutoff is computed from the PS file list
        rather than written as a literal, and PS is a strict superset of D's
        single-family scope across their 16-month overlap (37,023 of 37,023 pools,
        both keys), so no dedup branch is needed — see the assertion logged below.
      * **``factorAplat``** — one first-observation row per GNMA Platinum security,
        which for in-window issues IS the issuance month. Pools already carried by
        the ``nissues`` leg are skipped there, since that leg holds the true
        at-issuance record for them.

    **This master is a union of overlapping security layers.** Platinum securities and
    GSE Supers/Giants/Megas/Mirrors wrap collateral that is itself in the master, so
    any aggregate over balances or counts must filter ``is_resecuritization = False``
    unless the question is about the wrapper.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("mbs_issuance")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("mbs_issuance already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)

    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
    gnma = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver"
    gnma_ps = gnma / "nimonSFPS" / "PS"
    gnma_nissues = gnma / "nissues" / "D"
    gnma_nissues_u = gnma / "nissues" / "U"
    gnma_plat = gnma / "factorAplat" / "ALL"
    gnma_plat_ps = gnma / "platmonPPS" / "PS"
    gse_dirs = {"FNMA": umbs / "FNMA" / "IS", "FHLMC": umbs / "FHLMC" / "IS"}

    # Canonical schema = union of every assembler's output (the GSE and modern GNMA
    # legs emit the same columns; the legacy and Platinum legs differ, and the union
    # both reconciles them and guards any future divergence).
    gse_sample = sorted(gse_dirs["FNMA"].glob("*.parquet"))
    gnma_sample = sorted(gnma_ps.glob("*.parquet"))
    legacy_sample = sorted(gnma_nissues.glob("*.parquet"))
    plat_sample = sorted(gnma_plat.glob("*.parquet"))
    samples = [
        _assemble_gse_mbs_issuance(
            pl.scan_parquet(str(gse_sample[-1])), agency="FNMA", month="209912"
        ),
        _assemble_gnma_mbs_issuance(pl.scan_parquet(str(gnma_sample[-1])), month="209912"),
    ]
    if legacy_sample:
        samples.append(
            _assemble_gnma_legacy_mbs_issuance(
                pl.scan_parquet(str(legacy_sample[-1])), month="209912"
            )
        )
    if plat_sample:
        samples.append(
            _assemble_gnma_platinum_mbs_issuance(
                pl.scan_parquet(str(plat_sample[-1])), month="209912"
            )
        )
    canon = pl.concat(
        [pl.DataFrame(schema=s.collect_schema()).lazy() for s in samples],
        how="diagonal_relaxed",
    ).collect_schema()

    logger.info("Building combined mbs_issuance (GNMA+FNMA+FHLMC) -> %s", out_dir)
    total = 0
    for agency, sdir in gse_dirs.items():
        files = sorted(sdir.glob("*.parquet"))
        logger.info("  %s: %d IS files", agency, len(files))
        for f in files:
            month = f.stem.split("_")[-1]  # IS_YYYYMM
            chunk = _assemble_gse_mbs_issuance(
                pl.scan_parquet(str(f)), agency=agency, month=month
            ).collect(engine="streaming")
            total += _write_mbs_chunk(
                _reindex_to_schema(chunk, canon), out_dir, agency, f"{agency.lower()}_{month}"
            )
    gnma_files = sorted(gnma_ps.glob("*.parquet"))
    # The legacy feed stops where the modern one starts. Read the boundary from the
    # PS file list rather than writing it as a literal, so a re-download that extends
    # PS backwards moves the seam instead of duplicating months.
    ps_first = min((int(f.stem.split("_")[-2]) for f in gnma_files), default=0)
    legacy_files = [
        f for f in sorted(gnma_nissues.glob("*.parquet")) if int(f.stem.split("_")[1]) < ps_first
    ]
    logger.info(
        "  GNMA: %d nimonSFPS PS files (from %d) + %d legacy nissues D files",
        len(gnma_files),
        ps_first,
        len(legacy_files),
    )
    for f in gnma_files:
        month = f.stem.split("_")[-2]  # nimonSFPS_YYYYMM_PS
        chunk = _assemble_gnma_mbs_issuance(pl.scan_parquet(str(f)), month=month).collect(
            engine="streaming"
        )
        total += _write_mbs_chunk(
            _reindex_to_schema(chunk, canon), out_dir, "GNMA", f"gnma_{month}"
        )
    _log_gnma_issuance_seam(gnma_nissues, gnma_ps, ps_first)
    legacy_platinum_pools: set[str] = set()
    for f in legacy_files:
        month = f.stem.split("_")[1]  # nissues_YYYYMM_D
        chunk = _assemble_gnma_legacy_mbs_issuance(
            pl.scan_parquet(str(f)),
            month=month,
            u_file=gnma_nissues_u / f"nissues_{month}_U.parquet",
        ).collect(engine="streaming")
        legacy_platinum_pools.update(
            chunk.filter(pl.col("is_resecuritization"))["pool_id"].to_list()
        )
        total += _write_mbs_chunk(
            _reindex_to_schema(chunk, canon), out_dir, "GNMA", f"gnma_nissues_{month}"
        )
    if plat_sample:
        plat = _build_gnma_platinum_mbs_issuance(gnma_plat, gnma_plat_ps, legacy_platinum_pools)
        logger.info(
            "  GNMA Platinum: %d securities from factorAplat (%d already in the legacy leg)",
            plat.height,
            len(legacy_platinum_pools),
        )
        total += _write_mbs_chunk(_reindex_to_schema(plat, canon), out_dir, "GNMA", "gnma_platinum")

    logger.info("mbs_issuance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


def _date_to_yyyymmdd(expr: pl.Expr) -> pl.Expr:
    """Convert a ``pl.Date`` column to an ``Int32`` ``YYYYMMDD`` (null-safe).

    ``dt.month``/``dt.day`` return ``Int8``; cast each component to ``Int32`` first
    so ``month * 100`` does not overflow the ``Int8`` range.

    Returns:
        An Int32 ``YYYYMMDD`` expression, null-safe.
    """
    return (
        expr.dt.year().cast(pl.Int32) * 10000
        + expr.dt.month().cast(pl.Int32) * 100
        + expr.dt.day().cast(pl.Int32)
    ).cast(pl.Int32, strict=False)


def _next_month_yyyymm(expr: pl.Expr) -> pl.Expr:
    """Advance an ``Int32`` ``YYYYMM`` month key by one month (null-safe)."""
    return (pl.when(expr % 100 == 12).then((expr // 100 + 1) * 100 + 1).otherwise(expr + 1)).cast(
        pl.Int32, strict=False
    )


#: GNMA factor-file leg -> source dataset, keyed on the shared lowercase ``program``
#: vocabulary (``factorA1`` is Ginnie I, ``factorA2`` Ginnie II).
_GNMA_PERF_PROGRAMS: dict[str, str] = {"ginnie_i": "factorA1", "ginnie_ii": "factorA2"}


def _assemble_gse_mbs_performance(scan: pl.LazyFrame, *, agency: str, month: str) -> pl.LazyFrame:
    """Map one GSE monthly factor file to the unified mbs_performance grain.

    Mapping + scaling only (no cross-month state), so it is unit-testable on one
    file. Serves BOTH GSE factor feeds — FHLMC ``FD`` and FNMA ``FNM_MF`` — which
    share the UMBS security-disclosure layout; the ``IS`` feeds are new-issuance-
    only and NOT the paydown panel (verified Phase-1). Both store the packed date
    encodings as ``Int64`` (``Issue Date`` = ``MMDDYYYY``; ``Maturity Date`` =
    ``M(M)YYYY``), so they are parsed here rather than read straight through. The
    buyout column is ``Involuntary Loan Removal`` (FHLMC + FNMA ≥ 2024-12) or its
    pre-rename FNMA name ``Delinquent Loans Purchased`` — both aliased below.

    ``program``, ``structure_class`` and ``is_resecuritization`` come from the shared
    ``_gse_program`` / ``_gse_structure_flags`` rules, so this grain and
    ``mbs_issuance`` classify the same security identically.

    Args:
        scan: A scan over one FD / FNM_MF monthly file.
        agency: Agency partition value (``FHLMC`` or ``FNMA``).
        month: The as-of ``YYYYMM`` (from the filename), used for ``as_of_date``.

    Returns:
        A LazyFrame at the canonical grain (pre-derivation columns only).
    """
    av = set(scan.collect_schema().names())
    corr = _pick(["Security Data Correction Indicator"], av, pl.String).cast(pl.String)
    issue_date = _date_to_yyyymmdd(mmddyyyy_to_date(_pick(["Issue Date"], av, pl.Int64)))
    structure_class, is_resec = _gse_structure_flags(av, agency=agency, issue_date_ymd=issue_date)
    return scan.select(
        pl.lit(agency).alias("agency"),
        pl.col("Security Identifier").cast(pl.String).alias("pool_id"),
        _pick(["CUSIP"], av, pl.String).cast(pl.String).alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        _gse_program(issue_date),
        _pick(["Prefix"], av, pl.String).cast(pl.String).alias("pool_type"),
        structure_class.alias("structure_class"),
        is_resec.alias("is_resecuritization"),
        issue_date.alias("issue_date"),
        _date_to_yyyymmdd(month_to_date(_pick(["Maturity Date"], av, pl.Int64))).alias(
            "maturity_date"
        ),
        _pick(["Issuance Investor Security UPB"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("orig_security_upb"),
        _pick(["Current Investor Security UPB"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("current_security_upb"),
        _pick(["Security Factor"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("rpb_factor"),
        _pick(["Loan Count"], av, pl.Int64).cast(pl.Int64, strict=False).alias("loan_count"),
        _pick(["WA Net Interest Rate"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("passthrough_rate"),
        _pick(
            [
                "WA Current Interest Rate",
                "WA Issuance Interest Rate",
                "WA Origination Interest Rate",
            ],
            av,
            pl.Float64,
        )
        .cast(pl.Float64, strict=False)
        .alias("wac"),
        _pick(["WA Loan Age"], av, pl.Int64).cast(pl.Int32, strict=False).alias("wala"),
        _pick(["WA Current Remaining Months to Maturity"], av, pl.Int64)
        .cast(pl.Int32, strict=False)
        .alias("warm"),
        _pick(["WA Loan Term"], av, pl.Int64).cast(pl.Int32, strict=False).alias("waolt"),
        _pick(
            ["Involuntary Loan Removal (Loan Count)", "Delinquent Loans Purchased (Loan Count)"],
            av,
            pl.Int64,
        )
        .cast(pl.Int64, strict=False)
        .alias("involuntary_removal_count"),
        _pick(
            [
                "Involuntary Loan Removal (Prior Month UPB)",
                "Delinquent Loans Purchased (Prior Month UPB)",
            ],
            av,
            pl.Float64,
        )
        .cast(pl.Float64, strict=False)
        .alias("involuntary_removal_upb"),
        _pick(["Security Status Indicator"], av, pl.String)
        .cast(pl.String)
        .alias("security_status_raw"),
        pl.when(corr == "Y")
        .then(pl.lit("correction"))
        .otherwise(pl.lit("original"))
        .alias("record_source"),
        pl.lit("FNM_MF" if agency == "FNMA" else "FD").alias("_source_dataset"),
    )


def _assemble_gnma_mbs_performance(scan: pl.LazyFrame, *, month: str, program: str) -> pl.LazyFrame:
    """Map one GNMA ``factorA{1,2}`` file to the unified mbs_performance grain.

    Mapping only. ``factorA`` canonical silver is already decoded (real USD/percent,
    ``rpb_factor`` a real fraction, packed dates parsed to ``pl.Date``), so no scaling
    is applied. Three groups are filtered out: header rows (blank CUSIP), HMBS pools
    (``Pool Type`` in the reverse-mortgage set), and Platinum securities, which these
    files publish only as identifier stubs with a null RPB (see
    ``GNMA_PLATINUM_ISSUER_NUMBER``). The WA block + ``loan_count`` are left-enriched
    from ``monthlySFPS`` PS in the builder (this assembler emits the factor-side
    only).

    Args:
        scan: A scan over one factorA1/factorA2 monthly file.
        month: The as-of ``YYYYMM`` (from the filename).
        program: ``ginnie_i`` (factorA1) or ``ginnie_ii`` (factorA2).

    Returns:
        A LazyFrame at the canonical grain (factor side; WA cols null pending enrichment).
    """
    av = set(scan.collect_schema().names())
    dataset = _GNMA_PERF_PROGRAMS[program]
    cusip = _pick(["CUSIP Number"], av, pl.String).cast(pl.String).str.strip_chars()
    pool_type = _pick(["Pool Type"], av, pl.String).cast(pl.String).str.strip_chars()
    # Platinum stubs. Equivalent predicates in all 165 months of both files:
    # issuer 9999 <=> pool number starting 78 <=> null RPB. The issuer number is used
    # because the null-RPB form also catches unrelated early-vintage rows. Written as
    # a negated equality so a vintage without the column keeps its rows rather than
    # silently dropping all of them.
    is_platinum = (
        _pick(["Issuer Number"], av, pl.String).cast(pl.String).str.strip_chars()
        == GNMA_PLATINUM_ISSUER_NUMBER
    ).fill_null(value=False)
    return scan.filter(
        (cusip != "") & (~pool_type.is_in(list(GNMA_HMBS_POOL_TYPES))) & ~is_platinum
    ).select(
        pl.lit("GNMA").alias("agency"),
        _pick(["Pool Number"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        cusip.alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        pl.lit(program).alias("program"),
        pool_type.alias("pool_type"),
        # Base pools only, after the filters above: the surviving pool-type code
        # space (see core.reference.gnma_pool_types) has no REMIC member, and the
        # issue-type field carries only X / C / M. Platinum stubs are dropped here
        # and belong to a factorAplat leg; REMICs live in the REMIC factor files.
        pl.lit("pass_through").alias("structure_class"),
        pl.lit(value=False).alias("is_resecuritization"),
        # Canonical silver decodes packed dates to pl.Date; render back to Int32 YYYYMMDD.
        _date_to_yyyymmdd(_pick(["Pool Issue Date (MMDDYY)"], av, pl.Date)).alias("issue_date"),
        _date_to_yyyymmdd(_pick(["Pool Maturity Date (MMDDYY)"], av, pl.Date)).alias(
            "maturity_date"
        ),
        _gnma_upb_dec(_pick(["Original Aggregate Amount"], av, pl.Float64)).alias(
            "orig_security_upb"
        ),
        _gnma_upb_dec(_pick(["Remaining Security RPB"], av, pl.Float64)).alias(
            "current_security_upb"
        ),
        _pick(["RPB Factor"], av, pl.Float64).cast(pl.Float64, strict=False).alias("rpb_factor"),
        _gnma_rate_dec(_pick(["Pool Interest Rate"], av, pl.Float64)).alias("passthrough_rate"),
        pl.lit("original").alias("record_source"),
        pl.lit(dataset).alias("_source_dataset"),
    )


def _gnma_wa_enrichment(ps_file: Path, month: str) -> pl.LazyFrame:
    """Scan one GNMA PS file to the WA-block enrichment (already decoded).

    Serves both PS headers, which share a layout: ``monthlySFPS`` PS for the base
    pass-through panel (keyed on ``Pool ID`` == factorA ``Pool Number``; coverage
    starts 2020-05) and ``platmonPPS`` PS for the Platinum panel (keyed on the
    Platinum ``Pool ID`` == ``factorAplat``'s; coverage starts 2021-01, and it lags
    ``factorAplat`` by a month at the tail). Both carry the WA rate/age/term block as
    plain decoded numbers, so no scaling is applied; months with no PS file get a null
    WA block (the caller left-joins) and the derivation falls to its documented proxy
    tier. ``platmonPPS`` has no loan count, which resolves to a typed null.

    Args:
        ps_file: Path to one ``monthlySFPS_YYYYMM_PS`` / ``platmonPPS_YYYYMM_PS`` file.
        month: The as-of ``YYYYMM`` (used as the join key ``as_of_date``).

    Returns:
        A LazyFrame keyed on ``(pool_id, as_of_date)`` with the WA columns.
    """
    scan = pl.scan_parquet(str(ps_file))
    av = set(scan.collect_schema().names())

    def num(names: list[str]) -> pl.Expr:
        return _pick(names, av, pl.String).cast(pl.Float64, strict=False)

    return scan.select(
        _pick(["Pool ID"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        num(["WA Interest Rate (WAC)"]).alias("wac"),
        num(["WA Loan Age (WALA)"]).cast(pl.Int32, strict=False).alias("wala"),
        num(["WA Remaining Months to Maturity (WARM)"]).cast(pl.Int32, strict=False).alias("warm"),
        num(["WA Original Loan Term (WAOLT)"]).cast(pl.Int32, strict=False).alias("waolt"),
        num(["Number of loans in pool"]).cast(pl.Int64, strict=False).alias("loan_count"),
    )


def _assemble_gnma_platinum_mbs_performance(scan: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one GNMA ``factorAplat`` file to the unified mbs_performance grain.

    ``factorAplat`` is the RPB panel of record for GNMA Platinum. The base factor
    files carry the same securities only as identifier-only stubs with a null RPB
    (see ``GNMA_PLATINUM_ISSUER_NUMBER``), so adding this leg repairs rows the panel
    already emitted rather than adding a new universe: the ``pool_id`` space is
    disjoint from the base one only AFTER that stub filter.

    Canonical silver is already decoded, so nothing is scaled. HMBS Platinum is
    excluded by ``Pool Indicator`` (``GNMA_PLATINUM_HMBS_INDICATOR``) — the same
    exclusion the base panel makes by pool type, expressed on the field that works in
    the Platinum code space. ``rpb_factor`` is left as published: it exceeds 1 on a
    handful of securities a month, a publisher artifact that clipping would hide.

    ``loan_count``, the involuntary-removal pair and ``security_status_raw`` have no
    Platinum equivalent and resolve to typed nulls; the WA block is left-enriched from
    ``platmonPPS`` PS in the builder.

    Args:
        scan: A scan over one ``factorAplat_YYYYMM_ALL`` file.
        month: The as-of ``YYYYMM`` (from the filename).

    Returns:
        A LazyFrame at the canonical grain (factor side; WA cols pending enrichment).
    """
    av = set(scan.collect_schema().names())
    cusip = _pick(["cusip_number"], av, pl.String).cast(pl.String).str.strip_chars()
    pind = _pick(["pool_indicator"], av, pl.String).cast(pl.String).str.strip_chars()
    return scan.filter((cusip != "") & (pind != GNMA_PLATINUM_HMBS_INDICATOR)).select(
        pl.lit("GNMA").alias("agency"),
        _pick(["pool_id"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        cusip.alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        pl.lit(_GNMA_PLATINUM_PROGRAM).alias("program"),
        _pick(["pool_type"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_type"),
        pl.lit("platinum").alias("structure_class"),
        pl.lit(value=True).alias("is_resecuritization"),
        _date_to_yyyymmdd(_pick(["pool_issue_date"], av, pl.Date)).alias("issue_date"),
        _date_to_yyyymmdd(_pick(["pool_maturity_date"], av, pl.Date)).alias("maturity_date"),
        _gnma_upb_dec(_pick(["original_aggregate_amount"], av, pl.Float64)).alias(
            "orig_security_upb"
        ),
        _gnma_upb_dec(_pick(["remaining_security_rpb"], av, pl.Float64)).alias(
            "current_security_upb"
        ),
        _pick(["rpb_factor"], av, pl.Float64).cast(pl.Float64, strict=False).alias("rpb_factor"),
        _gnma_rate_dec(_pick(["pool_interest_rate"], av, pl.Float64)).alias("passthrough_rate"),
        pl.lit("original").alias("record_source"),
        pl.lit("factorAplat").alias("_source_dataset"),
    )


def _assemble_gnmega_mbs_performance(scan: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one ``umbs/silver/FNMA/GN_MEGA`` file to the unified mbs_performance grain.

    Fannie's Ginnie-backed Megas: a single-class resecuritization of Ginnie pools, so
    the rows carry ``agency = FNMA`` while their collateral sits in the panel under
    ``agency = GNMA`` — a cross-agency balance sum double counts them unless
    ``is_resecuritization`` is filtered. Their identifiers do not collide with the
    base FNMA factor feed (zero CUSIP and zero Security-Identifier overlap), and the
    program is in terminal runoff: 219 securities and $16.5B of original face in
    2019-06 down to 41 and $642M by 2026-05, every one issued 1990-2005.

    The feed publishes no current-balance column, so ``current_security_upb`` is
    DERIVED as issuance UPB x ``Security Factor``. Note the double space in
    ``Issuance Investor  Security UPB``, which is the source spelling.

    Args:
        scan: A scan over one ``GN_MEGA_YYYYMM`` file.
        month: The as-of ``YYYYMM`` (from the filename).

    Returns:
        A LazyFrame at the canonical grain (pre-derivation columns only).
    """
    av = set(scan.collect_schema().names())
    orig = _pick(
        ["Issuance Investor  Security UPB", "Issuance Investor Security UPB"], av, pl.Float64
    ).cast(pl.Float64, strict=False)
    factor = _pick(["Security Factor"], av, pl.Float64).cast(pl.Float64, strict=False)
    return scan.select(
        pl.lit("FNMA").alias("agency"),
        pl.col("Security Identifier").cast(pl.String).str.strip_chars().alias("pool_id"),
        _pick(["CUSIP"], av, pl.String).cast(pl.String).str.strip_chars().alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        pl.lit("gn_mega").alias("program"),
        _pick(["Prefix"], av, pl.String).cast(pl.String).alias("pool_type"),
        pl.lit("mega").alias("structure_class"),
        pl.lit(value=True).alias("is_resecuritization"),
        _date_to_yyyymmdd(_pick(["Issue Date"], av, pl.Date)).alias("issue_date"),
        _date_to_yyyymmdd(_pick(["Maturity Date"], av, pl.Date)).alias("maturity_date"),
        orig.alias("orig_security_upb"),
        (orig * factor).alias("current_security_upb"),  # no current-UPB column in the feed
        factor.alias("rpb_factor"),
        _pick(["WA Net Interest Rate"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("passthrough_rate"),
        _pick(["WA Current Interest Rate"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("wac"),
        _pick(["WA Loan Age"], av, pl.Int64).cast(pl.Int32, strict=False).alias("wala"),
        _pick(["WA Current Remaining Months to Maturity"], av, pl.Int64)
        .cast(pl.Int32, strict=False)
        .alias("warm"),
        _pick(["_record_source"], av, pl.String).alias("record_source"),
        pl.lit("GN_MEGA").alias("_source_dataset"),
    )


def _apply_mbs_performance_derivations(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply the §4 windowed prepayment derivations (over ``[agency, pool_id]``).

    The input must already be sorted by ``[agency, pool_id, as_of_date]``. Uses the
    backward-looking (prior-month) convention the spec mandates: the first
    observed month per pool leaves prior-dependent fields null. ``smm`` is clipped
    to ``[0, 1]`` with non-finite nulled; ``unscheduled_principal`` keeps its raw
    (possibly negative) value. Stages are sequential ``with_columns`` because each
    field feeds the next (``scheduled`` -> ``unscheduled`` -> ``smm``).

    The amortization inputs fall back through a documented proxy tier recorded in
    ``amort_basis`` (see ``AMORT_BASIS_VALUES``), because base GNMA carries no WA
    block before ``monthlySFPS`` begins in 2020-05. Where neither a rate nor a
    remaining term can be had, the amortization is **null**, not zero — a zero
    schedule hands the entire scheduled paydown to prepayment.

    Args:
        lf: The assembled pool-month panel, pre-sorted by ``(agency, pool_id, as_of_date)``.

    Returns:
        The panel with the derived prepayment columns populated.
    """
    upb = pl.col("current_security_upb")
    # order_by makes the shift correct regardless of physical row order after a
    # streaming re-partition (matches the cpr_3m rolling_mean call below).
    upb_prev = upb.shift(1).over(["agency", "pool_id"], order_by="as_of_date")
    warm_wa_prev = (
        pl.col("warm").shift(1).over(["agency", "pool_id"], order_by="as_of_date").cast(pl.Float64)
    )
    # Proxy term: whole months from the as-of month (YYYYMM) to stated maturity
    # (YYYYMMDD), floored at 1 so a past-due maturity amortizes to a full-balance
    # schedule (which clips prepayment to 0) rather than going negative.
    months_to_maturity = (
        (pl.col("maturity_date") // 10000 * 12 + pl.col("maturity_date") // 100 % 100)
        - (pl.col("as_of_date") // 100 * 12 + pl.col("as_of_date") % 100)
    ).cast(pl.Float64)
    rate_amort = pl.coalesce(pl.col("wac"), pl.col("passthrough_rate"))
    warm_amort_prev = pl.coalesce(warm_wa_prev, months_to_maturity.clip(lower_bound=1.0))
    smm_raw = (pl.col("unscheduled_principal") / pl.col("_smm_denom")).clip(
        lower_bound=0.0, upper_bound=1.0
    )
    return (
        lf.with_columns(
            (upb_prev - upb).alias("total_principal_reduction"),
            # wa_block only when BOTH inputs came from the disclosed WA block; null
            # when the schedule is not computable at all (which includes each pool's
            # first observed month, where there is no prior term to amortize from).
            pl.when(rate_amort.is_null() | warm_amort_prev.is_null() | upb_prev.is_null())
            .then(pl.lit(None, dtype=pl.String))
            .when(pl.col("wac").is_not_null() & warm_wa_prev.is_not_null())
            .then(pl.lit("wa_block"))
            .otherwise(pl.lit("proxy"))
            .alias("amort_basis"),
            scheduled_principal(upb_prev, rate_amort, warm_amort_prev).alias("scheduled_principal"),
            upb_prev.alias("_upb_prev"),
        )
        .with_columns(
            (pl.col("total_principal_reduction") - pl.col("scheduled_principal")).alias(
                "unscheduled_principal"
            ),
            # SMM against the beginning scheduled balance; null when the denom <= 0.
            pl.when(pl.col("_upb_prev") - pl.col("scheduled_principal") > 0)
            .then(pl.col("_upb_prev") - pl.col("scheduled_principal"))
            .otherwise(None)
            .alias("_smm_denom"),
        )
        .with_columns(
            pl.when(smm_raw.is_finite()).then(smm_raw).otherwise(None).alias("smm"),
        )
        .drop("_upb_prev", "_smm_denom")
    )


def build_mbs_performance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the master MBS/pool performance panel (pool x month).

    The security-grain paydown-and-prepayment panel — the security analog of
    loan-level ``loan_performance``. One row per pool x as-of month, keyed on
    ``(agency, pool_id, as_of_date)``, hive-partitioned by ``agency`` and
    ``as_of_year`` under ``data/combined/mbs_performance``.

    Sources (Phase-1 verified — these correct the spec draft):

      * **FNMA** = ``umbs/bronze/FNMA/FNM_MF`` (the Monthly Factor feed — the direct
        analog of FHLMC ``FD``; ``FNM_IS`` is issuance-only and ``FNM_DPR_FCTR`` is a
        cohort aggregate at the wrong grain). WA block from ``FNM_MF`` itself; 2019-06+
        with ~500k pools/month. Pre-2024-12 files name the buyout column ``Delinquent
        Loans Purchased`` (harmonized to ``Involuntary Loan Removal`` from 2024-12).
      * **FHLMC** = ``umbs/bronze/FHLMC/FD`` (the pool factor feed; ``IS`` is
        new-issuance-only and is NOT the paydown panel). WA block from ``FD`` itself.
      * **GNMA** = ``gnma/silver/factorA1`` (Ginnie I) ∪ ``factorA2`` (Ginnie II),
        canonical decoded silver, HMBS filtered by ``Pool Type``, WA block
        left-enriched from ``gnma/silver/monthlySFPS`` PS; ``termination_date`` from
        ``ptermmon`` P.
      * **GNMA Platinum** = ``gnma/silver/factorAplat``, WA block from
        ``gnma/silver/platmonPPS`` PS. The base factor files carry these securities
        only as null-RPB stubs, which the GNMA leg drops, so this leg repairs rows
        rather than adding a universe. HMBS Platinum is excluded by ``Pool
        Indicator``. Terminations need no new source: 748 of the 775 Platinums that
        disappear already have a ``ptermmon`` record on the CUSIP join.
      * **FNMA Ginnie-backed Megas** = ``umbs/silver/FNMA/GN_MEGA`` (~9.5k
        pool-months, all issued 1990-2005, disjoint from ``FNM_MF``). Current UPB is
        derived, since the feed has no current-balance column.

    FNMA and FHLMC share the ``_assemble_gse_mbs_performance`` mapper (identical UMBS
    security-disclosure layout); only the filename-to-month parse differs.

    Because SMM/CPR need cross-month history, the per-file assemblers only map +
    scale; all monthly rows are spilled to a temp dir, then re-scanned to apply the
    §4 windowed derivation before the partitioned write.

    Termination is resolved from four signals in descending order of authority: the
    GSE ``Security Status Indicator``, GNMA's disclosed ``ptermmon`` date, the pool's
    own disappearance from a month-contiguous feed, and a residual zero-factor row
    whose pool still reports at the frontier. ``termination_source`` records which
    one fired, so a synthesized ``termination_date`` is never mistaken for a
    disclosed one.

    **This master is a union of overlapping security layers**, so summing
    ``current_security_upb`` over a month double counts. The GSE factor feeds mix base
    pools with the Giants/Megas/Supers/Mirrors that wrap them; the GNMA side adds
    Platinum, and the FNMA side the Ginnie-backed Megas, whose collateral is itself in
    the panel under ``agency = GNMA``. Every row carries ``is_resecuritization`` and
    ``structure_class``; filter before aggregating RPB, loan counts or pool counts.
    Platinum also nests — 8.2% of 2026-04 Platinum RPB is collateralized by other
    Platinum securities — so even the wrapper layer is not a clean total on its own.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    out_dir = CombinedConfig.dataset_path("mbs_performance")
    if not should_process_file(out_dir, overwrite=overwrite):
        logger.info("mbs_performance already built at %s; skipping (use overwrite).", out_dir)
        return pl.scan_parquet(str(out_dir))

    CombinedConfig.ensure_directories()
    recreate_dir(out_dir)

    canon = pl.Schema(CANONICAL_MBS_PERFORMANCE_SCHEMA)
    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs")
    gnma = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver"
    fnma_mf = umbs / "bronze" / "FNMA" / "FNM_MF"
    fhlmc_fd = umbs / "bronze" / "FHLMC" / "FD"
    gnma_ps = gnma / "monthlySFPS" / "PS"
    gnma_pterm = gnma / "ptermmon" / "P"
    gnma_programs = {p: gnma / d / "ALL" for p, d in _GNMA_PERF_PROGRAMS.items()}
    gnma_plat = gnma / "factorAplat" / "ALL"
    gnma_plat_ps = gnma / "platmonPPS" / "PS"
    gn_mega = umbs / "silver" / "FNMA" / "GN_MEGA"

    # Spill all mapped monthly rows to a temp dir (SMM needs cross-month history,
    # so we cannot derive per file). Reindex each chunk to the canonical union so
    # the temp tree scans as one uniform table.
    tmp_dir = out_dir.parent / "_mbs_performance_tmp"
    recreate_dir(tmp_dir)

    logger.info("Building combined mbs_performance (FNMA + FHLMC + GNMA) -> %s", out_dir)

    def _spill(chunk: pl.DataFrame, tag: str) -> int:
        if chunk.is_empty():
            return 0
        _reindex_to_schema(chunk, canon).write_parquet(tmp_dir / f"{tag}.parquet")
        return chunk.height

    total_in = 0

    # --- FNMA (FNM_MF carries its own WA block; no enrichment join) ---
    # Filename is FNM_MF_{YYYYMM}[_N].parquet; the optional _N suffix is a same-
    # month republication whose rows are corrections (Correction Indicator = Y).
    # Extract the 6-digit month (NOT split("_")[-1], which reads "_2" as month 2)
    # and spill it under the real month; the per-shard dedup below keeps the
    # correction over the original at the same (pool, month) grain.
    mf_files = sorted(fnma_mf.glob("FNM_MF_*.parquet"))
    logger.info("  FNMA: %d FNM_MF files", len(mf_files))
    for f in mf_files:
        m = re.search(r"FNM_MF_(\d{6})", f.stem)
        if m is None:
            logger.warning("  FNMA: skipping FNM_MF file with no YYYYMM in name: %s", f.name)
            continue
        month = m.group(1)
        chunk = _assemble_gse_mbs_performance(
            pl.scan_parquet(str(f)), agency="FNMA", month=month
        ).collect(engine="streaming")
        total_in += _spill(chunk, f"fnma_{f.stem.split('FNM_MF_')[-1]}")

    # --- FHLMC (FD carries its own WA block; no enrichment join) ---
    fd_files = sorted(fhlmc_fd.glob("fd*.parquet"))
    logger.info("  FHLMC: %d FD files", len(fd_files))
    for f in fd_files:
        # fd{YYMMDD} -> as-of YYYYMM (the factor month; YY is 20YY).
        stamp = f.stem[2:]  # YYMMDD
        month = f"20{stamp[:2]}{stamp[2:4]}"
        chunk = _assemble_gse_mbs_performance(
            pl.scan_parquet(str(f)), agency="FHLMC", month=month
        ).collect(engine="streaming")
        total_in += _spill(chunk, f"fhlmc_{month}")

    # --- GNMA (factorA + monthlySFPS WA enrichment; ptermmon joined globally below) ---
    for program, fdir in gnma_programs.items():
        files = sorted(fdir.glob("*.parquet"))
        logger.info("  GNMA %s: %d factor files", program, len(files))
        for f in files:
            month = f.stem.split("_")[-2]  # factorA{1,2}_YYYYMM_ALL
            factor = _assemble_gnma_mbs_performance(
                pl.scan_parquet(str(f)), month=month, program=program
            )
            ps_file = gnma_ps / f"monthlySFPS_{month}_PS.parquet"
            if ps_file.exists():
                enrich = _gnma_wa_enrichment(ps_file, month)
                factor = factor.join(enrich, on=["pool_id", "as_of_date"], how="left")
            chunk = factor.collect(engine="streaming")
            total_in += _spill(chunk, f"gnma_{program}_{month}")

    # --- GNMA Platinum (factorAplat + platmonPPS WA enrichment) ---
    # Repairs the rows the base leg drops: factorA1/factorA2 publish every Platinum
    # security as a null-RPB stub, so these are the same securities with a real
    # balance, factor and speed, flagged is_resecuritization.
    plat_files = sorted(gnma_plat.glob("*.parquet"))
    logger.info("  GNMA Platinum: %d factorAplat files", len(plat_files))
    for f in plat_files:
        month = f.stem.split("_")[-2]  # factorAplat_YYYYMM_ALL
        plat_lf = _assemble_gnma_platinum_mbs_performance(pl.scan_parquet(str(f)), month=month)
        pps_file = gnma_plat_ps / f"platmonPPS_{month}_PS.parquet"
        if pps_file.exists():
            plat_lf = plat_lf.join(
                _gnma_wa_enrichment(pps_file, month), on=["pool_id", "as_of_date"], how="left"
            )
        total_in += _spill(plat_lf.collect(engine="streaming"), f"gnma_platinum_{month}")

    # --- FNMA Ginnie-backed Megas (resecuritization; no collision with FNM_MF) ---
    mega_files = sorted(gn_mega.glob("GN_MEGA_*.parquet"))
    logger.info("  FNMA GN_MEGA: %d files", len(mega_files))
    for f in mega_files:
        month = f.stem.split("_")[-1]  # GN_MEGA_YYYYMM
        total_in += _spill(
            _assemble_gnmega_mbs_performance(pl.scan_parquet(str(f)), month=month).collect(
                engine="streaming"
            ),
            f"fnma_gnmega_{month}",
        )

    # ptermmon reports a pool once, in its termination month, keyed by CUSIP (a
    # numeric ``Pool ID`` in a DIFFERENT namespace from factorA's alphanumeric
    # ``Pool Number`` — verified zero-overlap, so join on CUSIP). A terminated
    # pool is absent from that month's factorA (already gone), so the date is
    # attached globally to every earlier row of the pool via CUSIP. GSE CUSIPs are
    # disjoint from the GNMA space (zero rows matched in the 2026-08 build), so the
    # left join leaves the GSE side null and _finalize treats a non-null value as
    # proof of GNMA disclosure.
    pterm_files = sorted(gnma_pterm.glob("ptermmon_*_P.parquet"))

    def _scan_ptermmon(pf: Path) -> pl.LazyFrame:
        s = pl.scan_parquet(str(pf))
        av = set(s.collect_schema().names())
        return s.select(
            _pick(["CUSIP Number"], av, pl.String).cast(pl.String).str.strip_chars().alias("cusip"),
            # Canonical silver decodes Termination Date to pl.Date; render to Int32 YYYYMMDD.
            _date_to_yyyymmdd(_pick(["Termination Date"], av, pl.Date)).alias("_ptermmon_date"),
        )

    gnma_terminations = (
        pl.concat([_scan_ptermmon(pf) for pf in pterm_files], how="diagonal_relaxed")
        .filter(pl.col("cusip") != "")
        .unique()
        .unique(subset=["cusip"], keep="none")
    )

    logger.info("  mapped %d pool-months; applying §4 derivations...", total_in)

    # Per-FEED frontier = the newest as-of month that feed published. Computed once
    # over the whole temp panel, BEFORE sharding, because it is a global fact while
    # everything else in _finalize is shard-local.
    #
    # Keyed on the feed rather than the agency because an agency's feeds publish on
    # their own calendars, and the disappearance rule reads "absent from every later
    # month" off this number: Ginnie's Platinum panel runs a month AHEAD of its base
    # factor files, and Fannie's Mega file a month BEHIND its factor file, so an
    # agency-level frontier would mark every pool in the lagging feed as having
    # vanished — 383,061 base Ginnie pools in one month.
    frontier_by_source: dict[str, int] = dict(
        pl.scan_parquet(str(tmp_dir))
        .group_by("_source_dataset")
        .agg(pl.col("as_of_date").max().alias("frontier"))
        .collect(engine="streaming")
        .iter_rows()
    )
    logger.info("  feed frontier months: %s", frontier_by_source)

    # --- Derivation + partitioned write, SHARDED to bound peak memory. ---
    # polars 1.42 has no partitioned sink_parquet and window (.over) expressions
    # do not stream, so a single collect of the full GNMA universe (~40M pool-
    # months) would OOM the machine. Shard by a hash of (agency, pool_id): each
    # pool's whole month history lands in one shard, so the per-pool windows stay
    # correct while peak memory is ~1/N of the panel. Each shard appends its own
    # file to the (agency, as_of_year) hive partitions.
    base = (
        pl.scan_parquet(str(tmp_dir))
        .drop("termination_date")
        .join(gnma_terminations, on="cusip", how="left")  # null for GSE / never-terminated
    )
    shard_key = pl.concat_str(["agency", "pool_id"], separator="|").hash()

    def _finalize(shard_lf: pl.LazyFrame) -> pl.LazyFrame:
        # Collapse to one row per (agency, pool_id, as_of_date) BEFORE the windowed
        # derivation (a duplicated month would corrupt the prior-month shift). Two
        # sources violate the grain: (1) FNMA republishes a few pool-months as
        # corrections in FNM_MF_{month}_N files (Correction Indicator = Y) — the
        # correction wins; (2) three legacy Ginnie pool numbers (002590/004041/005338,
        # 2012-2015) exist as DISTINCT securities in both Ginnie I and Ginnie II
        # (overlapping numeric ranges, different CUSIPs). This grain cannot hold both
        # and nothing says which one a (pool_id, month) means, so both are dropped.
        # Safe within a shard: every row of a given (agency, pool_id) hashes here,
        # so no duplicate escapes to another shard. A no-op for FHLMC.
        grain = ["agency", "pool_id", "as_of_date"]
        is_corr = (pl.col("record_source") == "correction").cast(pl.Int8)
        deduped = (
            shard_lf.filter(is_corr == is_corr.max().over(grain))
            .unique()
            .unique(subset=grain, keep="none")
        )
        return (
            _apply_mbs_performance_derivations(deduped.sort(["agency", "pool_id", "as_of_date"]))
            .with_columns(
                # cpr_1m from smm. cpr_3m COMPOUNDS three monthly survivals rather
                # than averaging three SMMs: summing log(1-smm) over the window gives
                # log of the 3-month survival, and raising that to the 4th power
                # annualizes it. This is the form Ginnie publishes — against its
                # disclosed CPRmon the compounded version puts 99.91% of material
                # pools within +-0.5 CPR, the rolling-mean version only 88.6%.
                smm_to_cpr(pl.col("smm")).alias("cpr_1m"),
                (1.0 - pl.col("smm"))
                .log()
                .rolling_sum(window_size=3, min_samples=3)
                .over(["agency", "pool_id"], order_by="as_of_date")
                .alias("_log_surv3"),
                # Both structure columns are pool constants, but the GSE feeds blank
                # the descriptive block on a paid-off row and the assembler leaves
                # those null. Fill within the pool so a resecuritization does not
                # read as pass_through in the month it retires.
                *[
                    pl.col(col)
                    .forward_fill()
                    .backward_fill()
                    .over(["agency", "pool_id"], order_by="as_of_date")
                    .alias(col)
                    for col in ("structure_class", "is_resecuritization")
                ],
            )
            .with_columns(
                # min_samples=3 makes cpr_3m null until the pool has three consecutive
                # months, and null wherever any smm in the window is (which covers the
                # amort_basis-null rows). Ginnie blanks its 3-month CPR the same way.
                (100.0 * (1.0 - (4.0 * pl.col("_log_surv3")).exp())).alias("cpr_3m"),
                # Shard-local by construction: every row of a given (agency, pool_id)
                # hashes into this shard, so the per-pool max is the global one.
                pl.col("as_of_date").max().over(["agency", "pool_id"]).alias("_pool_last"),
                # Pool-level: a pool sits in exactly one feed bar the three legacy
                # Ginnie pool numbers that exist in both factor files, and taking the
                # max keeps one frontier per pool so _disappeared cannot disagree
                # across its own rows.
                pl.col("_source_dataset")
                .replace_strict(frontier_by_source, default=None, return_dtype=pl.Int32)
                .max()
                .over(["agency", "pool_id"])
                .alias("_frontier"),
                pl.col("security_status_raw")
                .is_in(list(GSE_TERMINAL_STATUS_INDICATORS))
                .fill_null(value=False)
                .alias("_status_terminal"),
            )
            .with_columns(
                # A pool has disappeared when it is absent from every later month AND
                # its last month is strictly before the agency frontier; at the
                # frontier itself absence is unobservable, so those pools stay ACTIVE.
                # Every feed is month-contiguous per pool (0 of 703,382 FNMA and 0 of
                # 464,745 FHLMC pools have an interior gap; 41 of 578,315 GNMA pools
                # do), so "last month < frontier" IS "absent from every later month"
                # — provided the frontier is the pool's OWN feed's newest month.
                (pl.col("_pool_last") < pl.col("_frontier")).alias("_disappeared"),
                # Synthesized termination date = the first absent month as YYYYMM01.
                # Stated against the disclosed convention: GNMA's ptermmon date falls
                # in the month AFTER the pool's last factor month for 113,633 of
                # 113,644 pools (+2 months for the other 11), on business day 23-27.
                # The synthesis therefore agrees with ptermmon on the month and
                # differs only in using 01 as a placeholder day; termination_source
                # is what tells the two apart.
                (_next_month_yyyymm(pl.col("_pool_last")) * 100 + 1)
                .cast(pl.Int32)
                .alias("_synth_termination_date"),
            )
            .with_columns(
                # TERMINATED when the security is paid off (GSE status P, or a zero
                # factor / zero UPB row), deleted (GSE status D — factor and UPB go
                # null instead of 0), or on the last row of a pool that then leaves
                # the feed. Otherwise ACTIVE.
                pl.when(
                    pl.col("_status_terminal")
                    | (pl.col("rpb_factor") == 0)
                    | (pl.col("current_security_upb") == 0)
                    | (pl.col("_disappeared") & (pl.col("as_of_date") == pl.col("_pool_last")))
                )
                .then(pl.lit("TERMINATED"))
                .otherwise(pl.lit("ACTIVE"))
                .alias("pool_status"),
                # Pool-level (identical on every row of the pool, like the ptermmon
                # date it generalizes): the disclosed GNMA date where there is one,
                # else the synthesized first-absent month. Null while the pool is
                # still reporting at the frontier — including a GSE pool that pays
                # off IN the frontier month, whose first absent month is not yet
                # observable even though its status already says P.
                pl.coalesce(
                    pl.col("_ptermmon_date"),
                    pl.when(pl.col("_disappeared")).then(pl.col("_synth_termination_date")),
                ).alias("termination_date"),
                (pl.col("as_of_date") // 100).cast(pl.Int32).alias("as_of_year"),
            )
            .with_columns(
                # What established the termination — and so where termination_date
                # came from, keeping a synthesized month distinguishable from a
                # disclosed one. Disclosure wins over inference; the zero_factor
                # residual is a row already at zero whose pool has not yet left the
                # feed, so no absent month is observable.
                pl.when(pl.col("pool_status") != "TERMINATED")
                .then(pl.lit(None, dtype=pl.String))
                .when(pl.col("_status_terminal"))
                .then(pl.lit("status_indicator"))
                .when(pl.col("_ptermmon_date").is_not_null())
                .then(pl.lit("ptermmon"))
                .when(pl.col("_disappeared"))
                .then(pl.lit("disappearance"))
                .otherwise(pl.lit("zero_factor"))
                .alias("termination_source"),
            )
            .select(list(canon.keys()))
        )

    logger.info("  applying §4 derivations across %d shards -> %s", _MBS_PERF_SHARDS, out_dir)
    total = 0
    for shard in range(_MBS_PERF_SHARDS):
        frame = _finalize(base.filter(shard_key % _MBS_PERF_SHARDS == shard)).collect(
            engine="streaming"
        )
        for key, part in frame.partition_by("agency", "as_of_year", as_dict=True).items():
            agency, year = key
            part_dir = out_dir / f"agency={agency}" / f"as_of_year={year}"
            part_dir.mkdir(parents=True, exist_ok=True)
            part.drop("agency", "as_of_year").write_parquet(
                part_dir / f"{agency.lower()}_{year}_s{shard}.parquet"
            )
            total += part.height

    shutil.rmtree(tmp_dir)
    logger.info("mbs_performance built: %d pool-months (FNMA + FHLMC + GNMA) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


#: Builder dispatch for the datasets this package actually builds.
BUILDERS = {
    "loan_issuance": build_loan_issuance,
    "loan_performance": build_loan_performance,
    "loan_terminations": build_loan_terminations,
    "loan_outcomes": build_loan_outcomes,
    "mbs_issuance": build_mbs_issuance,
    "mbs_performance": build_mbs_performance,
}


def build_datasets(datasets: list[str] | None = None, *, overwrite: bool = False) -> list[str]:
    """Build the requested combined master datasets.

    Args:
        datasets: Subset of buildable masters. ``None`` = all buildable.
        overwrite: Rebuild existing outputs.

    Returns:
        List of dataset identifiers that were (attempted to be) built.

    Raises:
        ValueError: If an unknown or non-buildable dataset is requested.
        NotImplementedError: Builders are scaffolds.
    """
    CombinedConfig.ensure_directories()
    selected = list(BUILDERS) if datasets is None else datasets
    unknown = [d for d in selected if d not in BUILDERS]
    if unknown:
        raise ValueError(
            f"Unknown/non-buildable combined datasets: {unknown}; buildable: {list(BUILDERS)}"
        )
    built: list[str] = []
    for dataset in selected:
        logger.info("Building combined master: %s", dataset)
        BUILDERS[dataset](overwrite=overwrite)
        built.append(dataset)
    return built
