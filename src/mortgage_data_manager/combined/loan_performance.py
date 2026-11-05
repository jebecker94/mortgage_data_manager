"""The ``loan_performance`` (loan x month) and ``loan_terminations`` (loan x event) masters."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined._common import (
    _LOAN_AGE_FLOOR,
    _LTV_SENTINEL_FLOOR,
    _MI_PCT_SENTINEL,
    _RATE_SENTINEL,
    _RMM_HI,
    _RMM_LO,
    Readers,
    _build_dataset,
    _coalesce_present,
    _count_rows,
    _flag_str,
    _g_raw,
    _gnma_loan_months,
    _gnma_loan_readers,
    _gse_crosswalk,
    _opt_str,
    _period_index,
    _plan_exprs,
    _plan_src,
    _reindex_to_schema,
    _union_schema,
    _write_partitioned,
)
from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_performance_schema import (
    EVENT_CLASS,
    GNMA_LOAN_PERFORMANCE_PLAN,
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
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.harmonized import credit_score, flag_to_bool
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.gnma.load import PORTFOLIO_CUTOVER, scan_portfolio

logger = get_logger(__name__)


# ===================== loan_performance (loan x month grain) ================
# GNMA perf = the loan-level portfolio (loan x reporting-month) through
# `sources.gnma.load.scan_portfolio`: llmon1 (Ginnie I) + llmon2 (Ginnie II) before
# 2026-07, the single llmon file from it, split by `program`. One row per (loan, As-of
# month); the time-varying fields only -- origination-snapshot covariates
# (purpose/LTV/FICO/...) live on `loan_issuance` and join by `loan_key`. Silver is
# canonical and already decoded. `Months Delinquent` is already the 0-6 capped cycle
# count (6 = 6+). Events: `Current Month Liquidation Flag` Y/N + `Removal Reason` ->
# unified `event_type`; credit event = Removal Reason 2 or 3 (delinquent buyout,
# foreclosure with claim). Pool-level CUSIP/pool_type come with each loan row.

#: GNMA Removal Reason -> unified event_type (official V1.8 codes).
_GNMA_REMOVAL_REASON: dict[str, str] = {
    "1": "voluntary_prepay",
    "2": "dq_buyout",
    "3": "foreclosure_reo",
    "4": "loss_mit_workout",
    "5": "substitution",
    "6": "other_removal",
}


#: Removal Reasons that are credit/default terminal events (foreclosure 3 grouped with buyout 2).
_GNMA_CREDIT_EVENT_CODES: frozenset[str] = frozenset({"2", "3"})


#: Months by which the v2.0 portfolio (from `PORTFOLIO_CUTOVER`) is ahead of v1.8 on loan
#: age and behind it on remaining term: exactly one on every loan with both values, in both
#: overlap months (12,423,780 loans in 2026-07, 12,465,887 in 2026-08).
_GNMA_V20_AGE_SHIFT = 1


def _gnma_portfolio_month(month: str) -> pl.LazyFrame:
    """One GNMA portfolio month, on the v1.8 loan-age convention throughout.

    Ginnie's v2.0 layout counts loan age one month higher, and remaining term one month
    lower, than v1.8 did for the same loan. Silver keeps both as published; combined
    takes them back to v1.8 from `PORTFOLIO_CUTOVER` so the series has no step there.

    Args:
        month: The reporting month ``YYYYMM``.

    Returns:
        The month's loan rows (`scan_portfolio` columns, ``program`` included).
    """
    lf = scan_portfolio(min_month=month, max_month=month)
    if month < PORTFOLIO_CUTOVER:
        return lf
    return lf.with_columns(
        pl.col("loan_age_in_months") - _GNMA_V20_AGE_SHIFT,
        pl.col("remaining_loan_term_in_months") + _GNMA_V20_AGE_SHIFT,
    )


def _gnma_event_type(rr: pl.Expr) -> pl.Expr:
    """Map a (stripped) GNMA Removal Reason code to the unified event_type.

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


def _gnma_perf_readers(schema: pl.Schema) -> Readers:
    """``_gnma_loan_readers`` plus ``rmm``, the remaining-term window scrub."""
    read = _gnma_loan_readers(schema)

    def rmm(src: tuple[str, ...]) -> pl.Expr:
        c = read["int"](src)
        return pl.when(c.is_between(_RMM_LO, _RMM_HI)).then(c).otherwise(None)

    return {**read, "rmm": rmm}


def _assemble_gnma_loan_performance(month: str) -> pl.LazyFrame:
    """Assemble one GNMA portfolio month into unified loan_performance rows.

    Args:
        month: The reporting month ``YYYYMM``.

    Returns:
        A LazyFrame of unified loan-performance rows for ``month`` (pre-reindex).
    """
    lf = _gnma_portfolio_month(month)
    schema = lf.collect_schema()
    read = _gnma_perf_readers(schema)
    rr = read["str"](_plan_src(GNMA_LOAN_PERFORMANCE_PLAN, "event_raw_code"))
    derived = {
        "program": pl.col("program"),
        "reporting_period": pl.lit(int(month), dtype=pl.Int64),
        "reporting_year": pl.lit(int(month[:4]), dtype=pl.Int64),
        "event_type": _gnma_event_type(rr),
        "event_class": _event_class_expr(_gnma_event_type(rr)),
        "is_credit_event": rr.is_in(list(_GNMA_CREDIT_EVENT_CODES)).fill_null(value=False),
    }
    return lf.select(
        *_plan_exprs(GNMA_LOAN_PERFORMANCE_PLAN, read, derived),
        read["raw"](("cusip_number",)).alias("cusip"),
        read["raw"](("pool_type",)).alias("pool_type"),
    )


def _gnma_perf_canon_schema() -> pl.Schema:
    """Resolve GNMA's loan_performance output schema (constant column set)."""
    return _assemble_gnma_loan_performance(_gnma_loan_months("portfolio")[-1]).collect_schema()


def _build_gnma_loan_performance(out_dir: Path, canon: pl.Schema) -> int:
    """Build GNMA's loan_performance rows, one portfolio month at a time.

    Ginnie I and II together, split by ``program``; each month streams independently so
    peak memory stays one month wide.

    Args:
        out_dir: Dataset root.
        canon: Canonical output schema.

    Returns:
        Rows written for GNMA.
    """
    months = _gnma_loan_months("portfolio")
    logger.info("  GNMA: %d portfolio months", len(months))
    total = 0
    for month in months:
        chunk = _assemble_gnma_loan_performance(month).collect(engine="streaming")
        n = _write_partitioned(chunk, canon, out_dir, "reporting_year", "GNMA", f"gnma_{month}")
        total += n
        logger.debug("    GNMA/%s: %d rows", month, n)
    return total


# ===================== GSE loan_performance (SF-perf ⋈ MLLD/FU fusion) =======
# GSE spine. Base = SF Loan-Performance monthly panel (vintage-partitioned;
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
    #: SF performance files published outside `sf_perf_dir`, as (file, source_universe)
    #: pairs: Fannie's HARP panel, whose directory also holds its issuance siblings.
    sf_extra: tuple[tuple[Path, str], ...] = ()


def _sf_perf_files(spec: _GsePerfSpec) -> list[tuple[Path, _GsePerfSpec]]:
    """Every SF performance file of a GSE, each with the spec that tags its population.

    Fannie's Exclusion files (``{quarter}EXCL``) hold the ARM, Alt-A and other loans
    left out of the standard dataset, and its HARP file the underwater refinances. Both
    are fused like any vintage file but keep their own ``source_universe``.

    Args:
        spec: The GSE spec.

    Returns:
        ``(file, spec)`` pairs, the spec's ``source_universe`` set per file.
    """
    files = [
        (f, replace(spec, source_universe="fnma_exclusion") if f.stem.endswith("EXCL") else spec)
        for f in sorted(spec.sf_perf_dir.glob("*.parquet"))
    ]
    for f, universe in spec.sf_extra:
        if f.exists():
            files.append((f, replace(spec, source_universe=universe)))
        else:
            logger.warning("  %s: %s absent; its %s rows are skipped", spec.agency, f, universe)
    return files


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
            # Same layout and id namespace as Primary, and id-disjoint from it.
            sf_extra=((fnma / "harp" / "performance.parquet", "gse_harp_disclosure"),),
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


def _materialize_umbs_perf(spec: _GsePerfSpec, dest: Path) -> None:
    """Stream the UMBS monthly-factor panel (perf cols + reporting_period) to one parquet.

    Concatenates all MLLD/FU month files (schema drifts -> ``diagonal_relaxed``),
    keyed by ``loan_key_umbs`` with ``reporting_period`` from the filename.
    """
    files = sorted(spec.umbs_perf_dir.glob("*.parquet"))
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
            expr = flag_to_bool(expr)
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
            expr = flag_to_bool(expr)
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
        pl.when(pl.col("estimated_ltv") >= _LTV_SENTINEL_FLOOR)
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
        credit_score(pl.col("updated_credit_score")).alias("updated_credit_score"),
        # '7'/'9' = "not available / not applicable" mixed into the plan-letter space
        # (the mi-cancellation indicator is handled at emit time by flag_to_bool). The
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
    out = out.with_columns([pl.lit(None).alias(c) for c in final if c not in present])
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
    sf_files = [f for f, _ in _sf_perf_files(spec)]
    sf_union = pl.concat([pl.scan_parquet(str(f)) for f in sf_files], how="diagonal_relaxed")
    cw = _gse_crosswalk(spec.crosswalk_file)
    fused = _fuse_gse_perf(spec, sf_union, pl.scan_parquet(tmp_umbs), cw)
    return _finalize_gse_perf(fused, spec).collect_schema()


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


def _split_umbs_perf_by_sf_file(
    spec: _GsePerfSpec, sf_files: list[tuple[Path, _GsePerfSpec]], cw: pl.LazyFrame, tmp_umbs: Path
) -> Path:
    """Split the materialized UMBS panel by the SF file its crosswalked twin lives in.

    One pass over ``tmp_umbs`` replaces a full re-scan per SF file: a semi-join cannot
    skip row groups, so reading the whole panel once per vintage cost 189 reads of 14 GB
    for FNMA. A loan whose SF id sits in two files is copied to both slices, as the
    per-file semi-join did.

    Args:
        spec: The GSE spec.
        sf_files: The GSE's SF performance files, in build order.
        cw: The GSE's crosswalk (``sf_id``, ``umbs_id``).
        tmp_umbs: The materialized UMBS panel.

    Returns:
        The directory holding one ``_sf_part={i}`` slice per SF file with UMBS rows.
    """
    parts_dir = tmp_umbs.with_name(f"{tmp_umbs.stem}_parts")
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    sf_parts = pl.concat(
        [
            pl.scan_parquet(str(f))
            .select(pl.col(spec.sf_key_col).cast(pl.String).alias("sf_id"))
            .unique()
            .with_columns(pl.lit(i, dtype=pl.Int32).alias("_sf_part"))
            for i, (f, _) in enumerate(sf_files)
        ]
    )
    umbs_parts = cw.join(sf_parts, on="sf_id", how="inner").select("umbs_id", "_sf_part").unique()
    pl.scan_parquet(tmp_umbs).join(
        umbs_parts, left_on="loan_key_umbs", right_on="umbs_id", how="inner"
    ).sink_parquet(pl.PartitionBy(str(parts_dir), key=["_sf_part"], include_key=False), mkdir=True)
    return parts_dir


def _build_gse_loan_performance(
    spec: _GsePerfSpec, out_dir: Path, canon: pl.Schema, tmp_umbs: Path
) -> None:
    """Fuse one GSE's SF-perf ⋈ UMBS panel, chunked by SF vintage file, into ``out_dir``.

    Each vintage chunk STREAMS to the partitioned layout via ``sink_parquet`` -- never
    ``collect``-ing the whole cohort, which OOMs on the big refi vintages (the per-vintage
    join build side stays bounded; only the materialized output did not). Each chunk
    reads only its own slice of the UMBS panel (`_split_umbs_perf_by_sf_file`).
    """
    cw = _gse_crosswalk(spec.crosswalk_file)
    sf_files = _sf_perf_files(spec)
    logger.info("  %s: %d SF-perf files (streaming sink)", spec.agency, len(sf_files))
    parts_dir = _split_umbs_perf_by_sf_file(spec, sf_files, cw, tmp_umbs)
    no_umbs_rows = pl.scan_parquet(tmp_umbs).head(0)
    for i, (sf_file, file_spec) in enumerate(sf_files):
        sf = pl.scan_parquet(str(sf_file))
        sf_ids = sf.select(pl.col(spec.sf_key_col).cast(pl.String).alias("sf_id")).unique()
        cw_chunk = cw.join(sf_ids, on="sf_id", how="semi")
        part = parts_dir / f"_sf_part={i}"
        umbs_chunk = pl.scan_parquet(part / "*.parquet") if part.is_dir() else no_umbs_rows
        fused = _finalize_gse_perf(_fuse_gse_perf(file_spec, sf, umbs_chunk, cw_chunk), file_spec)
        _reindex_to_schema(fused, canon).sink_parquet(
            _perf_partition(out_dir, f"{spec.agency.lower()}_part{i:03d}"), mkdir=True
        )
        logger.debug("    %s/part%03d (%s): sunk", spec.agency, i, sf_file.stem)
    shutil.rmtree(parts_dir)
    # UMBS-only loans (no SF twin: fresh issuance not yet in SF, ARMs) -> umbs_only rows.
    umbs_ids_all = cw.select("umbs_id").unique()
    umbs_only = pl.scan_parquet(tmp_umbs).join(
        umbs_ids_all, left_on="loan_key_umbs", right_on="umbs_id", how="anti"
    )
    umbs_only_h = _umbs_perf_helpers(
        umbs_only.with_columns(pl.lit(None, dtype=pl.String).alias("loan_key_sf"))
    )
    _reindex_to_schema(_finalize_gse_perf(umbs_only_h, spec), canon).sink_parquet(
        _perf_partition(out_dir, f"{spec.agency.lower()}_umbs_only"), mkdir=True
    )
    logger.debug("    %s/umbs_only: sunk", spec.agency)


# ===================== loan_terminations (one terminal event per loan) =======
# A terminal row blanks part of the loan's state: Fannie's leaves the loan age and current
# rate empty, Ginnie's the loan age (and the balance on 1-5% of removals); Freddie's
# carries all of it. Every terminations row takes its state from the terminal row where
# disclosed and otherwise from the loan's previous disclosed month, carrying the age
# forward by the months between.
def _removal_state(
    period: pl.Expr, age: pl.Expr, rate: pl.Expr, dlq: pl.Expr, upb: pl.Expr
) -> list[pl.Expr]:
    """The state helpers `_carry_removal_state` reads, typed and sentinel-scrubbed."""
    age = age.cast(pl.Int64, strict=False)
    rate = rate.cast(pl.Float64, strict=False)
    return [
        period.cast(pl.Int64).alias("_period"),
        pl.when(age >= _LOAN_AGE_FLOOR).then(age).alias("_age"),
        pl.when(rate != _RATE_SENTINEL).then(rate).alias("_rate"),
        dlq.cast(pl.Int64, strict=False).alias("_dlq"),
        upb.cast(pl.Float64, strict=False).alias("_upb"),
    ]


_REMOVAL_STATE_COLS: tuple[str, ...] = ("_period", "_age", "_rate", "_dlq", "_upb")


def _carry_removal_state(term: pl.LazyFrame, prev: pl.LazyFrame) -> pl.LazyFrame:
    """Fill each terminal row's blank state from the loan's previous month.

    Both frames carry ``loan_key`` and the `_removal_state` helpers; ``prev`` holds at
    most one row per loan.

    Returns:
        ``term`` with ``loan_age_at_removal``, ``interest_rate_at_removal``,
        ``delinquency_months_at_removal`` and ``_upb_carried`` in place of the helpers.
    """
    prev = prev.select("loan_key", *[pl.col(c).alias(f"{c}_prev") for c in _REMOVAL_STATE_COLS])
    gap = _period_index(pl.col("_period")) - _period_index(pl.col("_period_prev"))
    return (
        term.join(prev, on="loan_key", how="left")
        .with_columns(
            pl.coalesce("_age", pl.col("_age_prev") + gap).alias("loan_age_at_removal"),
            pl.coalesce("_rate", "_rate_prev").alias("interest_rate_at_removal"),
            pl.coalesce("_dlq", "_dlq_prev").alias("delinquency_months_at_removal"),
            pl.coalesce("_upb", "_upb_prev").alias("_upb_carried"),
        )
        .drop(*_REMOVAL_STATE_COLS, *[f"{c}_prev" for c in _REMOVAL_STATE_COLS])
    )


def _last_active_rows(active: pl.LazyFrame, term_keys: pl.LazyFrame) -> pl.LazyFrame:
    """Each terminated loan's latest non-terminal row (none where that month repeats)."""
    active = active.join(term_keys, on="loan_key", how="semi")
    last = active.group_by("loan_key").agg(pl.col("_period").max())
    return (
        active.join(last, on=["loan_key", "_period"], how="semi")
        .unique()
        .unique(subset=["loan_key"], keep="none")
    )


def _assemble_gse_terminations(spec: _GsePerfSpec, sf: pl.LazyFrame) -> pl.LazyFrame:
    """Extract one SF chunk's terminal (Zero-Balance) rows into the events grain."""
    names = set(sf.collect_schema().names())

    def opt(col: str) -> pl.Expr:
        return pl.col(col) if col in names else pl.lit(None)

    key = pl.col(spec.sf_key_col).cast(pl.String).alias("loan_key")
    zbc = _zbc_norm(pl.col("Zero Balance Code"))
    state = _removal_state(
        _period_from_date("Monthly Reporting Period"),
        opt("Loan Age"),
        opt("Current Interest Rate"),
        opt(SF_DELINQUENCY_COL),
        pl.lit(None),  # the SF release discloses the removal balance itself
    )
    exprs: list[pl.Expr] = [
        key,
        pl.lit(spec.loan_key_kind).alias("loan_key_kind"),
        pl.lit(spec.agency).alias("agency"),
        pl.lit(spec.source_universe).alias("source_dataset"),
        zbc.alias("zero_balance_code"),
        zbc.alias("event_raw_code"),
        _event_type_expr(zbc).alias("event_type"),
        _event_class_expr(_event_type_expr(zbc)).alias("event_class"),
        zbc.is_in(list(ZBC_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
        pl.col("Zero Balance Effective Date").alias("zero_balance_effective_date"),
        pl.col("Zero Balance Effective Date").dt.year().cast(pl.Int64).alias("zero_balance_year"),
        *state,
    ]
    for fld in GSE_TERM_PLAN:
        expr = _coalesce_present(fld["sf"], names)
        if expr is not None:
            exprs.append(expr.alias(fld["name"]))
    is_term = pl.col("Zero Balance Code").is_not_null()
    term = sf.filter(is_term).select(exprs)
    prev = _last_active_rows(sf.filter(~is_term).select(key, *state), term.select("loan_key"))
    return _carry_removal_state(term, prev).drop("_upb_carried")


def _gse_term_canon_schema(spec: _GsePerfSpec) -> pl.Schema:
    """Resolve the terminations output schema (schema-only; SF column union)."""
    sf_files = [f for f, _ in _sf_perf_files(spec)]
    sf_union = pl.concat([pl.scan_parquet(str(f)) for f in sf_files], how="diagonal_relaxed")
    return _assemble_gse_terminations(spec, sf_union).collect_schema()


def _build_gse_loan_terminations(spec: _GsePerfSpec, out_dir: Path, canon: pl.Schema) -> int:
    """Extract one GSE's terminal events from the SF-perf panel into ``out_dir``."""
    total = 0
    for i, (sf_file, file_spec) in enumerate(_sf_perf_files(spec)):
        chunk = _assemble_gse_terminations(file_spec, pl.scan_parquet(str(sf_file))).collect(
            engine="streaming"
        )
        n = _write_partitioned(
            chunk, canon, out_dir, "zero_balance_year", spec.agency, f"part{i:03d}"
        )
        total += n
    logger.info("  %s: %d termination events", spec.agency, total)
    return total


def _gnma_src(name: str) -> str:
    """The portfolio silver column behind GNMA ``loan_performance`` column ``name``."""
    return _plan_src(GNMA_LOAN_PERFORMANCE_PLAN, name)[0]


def _gnma_removal_state(lf: pl.LazyFrame, month: str) -> list[pl.Expr]:
    """`_removal_state` for one portfolio month."""
    read = _gnma_loan_readers(lf.collect_schema())

    def col(name: str, reader: str) -> pl.Expr:
        return read[reader](_plan_src(GNMA_LOAN_PERFORMANCE_PLAN, name))

    return _removal_state(
        pl.lit(int(month)),
        col("loan_age", "int"),
        col("current_interest_rate", "gnma_num"),
        col("delinquency_months", "int"),
        col("current_upb", "gnma_num"),
    )


def _assemble_gnma_terminations(month: str, prev_month: str | None) -> pl.LazyFrame:
    """One portfolio month's removals as termination events, state carried from ``prev_month``.

    Ginnie publishes no loss waterfall, so a removal carries the event, its month and
    the loan's state. A loan absent from ``prev_month`` keeps only what its removal row
    discloses.

    Args:
        month: The removal month ``YYYYMM``.
        prev_month: The previous portfolio month, or None for the first.

    Returns:
        A LazyFrame of termination rows for ``month`` (pre-reindex).
    """
    lf = _gnma_portfolio_month(month)
    schema = lf.collect_schema()

    dsn, liq = _g_raw(_gnma_src("loan_key"), schema), _g_raw(_gnma_src("event_flag"), schema)
    if dsn is None or liq is None:
        raise ValueError(f"GNMA {month}: missing Disclosure Sequence Number / flag")
    rr = _opt_str(_g_raw(_gnma_src("event_raw_code"), schema))
    event_type = _gnma_event_type(rr)
    term = lf.filter(liq == "Y").select(
        dsn.alias("loan_key"),
        pl.lit("gnma_seq").alias("loan_key_kind"),
        pl.lit("GNMA").alias("agency"),
        pl.lit("gnma_disclosure").alias("source_dataset"),
        pl.lit(None, dtype=pl.String).alias("zero_balance_code"),
        rr.alias("event_raw_code"),
        event_type.alias("event_type"),
        _event_class_expr(event_type).alias("event_class"),
        rr.is_in(list(_GNMA_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
        pl.date(int(month[:4]), int(month[4:]), 1).alias("zero_balance_effective_date"),
        pl.lit(int(month[:4]), dtype=pl.Int64).alias("zero_balance_year"),
        *_gnma_removal_state(lf, month),
    )
    if prev_month is None:
        prev = term.select("loan_key", *_REMOVAL_STATE_COLS).head(0)
    else:
        p = _gnma_portfolio_month(prev_month)
        p_dsn = _g_raw(_gnma_src("loan_key"), p.collect_schema())
        if p_dsn is None:
            raise ValueError(f"GNMA {prev_month}: missing Disclosure Sequence Number")
        prev = _last_active_rows(
            p.select(p_dsn.alias("loan_key"), *_gnma_removal_state(p, prev_month)),
            term.select("loan_key"),
        )
    return _carry_removal_state(term, prev).rename({"_upb_carried": "upb_at_removal"})


def _gnma_term_canon_schema() -> pl.Schema:
    """Resolve GNMA's loan_terminations output schema (constant column set)."""
    months = _gnma_loan_months("portfolio")
    return _assemble_gnma_terminations(months[-1], months[-2]).collect_schema()


def _gnma_repeat_removals() -> pl.DataFrame:
    """Loans Ginnie removes in more than one month (37 of 22.3M), which have no one event."""
    lf = scan_portfolio()
    schema = lf.collect_schema()
    dsn, liq = _g_raw(_gnma_src("loan_key"), schema), _g_raw(_gnma_src("event_flag"), schema)
    if dsn is None or liq is None:
        raise ValueError("GNMA portfolio: missing Disclosure Sequence Number / flag")
    return (
        lf.filter(liq == "Y")
        .select(dsn.alias("loan_key"))
        .group_by("loan_key")
        .len()
        .filter(pl.col("len") > 1)
        .select("loan_key")
        .collect(engine="streaming")
    )


def _build_gnma_loan_terminations(out_dir: Path, canon: pl.Schema) -> int:
    """Write Ginnie I and II removals, one portfolio month (plus the month before) at a time.

    A loan removed in two months is left out: which removal ended it is unknowable.

    Returns:
        Rows written for GNMA.
    """
    repeats = _gnma_repeat_removals().lazy()
    logger.info(
        "  GNMA: %d loans removed in two months are excluded",
        repeats.select(pl.len()).collect().item(),
    )
    total = 0
    months = _gnma_loan_months("portfolio")
    for prev_month, month in zip([None, *months[:-1]], months, strict=True):
        chunk = (
            _assemble_gnma_terminations(month, prev_month)
            .join(repeats, on="loan_key", how="anti")
            .collect(engine="streaming")
        )
        total += _write_partitioned(
            chunk, canon, out_dir, "zero_balance_year", "GNMA", f"gnma_{month}"
        )
    logger.info("  GNMA: %d termination events", total)
    return total


def build_loan_performance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the master loan-level performance panel (loan x month).

    Tri-agency (GNMA + FNMA + FHLMC). One row per (loan, reporting month) carrying
    only the genuinely time-varying fields (the field-volatility allow-list:
    balance/rate/seasoning/delinquency/servicer/forbearance/event); origination-
    snapshot covariates live on ``loan_issuance`` and join by ``loan_key``.

    GNMA is a single disclosure spine (the loan-level portfolio, Ginnie I and II split by
    ``program``; ``llmon1`` ∪ ``llmon2`` before 2026-07 and ``llmon`` from it, with loan
    age and remaining term kept on the earlier convention). Each GSE FUSES its SF Loan-Performance panel
    (base) with the UMBS MLLD/FU monthly factor (enrichment + ~9-month recency tail)
    via the strictly-1:1 mbs_umbs crosswalk on ``reporting_period`` — a full-outer
    month join that retains a matched loan's SF-only early months and UMBS-only
    recent months, plus UMBS-only loans with no SF twin yet; ``month_source`` tags
    each row both/sf_only/umbs_only. All three agencies reindex to one canonical
    schema. Two further FNMA populations are fused the same way under their own
    ``source_universe``: the HARP panel (1.04M underwater-refi loans, disjoint from
    the Primary dataset; ``gse_harp_disclosure``) and the Exclusion files (ARM, Alt-A
    and other loans left out of the standard dataset; ``fnma_exclusion``). Filter on
    ``source_universe`` when the standard-acquisition universe is required (HARP is a
    program population, not a sample). Output is hive-partitioned by ``agency`` and ``reporting_year``
    under ``data/combined/loan_performance``. Terminal/loss fields are NOT here —
    see `build_loan_terminations`.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    return _build_dataset("loan_performance", _write_loan_performance, overwrite=overwrite)


def _write_loan_performance(out_dir: Path) -> None:
    """Write the ``loan_performance`` tree into ``out_dir`` (see `build_loan_performance`)."""
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
    canon = _union_schema(per_agency)

    logger.info("Building combined loan_performance (GNMA+FNMA+FHLMC+HARP) -> %s", out_dir)
    _build_gnma_loan_performance(out_dir, canon)
    for spec in specs:
        _build_gse_loan_performance(spec, out_dir, canon, temps[spec.agency])
        temps[spec.agency].unlink(missing_ok=True)
    # GSE chunks stream-sink (no row count returned); tally from the parquet footers,
    # per file -- a single len() over the tree overflows polars' 32-bit row index
    # (the panel passed 2**32 rows once the full GSE histories were in).
    total = _count_rows(out_dir)
    logger.info("loan_performance built: %d rows (GNMA+FNMA+FHLMC+HARP) -> %s", total, out_dir)


def build_loan_terminations(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the loan terminations master: one terminal event per loan, all three agencies.

    The sparse counterpart to `build_loan_performance`, kept out of the dense monthly
    panel because it is populated for ~1 month in ~42. Each GSE loan's terminal
    Zero-Balance row (prepay / third-party sale / short sale / REO / repurchase / note
    sale) carries the loss waterfall (proceeds, costs, recoveries, write-offs); several
    FNMA CRT loss fields are absent in the standard SF product (null here). FNMA HARP
    and Exclusion events are tagged ``source_dataset='gse_harp_disclosure'`` /
    ``'fnma_exclusion'``. Ginnie removals (``gnma_disclosure``) carry the event and the
    balance but no loss fields, which Ginnie does not publish; a loan Ginnie removes in
    two months is left out. Every row carries the loan's age, rate and months
    delinquent at removal, from the terminal row or, where that row leaves them blank,
    the previous month. ``event_raw_code`` holds the source code (a GSE Zero Balance
    Code or a Ginnie Removal Reason). Output is hive-partitioned by ``agency`` and
    ``zero_balance_year`` under ``data/combined/loan_terminations``.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    return _build_dataset("loan_terminations", _write_loan_terminations, overwrite=overwrite)


def _write_loan_terminations(out_dir: Path) -> None:
    """Write the ``loan_terminations`` tree into ``out_dir`` (see `build_loan_terminations`)."""
    specs = _gse_perf_specs()
    per_agency = [_gse_term_canon_schema(spec) for spec in specs]
    canon = _union_schema([_gnma_term_canon_schema(), *per_agency])

    logger.info("Building combined loan_terminations (GNMA+FNMA+FHLMC+HARP) -> %s", out_dir)
    total = _build_gnma_loan_terminations(out_dir, canon)
    for spec in specs:
        total += _build_gse_loan_terminations(spec, out_dir, canon)
    logger.info("loan_terminations built: %d events (GNMA+FNMA+FHLMC+HARP) -> %s", total, out_dir)
