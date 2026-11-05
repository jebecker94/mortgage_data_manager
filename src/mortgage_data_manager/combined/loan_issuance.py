"""The ``loan_issuance`` master: one row per loan at issuance, tri-agency."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined._common import (
    _ARM_CAP_SENTINEL_FLOOR,
    _COUNT_SENTINEL,
    _GOV_MIP_RATE_CEILING,
    _LOAN_AGE_FLOOR,
    _LTV_SENTINEL_FLOOR,
    _MI_PCT_SENTINEL,
    _RATIO_SENTINELS,
    Readers,
    _build_dataset,
    _coalesce_present,
    _g_raw,
    _gnma_loan_months,
    _gnma_loan_readers,
    _gnma_program,
    _gse_crosswalk,
    _opt_str,
    _plan_exprs,
    _union_schema,
    _write_partitioned,
)
from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_issuance_schema import (
    DATE_FIELDS,
    GNMA_LOAN_ISSUANCE_PLAN,
    IL_FIRST_FIELDS,
    FieldSpec,
)
from mortgage_data_manager.combined.loan_performance_schema import GSE_NA_CODES
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.dates import yyyymmdd_to_date
from mortgage_data_manager.core.harmonized import (
    GNMA_TPO_CHANNEL,
    GSE_CHANNEL,
    GSE_LOAN_PURPOSE,
    credit_score,
    decode,
    flag_to_bool,
    gnma_loan_purpose,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.gnma.load import scan_new_issuance

logger = get_logger(__name__)


# UMBS-only issuance pass: hash shards so the per-shard dedup never holds the whole
# unmatched-loan universe (~6-8M loans x ~120 ILLD cols) in memory at once.
_UMBS_ONLY_SHARDS = 8


def _scrub_issuance_sentinels(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Null the issuance sentinels and impossible values that survive the silver layers.

    One rule set for every agency path. CLTV between 300 and 900 is kept: those rows
    look like real relief refinances, not codes.

    Returns:
        The frame with each present rule column scrubbed.
    """
    c = pl.col
    junk = {
        "ltv": c("ltv").is_in(_RATIO_SENTINELS) | (c("ltv") >= _LTV_SENTINEL_FLOOR),
        "cltv": c("cltv").is_in(_RATIO_SENTINELS) | (c("cltv") >= _LTV_SENTINEL_FLOOR),
        "dti": c("dti").is_in(_RATIO_SENTINELS),
        "mi_percent": c("mi_percent") == _MI_PCT_SENTINEL,
        "number_of_borrowers": c("number_of_borrowers") == _COUNT_SENTINEL,
        "number_of_units": c("number_of_units") == _COUNT_SENTINEL,
        "arm_init_cap_down": c("arm_init_cap_down") > _ARM_CAP_SENTINEL_FLOOR,
        "loan_age": c("loan_age") < _LOAN_AGE_FLOOR,
        "gov_annual_mip_rate": c("gov_annual_mip_rate") > _GOV_MIP_RATE_CEILING,
        "gov_upfront_mip_rate": c("gov_upfront_mip_rate") > _GOV_MIP_RATE_CEILING,
    }
    cols = set(lf.collect_schema().names())
    return lf.with_columns(
        pl.when(is_junk).then(None).otherwise(c(name)).alias(name)
        for name, is_junk in junk.items()
        if name in cols
    )


#: Multi-source yes/no indicators normalized to Boolean via ``flag_to_bool`` — every column
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
#: derived downstream anyway (``decode`` with ``GSE_LOAN_PURPOSE`` / ``GSE_CHANNEL``), so
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


def _illd_fixed_period_months(expr: pl.Expr) -> pl.Expr:
    """ILLD's initial fixed-rate period, published in years (``05``), as months like SF's."""
    return expr.cast(pl.Int16, strict=False) * 12


# --- shared categorical harmonization ---------------------------------------
# loan_purpose and channel are the two cross-agency categoricals GNMA actually
# populates, so they are unified to a self-documenting enum in BOTH the GSE and
# GNMA paths. The raw agency code is always retained alongside in ``{field}_raw``.
# GSE-only categoricals (occupancy, property_type, ...) are left in their raw
# encoding -- GNMA is null there, so re-coding them would add churn without
# cross-agency value.
def _null_na_codes(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Null the not-available codes (`GSE_NA_CODES`) in the GSE letter categoricals.

    ILLD publishes ``9`` in purpose, channel and occupancy where the SF release has no
    code, and an ILLD fill or a UMBS-only row carries it through. Enum-safe: the test
    casts through String and the kept value keeps its dtype.

    Returns:
        The frame with those codes null.
    """
    present = set(lf.collect_schema().names())
    return lf.with_columns(
        pl.when(pl.col(c).cast(pl.String, strict=False).is_in(list(GSE_NA_CODES)))
        .then(None)
        .otherwise(pl.col(c))
        .alias(c)
        for c in ("loan_purpose", "channel", "occupancy")
        if c in present
    )


def _quarter_from_date(expr: pl.Expr) -> pl.Expr:
    """Format a date as ``YYYYQn``."""
    return (expr.dt.year().cast(pl.String) + pl.lit("Q") + expr.dt.quarter().cast(pl.String)).cast(
        pl.String
    )


def _fhlmc_quarter_from_key(expr: pl.Expr) -> pl.Expr:
    """Decode Freddie's origination quarter out of the loan sequence number.

    Freddie encodes it in the key itself -- ``{F|A}{YY}Q{n}{seq}`` (``F`` fixed, ``A``
    ARM), so ``F21Q11275813`` is a 2021 Q1 origination and ``F99Q1...`` a 1999 one.
    This is the only origination-timing signal Freddie's SF disclosure carries (it
    publishes no origination date, unlike Fannie), and it is exact: the encoded quarter
    matches the source file's own origination quarter on 100.00% of the 13,352,164
    loans originated 2019-2024. The usual ``first_payment_date`` minus two months
    convention lands in the right quarter only 98.6% of the time -- seasoned and
    late-delivered loans miss -- so the key wins wherever both exist.

    Returns:
        An expression yielding the origination quarter decoded from the key.
    """
    yy = expr.str.slice(1, 2)
    century = (
        pl.when(yy.cast(pl.Int8, strict=False) >= 90).then(pl.lit("19")).otherwise(pl.lit("20"))
    )
    return (
        pl.when(expr.str.contains(r"^[FA]\d{2}Q[1-4]"))
        .then(century + yy + pl.lit("Q") + expr.str.slice(4, 1))
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
    drives all dispositions.
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
        loan_key_kind: Namespace label for the unified ``loan_key``.

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
            expr = flag_to_bool(expr)
        elif name in _STRINGIFY_FIELDS:  # per-agency dtypes with no polars supertype
            expr = expr.cast(pl.String, strict=False)
        elif name == "arm_initial_fixed_period":  # SF publishes months
            expr = expr.cast(pl.Int16, strict=False)
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
            expr = flag_to_bool(expr)
        elif name in _STRINGIFY_FIELDS:  # per-agency dtypes with no polars supertype
            expr = expr.cast(pl.String, strict=False)
        elif name == "arm_initial_fixed_period":
            expr = _illd_fixed_period_months(expr)
        elif name == "io_first_pi_date" and il_dtype != pl.Date:
            expr = _illd_month_int_to_date(expr)  # ILLD leaves this as a raw M(M)YYYY int
        elif name == "next_rate_adjustment_date" and il_dtype != pl.Date:
            continue  # ILLD int format unverified (not M(M)YYYY / YYDDD) -> take only Date
        if disp == "umbs_only":
            il_exprs.append(expr.alias(name))
        elif name == "representative_credit_score":
            il_exprs.append(credit_score(expr).alias("representative_credit_score__il"))
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
            if has_sf and has_il:  # ILLD-first: explicit 0 = no MI; SF null != 0.
                resolve.append(pl.coalesce([il_fill, pl.col(name)]).alias("mi_percent"))
            elif has_il:
                resolve.append(pl.col(helper).cast(pl.Float64, strict=False).alias("mi_percent"))
    # The published letter is kept (Freddie's N stays N; GSE_LOAN_PURPOSE decodes it as
    # rate-term); 'M' -> separate flag.
    purpose_parts = [pl.col(c) for c in ("loan_purpose", "loan_purpose__il") if c in cols]
    if purpose_parts:
        resolve.append(pl.coalesce(purpose_parts).cast(pl.String).alias("loan_purpose"))
        # An SF row is an origination, never a reissue: no ILLD match means False.
        modified = (
            (pl.col("loan_purpose__il") == "M").fill_null(value=False)
            if "loan_purpose__il" in cols
            else pl.lit(value=False)
        )
        resolve.append(modified.alias("is_modified_reissue"))
    # Representative score. FNMA = min(SF borrower, co-borrower); FHLMC = single SF
    # score. ILLD's representative backfills unmatched loans either way.
    rep_il = (
        pl.col("representative_credit_score__il")
        if "representative_credit_score__il" in cols
        else pl.lit(None)
    )
    if {"borrower_credit_score", "co_borrower_credit_score"} <= cols:
        rep_base = pl.min_horizontal(
            credit_score(pl.col("borrower_credit_score")),
            credit_score(pl.col("co_borrower_credit_score")),
        )
    elif "representative_credit_score" in cols:
        rep_base = credit_score(pl.col("representative_credit_score"))
    else:
        rep_base = pl.lit(None)
    resolve.append(pl.coalesce([rep_base, rep_il]).alias("representative_credit_score"))
    combined = combined.with_columns(resolve)

    # Second pass: sentinel scrubs (kept separate so a scrubbed column is not also
    # produced by the shared-keep coalesce in the same with_columns).
    scrub: list[pl.Expr] = [
        credit_score(pl.col(c)).alias(c)
        for c in ("borrower_credit_score", "co_borrower_credit_score")
        if c in cols
    ]
    # (the '7'/'9' not-available codes: flag_to_bool nulls them for the Boolean
    # indicators, and _null_na_codes for the categoricals an ILLD fill can carry them in.)
    if scrub:
        combined = combined.with_columns(scrub)
    combined = _null_na_codes(_scrub_issuance_sentinels(combined))

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
        # its namespace. loan_key_umbs stays as the pool-spine cross-ref.
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
    if "loan_purpose" in post_cols:  # retain raw letter, expose unified enum
        prov.append(pl.col("loan_purpose").alias("loan_purpose_raw"))
        prov.append(decode(pl.col("loan_purpose"), GSE_LOAN_PURPOSE).alias("loan_purpose"))
    if "channel" in post_cols:  # retain raw letter, expose unified enum
        prov.append(pl.col("channel").alias("channel_raw"))
        prov.append(decode(pl.col("channel"), GSE_CHANNEL).alias("channel"))
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
            expr = flag_to_bool(expr)
        elif name in _STRINGIFY_FIELDS:
            expr = expr.cast(pl.String, strict=False)
        elif name == "representative_credit_score":
            expr = credit_score(expr)
        elif name == "mi_percent":
            expr = expr.cast(pl.Float64, strict=False)
        elif name in DATE_FIELDS:  # defensive vs ILLD String/Date dtype drift
            expr = expr.cast(pl.Date, strict=False)
        elif name == "arm_initial_fixed_period":
            expr = _illd_fixed_period_months(expr)
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

    # Same scrubs as the SF path, before the prov pass so the *_raw columns are born clean.
    out = _null_na_codes(_scrub_issuance_sentinels(out))

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
    if "loan_purpose" in cols:  # raw letter + enum + M flag, mirroring the SF path
        purpose_str = pl.col("loan_purpose").cast(pl.String, strict=False)  # Enum-safe
        prov.append(purpose_str.alias("loan_purpose_raw"))
        prov.append(decode(purpose_str, GSE_LOAN_PURPOSE).alias("loan_purpose"))
        prov.append(
            (pl.col("loan_purpose") == "M").fill_null(value=False).alias("is_modified_reissue")
        )
    if "channel" in cols:
        prov.append(pl.col("channel").alias("channel_raw"))
        prov.append(decode(pl.col("channel"), GSE_CHANNEL).alias("channel"))
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
    loan_key_kind: str  # namespace of the unified loan_key
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
    snapshot_file = MBSUMBSMatchingConfig.get_umbs_snapshot_file(
        spec.snapshot_gse
    )  # pre-2019-06 loans
    if snapshot_file is not None:
        frames.append(_select_one(str(snapshot_file), "snapshot", _UMBS_RANK_SNAPSHOT))
    else:
        logger.warning(
            "No %s snapshot found; pre-2019-06 UMBS coverage will be missing.", spec.agency
        )
    pl.concat(frames, how="diagonal_relaxed").sink_parquet(dest)


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
        _gse_crosswalk(spec.crosswalk_file),
        agency=spec.agency,
        source_dataset=spec.source_dataset,
        sf_key_col=spec.sf_key_col,
        loan_key_kind=spec.loan_key_kind,
    ).collect_schema()


#: Canonical fields a matcher compares against ``loan_issuance`` (the direct HMDA route
#: and the private rate-lock reader). A loan whose best-ranked UMBS disclosures disagree
#: on one of these is excluded; a disagreement anywhere else only nulls that field.
_MATCHER_COMPARED_FIELDS: frozenset[str] = frozenset(
    {
        "seller_name",
        "original_upb",
        "note_rate",
        "loan_term",
        "first_payment_date",
        "ltv",
        "cltv",
        "dti",
        "representative_credit_score",
        "number_of_units",
        "loan_purpose",
        "occupancy",
        "channel",
        "property_state",
    }
)


def _matcher_compared_il_cols(plan: list[FieldSpec]) -> list[str]:
    """The ILLD source columns behind `_MATCHER_COMPARED_FIELDS` in ``plan``."""
    return sorted({c for fs in plan if fs["name"] in _MATCHER_COMPARED_FIELDS for c in fs["il"]})


def _one_umbs_row_per_loan(lf: pl.LazyFrame, compared_cols: list[str]) -> pl.LazyFrame:
    """One UMBS row per loan: its best-ranked disclosure, with disputed fields nulled.

    ``_umbs_priority`` ranks an original ILLD disclosure above the first monthly snapshot
    above a purpose-M modification reissue of the same loan. Byte-identical rows at a
    loan's best rank collapse to one. FNMA discloses some loans in two securities, so
    their best-ranked rows still differ, mostly on CUSIP and issuance UPB. Which value
    is right is unknowable, so no row is picked: every field the rows disagree on is
    null, and a loan that disagrees on a ``compared_cols`` field is excluded, because a
    matcher would compare it.

    Args:
        lf: Materialized UMBS rows (``loan_key_umbs``, ``_umbs_priority``, ILLD columns).
        compared_cols: ILLD columns whose disagreement excludes the loan.

    Returns:
        At most one row per ``loan_key_umbs``.
    """
    best = pl.col("_umbs_priority") == pl.col("_umbs_priority").min().over("loan_key_umbs")
    rows = lf.filter(best).unique()
    copies = pl.len().over("loan_key_umbs")
    names = rows.collect_schema().names()
    fields = [c for c in names if c != "loan_key_umbs"]
    disputed = [
        # tie-safe: n_unique() == 1 means every copy holds this value (nulls count).
        pl.when(pl.col(c).n_unique() == 1).then(pl.col(c).first()).alias(c)
        for c in fields
    ]
    compared_dispute = pl.any_horizontal(
        pl.lit(value=False), *[pl.col(c).n_unique() > 1 for c in compared_cols if c in names]
    )
    merged = (
        rows.filter(copies > 1)
        .group_by("loan_key_umbs")
        .agg(*disputed, compared_dispute.alias("_compared_dispute"))
        .filter(~pl.col("_compared_dispute"))
        .select(names)
    )
    return pl.concat([rows.filter(copies == 1), merged])


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
    crosswalk = _gse_crosswalk(spec.crosswalk_file)
    compared = _matcher_compared_il_cols(spec.plan)
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
            ),
            compared,
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
        n = _write_partitioned(chunk, canon, out_dir, "issuance_year", spec.agency, tag)
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
            .filter(pl.col("loan_key_umbs").hash(seed=0) % _UMBS_ONLY_SHARDS == i),
            compared,
        )
        chunk = _assemble_umbs_only_loan_issuance(
            spec.plan,
            shard,
            agency=spec.agency,
            source_dataset=f"{spec.agency.lower()}_umbs_only",
        ).collect(engine="streaming")
        n = _write_partitioned(
            chunk, canon, out_dir, "issuance_year", spec.agency, f"umbsonly{i:02d}"
        )
        umbs_only_total += n
        total += n
    logger.info("  %s: %d umbs-only (umbs_seq) issuance rows", spec.agency, umbs_only_total)
    return total


# ===================== GNMA loan_issuance (single new-issuance spine) ========
# GNMA is NOT a two-spine fusion: the new-issuance feed carries one loan-level row per
# new issuance (the loan as it appeared at pool issuance), so there is no SF/UMBS
# crosswalk join. Read through `sources.gnma.load.scan_new_issuance` (dailyllmni before
# 2026-08, dailyllmnipd from it), one issuance month at a time; pool-level CUSIP / Issue
# Type / pool_type come with each loan row. GNMA silver is canonical and already
# decoded, so nothing is rescaled. Choices: Ginnie I and II both flow through the same
# feed, split by Issue Type into ``program``; purpose comes from the (Loan Purpose,
# Refinance Type) pair (`gnma_loan_purpose`); government MIP stays separate from the GSE
# private ``mi_percent``; TPO 3 maps to retail; a loan is an ARM iff its index is
# non-blank or its margin is positive; LTV/CLTV/DTI of 0 or blank are null.


def _gnma_ratio_dec(expr: pl.Expr | None) -> pl.Expr:
    """An LTV/CLTV/DTI from CANONICAL silver as int percent; 0/blank -> null (no scaling)."""
    if expr is None:
        return pl.lit(None, dtype=pl.Int64)
    v = expr.cast(pl.Float64, strict=False)
    return pl.when(v > 0).then(v.round(0).cast(pl.Int64)).otherwise(None)


def _gnma_date(expr: pl.Expr | None) -> pl.Expr:
    """Parse a GNMA CCYYMMDD date string to ``pl.Date``; blank/sentinel/absent -> null."""
    if expr is None:
        return pl.lit(None, dtype=pl.Date)
    return yyyymmdd_to_date(expr)


def _gnma_gov_insurer(expr: pl.Expr | None) -> pl.Expr:
    """Map GNMA Agency loan type to the government_insurer enum."""
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


def _gnma_arm_index(expr: pl.Expr | None) -> pl.Expr:
    """Normalize GNMA ``Index Type`` to the controlled ARM-index vocab; blank or null -> null."""
    if expr is None:
        return pl.lit(None, dtype=pl.String)
    e = expr.str.to_uppercase()
    return (
        pl.when(e.is_null() | (e.str.len_chars() == 0))
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


def _gnma_issuance_readers(schema: pl.Schema) -> Readers:
    """``_gnma_loan_readers`` plus the issuance decodes `GNMA_LOAN_ISSUANCE_PLAN` names."""

    def g(src: tuple[str, ...]) -> pl.Expr | None:
        return _g_raw(src[0], schema)

    def channel(src: tuple[str, ...]) -> pl.Expr:
        tpo = g(src)
        return decode(tpo, GNMA_TPO_CHANNEL) if tpo is not None else pl.lit(None, dtype=pl.String)

    return {
        **_gnma_loan_readers(schema),
        "date": lambda src: _gnma_date(g(src)),
        "ratio": lambda src: _gnma_ratio_dec(g(src)),
        "insurer": lambda src: _gnma_gov_insurer(g(src)),
        "arm_index": lambda src: _gnma_arm_index(g(src)),
        "channel": channel,
    }


def _assemble_gnma_loan_issuance(month: str) -> pl.LazyFrame:
    """Assemble one GNMA issuance month from the new-issuance feed.

    Args:
        month: The issuance month ``YYYYMM`` (every row of a month's file is a new
            issuance of that month).

    Returns:
        A LazyFrame of unified loan-issuance rows for ``month`` (pre-reindex).
    """
    lf = scan_new_issuance(min_month=month, max_month=month)
    schema = lf.collect_schema()

    def g(name: str) -> pl.Expr | None:
        return _g_raw(name, schema)

    read = _gnma_issuance_readers(schema)
    margin = g("loan_gross_margin")
    margin_pos = (
        (margin.cast(pl.Float64, strict=False) > 0).fill_null(value=False)
        if margin is not None
        else pl.lit(value=False)
    )
    purpose = _opt_str(g("loan_purpose"))
    derived = {
        "issuance_month": pl.lit(int(month), dtype=pl.Int64),
        "issuance_year": pl.lit(int(month[:4]), dtype=pl.Int64),
        "orig_quarter": _quarter_from_date(_gnma_date(g("loan_origination_date"))),
        "loan_purpose": gnma_loan_purpose(purpose, g("refinance_type")),
        "is_modified_reissue": purpose.is_in(["3", "4", "5"]).fill_null(value=False),
        "amortization_type": pl.when(_opt_str(g("index_type")).is_not_null() | margin_pos)
        .then(pl.lit("ARM"))
        .otherwise(pl.lit("FRM")),
    }
    out = lf.select(
        *_plan_exprs(GNMA_LOAN_ISSUANCE_PLAN, read, derived),
        read["raw"](("cusip_number",)).alias("cusip"),
        _gnma_program(pl.col("issue_type")).alias("program"),
        read["raw"](("pool_type",)).alias("pool_type"),
    )
    return _scrub_issuance_sentinels(out)


def _gnma_canon_schema() -> pl.Schema:
    """Resolve GNMA's output schema (schema-only; the column set is constant)."""
    return _assemble_gnma_loan_issuance(_gnma_loan_months("new_issuance")[-1]).collect_schema()


def _build_gnma_loan_issuance(out_dir: Path, canon: pl.Schema) -> int:
    """Build GNMA's loan-issuance rows, one month file at a time, into ``out_dir``.

    Args:
        out_dir: Dataset root.
        canon: Canonical (tri-agency union) output schema.

    Returns:
        Rows written for GNMA.
    """
    months = _gnma_loan_months("new_issuance")
    logger.info("  GNMA: %d new-issuance months", len(months))
    total = 0
    for month in months:
        chunk = _assemble_gnma_loan_issuance(month).collect(engine="streaming")
        n = _write_partitioned(chunk, canon, out_dir, "issuance_year", "GNMA", f"gnma_{month}")
        total += n
        logger.debug("    GNMA/%s: %d rows", month, n)
    return total


def build_loan_issuance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the tri-agency master loan-level issuance dataset (GNMA+FNMA+FHLMC).

    For each GSE, fuses its two non-ID-joinable loan-level spines — the SF
    credit-research / origination spine (base) and the ILLD UMBS pool-disclosure
    spine (enrichment, ∪ the first monthly snapshot) — bridged by the strictly-1:1
    ``crosswalk/mbs_umbs/mbs_umbs_crosswalk_{gse}.parquet`` (field plans in the schema
    module). GNMA is a SINGLE
    new-issuance spine (no crosswalk; one row per new issuance; ``dailyllmni`` before
    2026-08, ``dailyllmnipd`` from it), harmonized to the same unified columns with
    pool-level CUSIP/program/pool_type. Output is hive-partitioned by ``agency`` and ``issuance_year``
    under ``data/combined/loan_issuance``.

    Processing is CHUNKED by source file: each GSE chunk narrows the crosswalk and
    the UMBS side to the same loans before joining (a single-shot join OOMs on a
    small machine); GNMA is chunked by issuance month. All three agencies are
    reindexed to one canonical schema so the dataset scans uniformly.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    return _build_dataset("loan_issuance", _write_loan_issuance, overwrite=overwrite)


def _write_loan_issuance(out_dir: Path) -> None:
    """Write the ``loan_issuance`` tree into ``out_dir`` (see `build_loan_issuance`)."""
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
    canon = _union_schema(per_agency)

    logger.info("Building combined loan_issuance (GNMA+FNMA+FHLMC) -> %s", out_dir)
    total = 0
    for spec in specs:
        total += _build_gse_loan_issuance(spec, out_dir, canon, temps[spec.agency])
        temps[spec.agency].unlink(missing_ok=True)
    total += _build_gnma_loan_issuance(out_dir, canon)

    logger.info("loan_issuance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
