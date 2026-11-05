"""The ``mbs_performance`` master: one row per security or pool and month."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined._common import (
    _build_dataset,
    _date_to_yyyymmdd,
    _plan_exprs,
    _reindex_to_schema,
)
from mortgage_data_manager.combined.mbs_issuance import (
    _GNMA_PLATINUM_PROGRAM,
    _GNMA_PROJECT_POOL_TYPES,
    _gse_program,
    _gse_structure_flags,
    _mbs_readers,
    _pick,
    _pick_num,
    _pick_str,
)
from mortgage_data_manager.combined.mbs_performance_schema import (
    CANONICAL_MBS_PERFORMANCE_SCHEMA,
    GN_MEGA_PERFORMANCE_PLAN,
    GNMA_HMBS_POOL_TYPES,
    GNMA_MBS_PERFORMANCE_PLAN,
    GNMA_PLATINUM_HMBS_INDICATOR,
    GNMA_PLATINUM_ISSUER_NUMBER,
    GNMA_PLATINUM_MBS_PERFORMANCE_PLAN,
    GNMA_WA_PERFORMANCE_PLAN,
    GSE_MBS_PERFORMANCE_PLAN,
    GSE_TERMINAL_STATUS_INDICATORS,
)
from mortgage_data_manager.core.amortization import scheduled_principal, smm_to_cpr
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.dates import (
    mmddyyyy_to_date,
    month_to_date,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import recreate_dir

logger = get_logger(__name__)


# mbs_performance: number of (agency, pool_id)-hash shards for the windowed
# derivation, so peak memory is ~1/N of the full pool-month panel (GNMA ~40M +
# FNMA ~43M + FHLMC; each pool's whole history hashes to one shard for any N).
_MBS_PERF_SHARDS = 16


#: Calendar dates the build computes on as ``Int32`` ``YYYYMMDD`` (feed decoding, the
#: remaining-term arithmetic, the synthesized termination month) and publishes as
#: ``pl.Date``, like every other combined master.
_MBS_PERF_DATE_COLS: tuple[str, ...] = ("issue_date", "maturity_date", "termination_date")


def _yyyymmdd_to_date(expr: pl.Expr) -> pl.Expr:
    """``Int32`` ``YYYYMMDD`` -> ``pl.Date`` (null-safe)."""
    return pl.date(expr // 10000, expr // 100 % 100, expr % 100)


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
    only and NOT the paydown panel. Both store the packed date
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
        month: The as-of ``YYYYMM`` (from the filename), used for ``as_of_month``.

    Returns:
        A LazyFrame at the canonical grain (pre-derivation columns only).
    """
    av = set(scan.collect_schema().names())
    corr = _pick(["Security Data Correction Indicator"], av, pl.String).cast(pl.String)
    issue_date = _date_to_yyyymmdd(mmddyyyy_to_date(_pick(["Issue Date"], av, pl.Int64)))
    structure_class, is_resec = _gse_structure_flags(av, agency=agency, issue_date_ymd=issue_date)
    derived = {
        "agency": pl.lit(agency),
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "program": _gse_program(issue_date),
        "structure_class": structure_class,
        "is_resecuritization": is_resec,
        "issue_date": issue_date,
        "maturity_date": _date_to_yyyymmdd(month_to_date(_pick(["Maturity Date"], av, pl.Int64))),
        "record_source": pl.when(corr == "Y")
        .then(pl.lit("correction"))
        .otherwise(pl.lit("original")),
        "source_dataset": pl.lit("FNM_MF" if agency == "FNMA" else "FD"),
    }
    return scan.select(_plan_exprs(GSE_MBS_PERFORMANCE_PLAN, _mbs_readers(av), derived))


def _assemble_gnma_mbs_performance(scan: pl.LazyFrame, *, month: str, program: str) -> pl.LazyFrame:
    """Map one GNMA ``factorA{1,2}`` file to the unified mbs_performance grain.

    Mapping only. ``factorA`` canonical silver is already decoded (real USD/percent,
    ``security_factor`` a real fraction, packed dates parsed to ``pl.Date``), so no scaling
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
    cusip = _pick_str(["CUSIP Number"], av)
    pool_type = _pick_str(["Pool Type"], av)
    # Platinum stubs. Equivalent predicates in all 165 months of both files:
    # issuer 9999 <=> pool number starting 78 <=> null RPB. The issuer number is used
    # because the null-RPB form also catches unrelated early-vintage rows. Written as
    # a negated equality so a vintage without the column keeps its rows rather than
    # silently dropping all of them.
    is_platinum = (_pick_str(["Issuer Number"], av) == GNMA_PLATINUM_ISSUER_NUMBER).fill_null(
        value=False
    )
    # A blank pool type (null once silver nulls blanks) is not an excluded type.
    excluded_type = pool_type.is_in([*GNMA_HMBS_POOL_TYPES, *_GNMA_PROJECT_POOL_TYPES])
    return scan.filter((cusip != "") & ~excluded_type.fill_null(value=False) & ~is_platinum).select(
        _plan_exprs(
            GNMA_MBS_PERFORMANCE_PLAN,
            _mbs_readers(av),
            {
                "as_of_month": pl.lit(int(month), dtype=pl.Int64),
                "program": pl.lit(program),
                "source_dataset": pl.lit(dataset),
            },
        )
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
        month: The as-of ``YYYYMM`` (used as the join key ``as_of_month``).

    Returns:
        A LazyFrame keyed on ``(pool_id, as_of_month)`` with the WA columns.
    """
    scan = pl.scan_parquet(str(ps_file))
    av = set(scan.collect_schema().names())
    return scan.select(
        _plan_exprs(
            GNMA_WA_PERFORMANCE_PLAN,
            _mbs_readers(av),
            {"as_of_month": pl.lit(int(month), dtype=pl.Int64)},
        )
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
    the Platinum code space. ``security_factor`` is left as published; the only factors
    above 1 in the source (863 rows) are HMBS Platinum, which this exclusion drops.

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
    cusip = _pick_str(["cusip_number"], av)
    pind = _pick_str(["pool_indicator"], av)
    # ne_missing: a blank indicator (null once silver nulls blanks) is not HMBS.
    return scan.filter((cusip != "") & pind.ne_missing(GNMA_PLATINUM_HMBS_INDICATOR)).select(
        _plan_exprs(
            GNMA_PLATINUM_MBS_PERFORMANCE_PLAN,
            _mbs_readers(av),
            {
                "as_of_month": pl.lit(int(month), dtype=pl.Int64),
                "program": pl.lit(_GNMA_PLATINUM_PROGRAM),
            },
        )
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

    The feed publishes no current-balance column, so ``current_face`` is
    DERIVED as issuance UPB x ``Security Factor``. Note the double space in
    ``Issuance Investor  Security UPB``, which is the source spelling.

    Args:
        scan: A scan over one ``GN_MEGA_YYYYMM`` file.
        month: The as-of ``YYYYMM`` (from the filename).

    Returns:
        A LazyFrame at the canonical grain (pre-derivation columns only).
    """
    av = set(scan.collect_schema().names())
    orig = _pick_num(["Issuance Investor  Security UPB", "Issuance Investor Security UPB"], av)
    derived = {
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "current_face": orig * _pick_num(["Security Factor"], av),
    }
    return scan.select(_plan_exprs(GN_MEGA_PERFORMANCE_PLAN, _mbs_readers(av), derived))


def _apply_mbs_performance_derivations(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply the windowed prepayment derivations (over ``[agency, pool_id]``).

    The input must already be sorted by ``[agency, pool_id, as_of_month]``. Uses the
    backward-looking (prior-month) convention: the first
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
        lf: The assembled pool-month panel, pre-sorted by ``(agency, pool_id, as_of_month)``.

    Returns:
        The panel with the derived prepayment columns populated.
    """
    upb = pl.col("current_face")
    # order_by makes the shift correct regardless of physical row order after a
    # streaming re-partition (matches the cpr_3m rolling_mean call below).
    upb_prev = upb.shift(1).over(["agency", "pool_id"], order_by="as_of_month")
    warm_wa_prev = (
        pl.col("wa_rem_term")
        .shift(1)
        .over(["agency", "pool_id"], order_by="as_of_month")
        .cast(pl.Float64)
    )
    # Proxy term: whole months from the as-of month (YYYYMM) to stated maturity
    # (YYYYMMDD), floored at 1 so a past-due maturity amortizes to a full-balance
    # schedule (which clips prepayment to 0) rather than going negative.
    months_to_maturity = (
        (pl.col("maturity_date") // 10000 * 12 + pl.col("maturity_date") // 100 % 100)
        - (pl.col("as_of_month") // 100 * 12 + pl.col("as_of_month") % 100)
    ).cast(pl.Float64)
    rate_amort = pl.coalesce(pl.col("wac"), pl.col("pass_through_rate"))
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
    ``(agency, pool_id, as_of_month)``, hive-partitioned by ``agency`` and
    ``as_of_year`` under ``data/combined/mbs_performance``.

    Sources:

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
    windowed derivation before the partitioned write.

    Termination is resolved from four signals in descending order of authority: the
    GSE ``Security Status Indicator``, GNMA's disclosed ``ptermmon`` date, the pool's
    own disappearance from a month-contiguous feed, and a residual zero-factor row
    whose pool still reports at the frontier. ``termination_source`` records which
    one fired, so a synthesized ``termination_date`` is never mistaken for a
    disclosed one.

    **This master is a union of overlapping security layers**, so summing
    ``current_face`` over a month double counts. The GSE factor feeds mix base
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
    return _build_dataset("mbs_performance", _write_mbs_performance, overwrite=overwrite)


def _write_mbs_performance(out_dir: Path) -> None:
    """Write the ``mbs_performance`` tree into ``out_dir`` (see `build_mbs_performance`)."""
    canon = pl.Schema(CANONICAL_MBS_PERFORMANCE_SCHEMA)
    work = pl.Schema({**canon, **dict.fromkeys(_MBS_PERF_DATE_COLS, pl.Int32())})
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
    # so we cannot derive per file). Reindex each chunk to the working schema (the
    # canonical union, dates still YYYYMMDD) so the temp tree scans as one uniform table.
    tmp_dir = out_dir.parent / "_mbs_performance_tmp"
    recreate_dir(tmp_dir)

    logger.info("Building combined mbs_performance (FNMA + FHLMC + GNMA) -> %s", out_dir)

    def _spill(chunk: pl.DataFrame, tag: str) -> int:
        if chunk.is_empty():
            return 0
        _reindex_to_schema(chunk, work).write_parquet(tmp_dir / f"{tag}.parquet")
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
    # A month can have a second, mid-month FD file (fd210817 republishes five
    # August-2021 pools, Correction Indicator = Y), so spill under the full date
    # stamp, not the month, and let the per-shard dedup keep the correction.
    fd_files = sorted(fhlmc_fd.glob("fd*.parquet"))
    logger.info("  FHLMC: %d FD files", len(fd_files))
    for f in fd_files:
        # fd{YYMMDD} -> as-of YYYYMM (the factor month; YY is 20YY).
        stamp = f.stem[2:]  # YYMMDD
        month = f"20{stamp[:2]}{stamp[2:4]}"
        chunk = _assemble_gse_mbs_performance(
            pl.scan_parquet(str(f)), agency="FHLMC", month=month
        ).collect(engine="streaming")
        total_in += _spill(chunk, f"fhlmc_{stamp}")

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
                factor = factor.join(enrich, on=["pool_id", "as_of_month"], how="left")
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
                _gnma_wa_enrichment(pps_file, month), on=["pool_id", "as_of_month"], how="left"
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
            _pick_str(["CUSIP Number"], av).alias("cusip"),
            # Canonical silver decodes Termination Date to pl.Date; render to Int32 YYYYMMDD.
            _date_to_yyyymmdd(_pick(["Termination Date"], av, pl.Date)).alias("_ptermmon_date"),
        )

    gnma_terminations = (
        pl.concat([_scan_ptermmon(pf) for pf in pterm_files], how="diagonal_relaxed")
        .filter(pl.col("cusip") != "")
        .unique()
        .unique(subset=["cusip"], keep="none")
    )

    logger.info("  mapped %d pool-months; applying derivations...", total_in)

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
        .group_by("source_dataset")
        .agg(pl.col("as_of_month").max().alias("frontier"))
        .collect(engine="streaming")
        .iter_rows()
    )
    logger.info("  feed frontier months: %s", frontier_by_source)

    # --- Derivation + partitioned write, SHARDED to bound peak memory. ---
    # Window (.over) expressions do not stream, so a single collect of the full GNMA universe (~40M pool-
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
        # Collapse to one row per (agency, pool_id, as_of_month) BEFORE the windowed
        # derivation (a duplicated month would corrupt the prior-month shift). Two
        # sources violate the grain: (1) FNMA and FHLMC republish a few pool-months
        # as corrections (FNM_MF_{month}_N files, a second FD file in the month;
        # Correction Indicator = Y) — the correction wins; (2) three legacy Ginnie pool numbers (002590/004041/005338,
        # 2012-2015) exist as DISTINCT securities in both Ginnie I and Ginnie II
        # (overlapping numeric ranges, different CUSIPs). This grain cannot hold both
        # and nothing says which one a (pool_id, month) means, so both are dropped.
        # Safe within a shard: every row of a given (agency, pool_id) hashes here,
        # so no duplicate escapes to another shard.
        grain = ["agency", "pool_id", "as_of_month"]
        is_corr = (pl.col("record_source") == "correction").cast(pl.Int8)
        deduped = (
            shard_lf.filter(is_corr == is_corr.max().over(grain))
            .unique()
            .unique(subset=grain, keep="none")
        )
        return (
            _apply_mbs_performance_derivations(deduped.sort(["agency", "pool_id", "as_of_month"]))
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
                .over(["agency", "pool_id"], order_by="as_of_month")
                .alias("_log_surv3"),
                # Both structure columns are pool constants, but the GSE feeds blank
                # the descriptive block on a paid-off row and the assembler leaves
                # those null. Fill within the pool so a resecuritization does not
                # read as pass_through in the month it retires.
                *[
                    pl.col(col)
                    .forward_fill()
                    .backward_fill()
                    .over(["agency", "pool_id"], order_by="as_of_month")
                    .alias(col)
                    for col in ("structure_class", "is_resecuritization")
                ],
            )
            .with_columns(
                # min_samples=3 makes cpr_3m null until the pool has three consecutive
                # months, and null wherever any smm in the window is (which covers the
                # amort_basis-null rows). Ginnie blanks its 3-month CPR the same way.
                # (clipped: float rounding leaves -1e-13 on zero-prepayment windows)
                (100.0 * (1.0 - (4.0 * pl.col("_log_surv3")).exp()))
                .clip(lower_bound=0.0)
                .alias("cpr_3m"),
                # Shard-local by construction: every row of a given (agency, pool_id)
                # hashes into this shard, so the per-pool max is the global one.
                pl.col("as_of_month").max().over(["agency", "pool_id"]).alias("_pool_last"),
                # Pool-level: a pool sits in exactly one feed bar the three legacy
                # Ginnie pool numbers that exist in both factor files, and taking the
                # max keeps one frontier per pool so _disappeared cannot disagree
                # across its own rows.
                pl.col("source_dataset")
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
                    | (pl.col("security_factor") == 0)
                    | (pl.col("current_face") == 0)
                    | (pl.col("_disappeared") & (pl.col("as_of_month") == pl.col("_pool_last")))
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
                (pl.col("as_of_month") // 100).cast(pl.Int32).alias("as_of_year"),
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
                *[_yyyymmdd_to_date(pl.col(c)).alias(c) for c in _MBS_PERF_DATE_COLS],
            )
            .select(list(canon.keys()))
        )

    logger.info("  applying derivations across %d shards -> %s", _MBS_PERF_SHARDS, out_dir)
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
