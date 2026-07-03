"""Builders for the combined cross-source master datasets.

Each builder assembles one master dataset from the per-source silver layers
(see ``config.DATASET_MAP`` for the source wiring). ``build_loan_issuance`` is
implemented for all three agencies — the two GSEs (FNMA SF Loan-Perf ⋈ FNM_ILLD,
FHLMC SF origination ⋈ FRE_ILLD, each via its mbs_umbs crosswalk) plus GNMA (a
single ``dailyllmni`` spine, L->P CUSIP attach); the remaining builders are still
``NotImplementedError`` scaffolds.

Keep these intentionally few — this package builds a small curated set of
masters, not the full vendor-style aggregation catalog.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_issuance_schema import IL_FIRST_FIELDS, FieldSpec
from mortgage_data_manager.combined.loan_performance_schema import (
    GSE_PERF_PLAN,
    GSE_TERM_PLAN,
    SF_DELINQUENCY_COL,
    UMBS_DAYS_DELINQUENT_COL,
    ZBC_CREDIT_EVENT_CODES,
    ZBC_EVENT_TYPE,
    PerfField,
)
from mortgage_data_manager.combined.mbs_performance_schema import (
    CANONICAL_MBS_PERFORMANCE_SCHEMA,
    GNMA_HMBS_POOL_TYPES,
)
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.dates import (
    mmddyy_to_date,
    mmddyyyy_to_date,
    month_to_date,
    yyyymmdd_to_date,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.reference.gnma_pool_types import (
    GNMA_POOL_TYPES,
    GNMA_PROGRAM_BY_ISSUE_TYPE,
)

logger = get_logger(__name__)

# Valid Classic-FICO range; values outside (9999/7777/0/blank) are sentinels -> null.
_FICO_LO, _FICO_HI = 300, 850
# Underwriting-ratio sentinels (FNMA: 999 = NA; DTI also -99999 = Exempt).
_RATIO_SENTINELS = (999, -99999)
# mbs_performance: number of (agency, pool_id)-hash shards for the windowed
# derivation, so peak memory is ~1/N of the full pool-month panel (GNMA is ~40M).
_MBS_PERF_SHARDS = 8


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
    """
    num = expr.cast(pl.Float64, strict=False)
    return pl.when(num.is_between(_FICO_LO, _FICO_HI)).then(num).otherwise(None)


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
    drives all dispositions. Harmonization follows D-1..D-6 (see the schema module
    and the investigation report). The two GSEs differ only in SF key column and
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
    il_names = set(umbs.collect_schema().names())

    # --- SF side: emit base PK + every SF-sourced unified field ---
    sf_exprs: list[pl.Expr] = [pl.col(sf_key_col).cast(pl.String).alias("loan_key_sf")]
    for spec in plan:
        name, disp = spec["name"], spec["disposition"]
        if disp in {"drop", "key", "umbs_only"} or not spec["sf"]:
            continue
        expr = _coalesce_present(spec["sf"], sf_names)
        if expr is None:
            continue
        if disp == "shared_both":  # issuance_investor_upb: SF carries the full-loan UPB
            sf_exprs.append(expr.alias("issuance_full_upb"))
        else:  # sf_only / shared_keep / shared_harmon all land under their unified name
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
        if disp == "umbs_only":
            il_exprs.append(expr.alias(name))
        elif disp == "shared_both":  # ILLD carries the participation-weighted investor UPB
            il_exprs.append(expr.alias("issuance_investor_upb"))
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
    ]
    for spec in plan:
        if spec["disposition"] in {"drop", "key"}:
            continue
        if spec["disposition"] == "shared_both":
            final += ["issuance_full_upb", "issuance_investor_upb"]
        else:
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


@dataclass(frozen=True)
class _GseSpec:
    """Per-GSE wiring for the loan-issuance build."""

    agency: str  # "FNMA" / "FHLMC"
    source_dataset: str  # provenance literal
    plan: list[FieldSpec]  # field plan for this GSE
    sf_dir: Path  # SF base directory (one parquet per quarter / vintage year)
    illd_dir: Path  # UMBS ILLD silver directory
    crosswalk_file: Path  # mbs_umbs crosswalk for this GSE
    snapshot_gse: str  # MBSUMBSConfig.get_umbs_snapshot_file() key ("fnma"/"fhlmc")
    sf_key_col: str  # SF loan-id column name
    loan_key_kind: str  # namespace of the unified loan_key (§5)


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
            crosswalk_file=cw / "fnma_crosswalk.parquet",
            snapshot_gse="fnma",
            sf_key_col="Loan Identifier",
            loan_key_kind="gse_numeric",
        ),
        _GseSpec(
            agency="FHLMC",
            source_dataset="fhlmc_sf_orig",
            plan=FHLMC_LOAN_ISSUANCE_PLAN,
            sf_dir=MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "silver" / "origination",
            illd_dir=umbs / "FHLMC" / "ILLD",
            crosswalk_file=cw / "fhlmc_crosswalk.parquet",
            snapshot_gse="fhlmc",
            sf_key_col="Loan Sequence Number",
            loan_key_kind="sf_seq",
        ),
    ]


def _materialize_umbs(spec: _GseSpec, dest: Path) -> None:
    """Materialize a GSE's UMBS side (ILLD silver + first monthly snapshot) to parquet.

    Streams the concatenation to ``dest`` WITHOUT a global dedup (a blocking
    ``unique`` over ~28M rows OOMs on a small machine). Per-loan dedup — ILLD wins
    over the snapshot via ``_umbs_priority`` — is done cheaply per chunk in the
    builder, after the crosswalk has narrowed each chunk to ~1 quarter of loans.

    ILLD silver drifts dtype across months (e.g. ``Index`` String vs Int64), so the
    per-file scans are concatenated ``diagonal_relaxed`` (column union + supertypes).

    Args:
        spec: The GSE spec (plan + ILLD dir + snapshot key).
        dest: Output parquet path (single file).
    """
    from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSConfig

    il_src_cols = sorted({c for fs in spec.plan for c in fs["il"]})

    def _select_one(path: str, label: str, priority: int) -> pl.LazyFrame:
        scan = pl.scan_parquet(path)
        names = set(scan.collect_schema().names())
        sel = [pl.col("Loan Identifier").cast(pl.String).alias("loan_key_umbs")]
        sel += [pl.col(c) for c in il_src_cols if c in names and c != "Loan Identifier"]
        return scan.select(sel).with_columns(
            pl.lit(label).alias("umbs_source"),
            pl.lit(priority, dtype=pl.Int8).alias("_umbs_priority"),
        )

    frames = [_select_one(str(p), "illd", 0) for p in sorted(spec.illd_dir.rglob("*.parquet"))]
    snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file(spec.snapshot_gse)  # D-6 recovery
    if snapshot_file is not None:
        frames.append(_select_one(str(snapshot_file), "snapshot", 1))
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
    """
    sf_files = sorted(spec.sf_dir.glob("**/*.parquet"))
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


def _build_gse_loan_issuance(
    spec: _GseSpec, out_dir: Path, canon: pl.Schema, tmp_umbs: Path
) -> int:
    """Build one GSE's loan-issuance rows, chunked by SF file, into ``out_dir``.

    Each SF file's loans narrow the crosswalk and the UMBS side to the same loans
    before joining, so every join's build side and the per-loan dedup stay small.
    Chunks are reindexed to ``canon`` (the cross-GSE union) so the whole dataset
    scans as one uniform table.

    Args:
        spec: The GSE spec.
        out_dir: Dataset root.
        canon: Canonical (cross-GSE) output schema.
        tmp_umbs: Path to this GSE's materialized UMBS parquet.

    Returns:
        Rows written for this GSE.
    """
    crosswalk = _gse_crosswalk(spec)
    sf_files = sorted(spec.sf_dir.glob("**/*.parquet"))
    logger.info("  %s: %d SF files", spec.agency, len(sf_files))
    total = 0
    for i, sf_file in enumerate(sf_files):
        # Index-based tag: SF filenames are not unique across partitions (every
        # FHLMC vintage file is `data.parquet`), which would collide in shared
        # issuance_year dirs and overwrite rows.
        tag = f"part{i:03d}"
        sf_chunk = pl.scan_parquet(str(sf_file))
        sf_ids = sf_chunk.select(pl.col(spec.sf_key_col).cast(pl.String).alias("sf_id"))
        cw_chunk = crosswalk.join(sf_ids, on="sf_id", how="semi")
        umbs_ids = cw_chunk.select("umbs_id")
        umbs_chunk = (
            pl.scan_parquet(tmp_umbs)
            .join(umbs_ids, left_on="loan_key_umbs", right_on="umbs_id", how="semi")
            .sort("_umbs_priority")  # ILLD (0) before snapshot (1)
            .unique(subset=["loan_key_umbs"], keep="first")
        )
        chunk = _assemble_loan_issuance(
            spec.plan,
            sf_chunk,
            umbs_chunk,
            cw_chunk,
            agency=spec.agency,
            source_dataset=spec.source_dataset,
            sf_key_col=spec.sf_key_col,
            loan_key_kind=spec.loan_key_kind,
        ).collect(engine="streaming")
        n = _write_chunk(_reindex_to_schema(chunk, canon), out_dir, spec.agency, tag)
        total += n
        logger.debug("    %s/%s (%s): %d rows", spec.agency, tag, sf_file.stem, n)
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


def _g_norm(name: str) -> str:
    """Normalize a column name (drop all whitespace, lowercase) for robust matching.

    Pre-2021 GNMA files carry whitespace drift anywhere in the name (e.g. the
    ``Disclosure Sequence Number (...level )`` space before the paren, or
    ``Record Type L=...`` vs ``L = ...``), so all whitespace is removed before
    matching the canonical ``_G`` names — the descriptive phrases stay distinct.
    """
    return "".join(name.split()).lower()


def _g_raw(key: str, lookup: dict[str, str]) -> pl.Expr | None:
    """Resolve a GNMA L field to a trimmed-string expr, or None if absent this vintage.

    Late-added fields (origination date V1.6+, seller issuer id V1.6+, index type
    V1.7+) are simply missing from early schemas — callers null-fill via the
    ``_opt_*`` / scaling helpers, which all accept ``None``.
    """
    actual = lookup.get(_g_norm(_G[key]))
    return pl.col(actual).cast(pl.String).str.strip_chars() if actual is not None else None


def _g_blank_to_null(expr: pl.Expr) -> pl.Expr:
    """Map empty strings to null."""
    return pl.when(expr.str.len_chars() == 0).then(None).otherwise(expr)


def _opt_str(expr: pl.Expr | None) -> pl.Expr:
    """A blank-stripped String expr, or a typed null if the source column is absent."""
    return _g_blank_to_null(expr) if expr is not None else pl.lit(None, dtype=pl.String)


def _opt_int(expr: pl.Expr | None) -> pl.Expr:
    """An Int64 expr (sentinels -> null), or a typed null if absent."""
    return expr.cast(pl.Int64, strict=False) if expr is not None else pl.lit(None, dtype=pl.Int64)


def _gnma_rate(expr: pl.Expr | None) -> pl.Expr:
    """Decode a GNMA fixed-point rate (9(2)v9(3)) to percent (§6): /1000.

    The spec flags a V1.0 (Sep-Nov 2013) ``/100`` special case, but the silver
    data does not bear it out — every month back to 2013-09 stores rates as a
    5-digit ``0RRRR`` (e.g. ``03500`` -> 3.500%), so ``/1000`` applies uniformly.
    """
    if expr is None:
        return pl.lit(None, dtype=pl.Float64)
    scaled = expr.cast(pl.Float64, strict=False) / 1000.0
    return pl.when(scaled > 0).then(scaled).otherwise(None)


def _gnma_ratio(expr: pl.Expr | None) -> pl.Expr:
    """Decode a GNMA LTV/CLTV/DTI to integer percent; 0/blank -> null (§6, OQ#8)."""
    if expr is None:
        return pl.lit(None, dtype=pl.Int64)
    raw = expr.cast(pl.Float64, strict=False) / 100.0
    return pl.when(raw > 0).then(raw.round(0).cast(pl.Int64)).otherwise(None)


def _gnma_upb(expr: pl.Expr | None) -> pl.Expr:
    """Decode a GNMA UPB/OPB (cents) to USD (§6); 0/blank -> null."""
    if expr is None:
        return pl.lit(None, dtype=pl.Float64)
    raw = expr.cast(pl.Float64, strict=False) / 100.0
    return pl.when(raw > 0).then(raw).otherwise(None)


def _gnma_date(expr: pl.Expr | None) -> pl.Expr:
    """Parse a GNMA CCYYMMDD date string to ``pl.Date``; blank/sentinel/absent -> null."""
    if expr is None:
        return pl.lit(None, dtype=pl.Date)
    return yyyymmdd_to_date(expr)


def _gnma_purpose(purpose: pl.Expr, refi: pl.Expr | None) -> pl.Expr:
    """Unify GNMA (Loan Purpose, Refinance Type) to the loan_purpose enum (§4.1)."""
    refi_e = refi if refi is not None else pl.lit(None, dtype=pl.String)
    is_refi = purpose == "2"
    return (
        pl.when(purpose == "1")
        .then(pl.lit("purchase"))
        .when(is_refi & (refi_e == "3"))
        .then(pl.lit("refi_cash_out"))
        .when(is_refi & refi_e.is_in(["1", "2"]))
        .then(pl.lit("refi_rate_term"))
        .when(is_refi)
        .then(pl.lit("refi_unspecified"))
        .when(purpose == "3")
        .then(pl.lit("construction"))
        .when(purpose.is_in(["4", "5"]))
        .then(pl.lit("other"))
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
    lookup: dict[str, str] = {}
    for actual in lf.collect_schema().names():
        lookup.setdefault(_g_norm(actual), actual)

    def g(key: str) -> pl.Expr | None:
        return _g_raw(key, lookup)

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
        _opt_str(g("seller_issuer_id")).alias("seller_issuer_id"),
        # --- dates ---
        _gnma_date(g("first_payment")).alias("first_payment_date"),
        _gnma_date(g("maturity")).alias("maturity_date"),
        _gnma_date(g("orig_date")).alias("origination_date"),
        # --- amounts / terms / rate ---
        _gnma_upb(g("opb")).alias("original_upb"),
        _gnma_upb(g("upb_issuance")).alias("issuance_investor_upb"),
        _opt_int(g("term")).alias("loan_term"),
        _opt_int(g("age")).alias("loan_age"),
        _opt_int(g("rmm")).alias("remaining_months_to_maturity"),
        _gnma_rate(g("rate")).alias("note_rate"),
        # --- underwriting ---
        _gnma_ratio(g("ltv")).alias("ltv"),
        _gnma_ratio(g("cltv")).alias("cltv"),
        _gnma_ratio(g("dti")).alias("dti"),
        _fico_clean(_opt_str(g("credit_score"))).alias("representative_credit_score"),
        _opt_int(g("num_borrowers")).alias("number_of_borrowers"),
        _opt_int(g("units")).alias("number_of_units"),
        # --- categoricals (unified enum + raw) ---
        _gnma_purpose(_opt_str(g("purpose")), g("refi_type")).alias("loan_purpose"),
        _opt_str(g("purpose")).alias("loan_purpose_raw"),
        _opt_str(g("refi_type")).alias("refi_type"),
        channel.alias("channel"),
        _opt_str(tpo).alias("channel_raw"),
        pl.when(idx_present | margin_pos)
        .then(pl.lit("ARM"))
        .otherwise(pl.lit("FRM"))
        .alias("amortization_type"),
        _opt_str(g("fthb")).alias("first_time_homebuyer"),
        _opt_str(g("state")).alias("property_state"),
        _opt_str(g("msa")).alias("msa"),
        _gnma_gov_insurer(g("agency_loan_type")).alias("government_insurer"),
        # --- GNMA-only extensions ---
        _gnma_rate(g("upfront_mip")).alias("gov_upfront_mip_rate"),
        _gnma_rate(g("annual_mip")).alias("gov_annual_mip_rate"),
        _opt_str(g("dpa")).alias("down_payment_assistance"),
        _opt_str(g("buydown")).alias("buydown"),
        _gnma_arm_index(g("index_type")).alias("arm_index"),
        _opt_str(g("index_type")).alias("arm_index_raw"),
    )

    # --- attach pool-level CUSIP / program / pool_type via L->P on Pool ID ---
    p_path = _gnma_p_dir() / f"dailyllmni_{month}_P.parquet"
    if p_path.exists():
        p = (
            pl.scan_parquet(str(p_path))
            .select(
                pl.col("Pool ID").cast(pl.String).str.strip_chars().alias("_pool_key"),
                pl.col("CUSIP Number").cast(pl.String).str.strip_chars().alias("cusip"),
                _gnma_program(pl.col("Issue Type (X, C, or M)")).alias("program"),
                pl.col("Pool Type").cast(pl.String).str.strip_chars().alias("pool_type"),
            )
            .unique(subset=["_pool_key"])
        )
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
    ``crosswalk/mbs_umbs/{gse}_crosswalk.parquet``. Harmonization follows D-1..D-6
    (see the schema module and the investigation report). GNMA is a SINGLE
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
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

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
# (SF-perf base ⋈ MLLD/FU enrichment, fused on the 1:1 crosswalk + reporting_period
# per the 2026-06-25 alignment investigation) are the v2 follow-up.

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

    lookup: dict[str, str] = {}
    for actual in lf.collect_schema().names():
        lookup.setdefault(_g_norm(actual), actual)

    def g(key: str) -> pl.Expr | None:
        actual = lookup.get(_g_norm(_GP[key]))
        return pl.col(actual).cast(pl.String).str.strip_chars() if actual is not None else None

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
        _gnma_upb(g("upb")).alias("current_upb"),
        _gnma_rate(g("rate")).alias("current_interest_rate"),
        _opt_int(g("age")).alias("loan_age"),
        _opt_int(g("rmm")).alias("remaining_months_to_maturity"),
        # --- delinquency (already 0-6 capped; 6 = 6+) ---
        _opt_int(g("months_delinquent")).alias("delinquency_months"),
        _opt_str(g("months_delinquent")).alias("delinquency_raw"),
        _opt_int(g("months_prepaid")).alias("months_prepaid"),
        # --- event / termination ---
        (liq == "Y").fill_null(value=False).alias("event_flag")
        if liq is not None
        else pl.lit(False).alias("event_flag"),  # noqa: FBT003
        _gnma_event_type(rr).alias("event_type"),
        rr.alias("event_raw_code"),
        rr.is_in(list(_GNMA_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
        # --- geography ---
        _opt_str(g("state")).alias("property_state"),
    )

    # --- attach pool-level CUSIP / pool_type via L->P on Pool ID ---
    p_path = _gnma_llmon_p_dir(family) / f"{family}_{month}_P.parquet"
    if p_path.exists():
        p = (
            pl.scan_parquet(str(p_path))
            .select(
                pl.col("Pool ID").cast(pl.String).str.strip_chars().alias("_pool_key"),
                pl.col("CUSIP Number").cast(pl.String).str.strip_chars().alias("cusip"),
                pl.col("Pool Type").cast(pl.String).str.strip_chars().alias("pool_type"),
            )
            .unique(subset=["_pool_key"])
        )
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
            crosswalk_file=cw / "fnma_crosswalk.parquet",
            sf_key_col="Loan Identifier",
            loan_key_kind="gse_numeric",
        ),
        _GsePerfSpec(
            agency="FHLMC",
            sf_perf_dir=fhlmc / "performance",
            umbs_perf_dir=umbs / "FHLMC" / "FU",
            crosswalk_file=cw / "fhlmc_crosswalk.parquet",
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
        if expr is not None:
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
        if expr is not None:
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
        pl.lit("gse_disclosure").alias("source_universe"),
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
        zbc.is_not_null().alias("event_flag"),
        zbc.is_in(list(ZBC_CREDIT_EVENT_CODES)).fill_null(value=False).alias("is_credit_event"),
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

    The streaming-sink equivalent of :func:`_reindex_to_schema` -- keeps the plan lazy so
    the partitioned sink never materializes the whole chunk in memory.
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
        pl.lit("gse_disclosure").alias("source_dataset"),
        zbc.alias("zero_balance_code"),
        _event_type_expr(zbc).alias("event_type"),
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
        chunk = _assemble_gse_terminations(spec, pl.scan_parquet(str(sf_file))).collect(
            engine="streaming"
        )
        n = _write_term_chunk(
            _reindex_to_schema(chunk, canon), out_dir, spec.agency, f"part{i:03d}"
        )
        total += n
    logger.info("  %s: %d termination events", spec.agency, total)
    return total


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
    schema. Output is hive-partitioned by ``agency`` and ``reporting_year`` under
    ``data/combined/loan_performance``. Terminal/loss fields are NOT here — see
    :func:`build_loan_terminations`.

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
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

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

    logger.info("Building combined loan_performance (GNMA+FNMA+FHLMC) -> %s", out_dir)
    _build_gnma_loan_performance(out_dir, canon)
    for spec in specs:
        _build_gse_loan_performance(spec, out_dir, canon, temps[spec.agency], max_sf_files=None)
        temps[spec.agency].unlink(missing_ok=True)
    # GSE chunks stream-sink (no row count returned); tally from the parquet footers.
    total = pl.scan_parquet(str(out_dir)).select(pl.len()).collect().item()
    logger.info("loan_performance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


def build_loan_terminations(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the GSE loan termination / loss-waterfall events master (one row per event).

    The sparse counterpart to :func:`build_loan_performance`: each loan's terminal
    Zero-Balance row (prepay / third-party sale / short sale / REO / repurchase /
    note sale) with the loss waterfall (proceeds, costs, recoveries, write-offs).
    SF-sourced (SF carries the explicit ZBC; UMBS corroborates by disappearance),
    kept OUT of the dense monthly panel because it is populated for ~1 month in ~42.
    Several FNMA CRT loss fields are absent in the standard SF product (null here).
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
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    specs = _gse_perf_specs()
    per_agency = [_gse_term_canon_schema(spec) for spec in specs]
    canon = pl.concat(
        [pl.DataFrame(schema=s).lazy() for s in per_agency], how="diagonal_relaxed"
    ).collect_schema()

    logger.info("Building combined loan_terminations (FNMA+FHLMC) -> %s", out_dir)
    total = 0
    for spec in specs:
        total += _build_gse_loan_terminations(spec, out_dir, canon, max_sf_files=None)
    logger.info("loan_terminations built: %d events (FNMA+FHLMC) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


# ===================== mbs_issuance (security / pool grain) =================
# One row per security/pool at issuance. GSE spine = umbs/silver/{FNMA,FHLMC}/IS
# (new-issuance-only; FNMA & FHLMC share an identical 99-col schema, as-published
# dollars/percent — no scaling). GNMA spine = gnma/silver/nimonSFPS PS record
# (already decoded; only DTI needs x100, a fraction). CUSIP is the universal key.
# Clean tri-agency window 2020-05+ (GSE alone reaches 2019-06). v1 covers the base
# single-class pools (is_resecuritization=False); Megas/Platinum (separate files)
# and the legacy nissues pre-2020 GNMA backfill are deferred follow-ups. Spec §6
# corrections verified against silver: both GSEs' Issue Date is MMDDYYYY (not
# FHLMC=MMYYYY), and GNMA PS Original Aggregate Amount is already USD (not /100).

#: GNMA Pool Type codes that denote an ARM (security carries a margin).
_GNMA_ARM_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.is_arm
)
#: GNMA Pool Indicator -> issue_type enum (Appendix III-6 instr. 15).
_GNMA_ISSUE_TYPE_ENUM: dict[str, str] = {"X": "single_issuer", "C": "custom", "M": "multi_issuer"}


def _pick(names: list[str], available: set[str], dtype: pl.DataType | type[pl.DataType]) -> pl.Expr:
    """Coalesce the present source columns, or a typed null if none exist (vintage-robust)."""
    present = [n for n in names if n in available]
    if not present:
        return pl.lit(None, dtype=dtype)
    return pl.coalesce([pl.col(n) for n in present]) if len(present) > 1 else pl.col(present[0])


def _mbs_gnma_ymd(expr: pl.Expr) -> pl.Expr:
    """GNMA PS ``YYYYMMDD`` date string -> ``pl.Date``; blank/sentinel -> null."""
    return yyyymmdd_to_date(expr)


def _assemble_gse_mbs_issuance(lf: pl.LazyFrame, *, agency: str, month: str) -> pl.LazyFrame:
    """Map one GSE IS file (FNMA or FHLMC — identical schema) to the unified grain.

    GSE IS date columns are already ``pl.Date`` in the umbs silver layer (parsed
    bronze->silver via ``core.dates``), so they are read straight through.
    """
    av = set(lf.collect_schema().names())
    issue_date = _pick(["Issue Date"], av, pl.Date)
    margin = _pick(["WA Mortgage Margin"], av, pl.Float64).cast(pl.Float64, strict=False)
    index_present = _pick(["Index"], av, pl.String).cast(pl.String).str.strip_chars()
    return lf.select(
        pl.lit(agency).alias("agency"),
        pl.col("Security Identifier").cast(pl.String).alias("pool_id"),
        pl.col("CUSIP").cast(pl.String).alias("cusip"),
        _pick(["Prefix"], av, pl.String).alias("prefix"),
        pl.lit("UMBS").alias("program"),  # post-2019 GSE SF IS = UMBS (coarse; see prefix)
        _pick(["Subtype"], av, pl.String).alias("pool_type_raw"),
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
        _pick(["Social Indicator"], av, pl.String).alias("social_indicator"),
        _pick(["Green Indicator"], av, pl.String).alias("green_indicator"),
        pl.lit(value=False).alias("is_resecuritization"),
        _pick(["_record_source"], av, pl.String).alias("record_source"),
        issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
    )


def _assemble_gnma_mbs_issuance(lf: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one GNMA nimonSFPS PS file to the unified mbs_issuance grain."""
    av = set(lf.collect_schema().names())

    def num(names: list[str]) -> pl.Expr:
        return _pick(names, av, pl.String).cast(pl.Float64, strict=False)

    pind = _pick(["Pool Indicator"], av, pl.String).cast(pl.String).str.strip_chars()
    ptype = _pick(["Pool Type"], av, pl.String).cast(pl.String).str.strip_chars()
    issue_date = _mbs_gnma_ymd(_pick(["Issue Date"], av, pl.String))
    wagm = num(["WA Gross Margin (WAGM)"])
    return lf.select(
        pl.lit("GNMA").alias("agency"),
        _pick(["Pool ID"], av, pl.String).cast(pl.String).str.strip_chars().alias("pool_id"),
        _pick(["CUSIP Number"], av, pl.String).cast(pl.String).str.strip_chars().alias("cusip"),
        pl.lit(None, dtype=pl.String).alias("prefix"),
        pind.replace_strict(GNMA_PROGRAM_BY_ISSUE_TYPE, default=None).alias("program"),
        ptype.alias("pool_type_raw"),
        pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None).alias("issue_type"),
        issue_date.alias("issue_date"),
        _mbs_gnma_ymd(_pick(["Maturity Date"], av, pl.String)).alias("maturity_date"),
        pl.lit(int(month), dtype=pl.Int64).alias("as_of_month"),
        num(["Number of loans in pool"]).cast(pl.Int64, strict=False).alias("loan_count"),
        num(["Original Aggregate Amount"]).alias("original_face"),  # already USD (not /100)
        num(["Remaining Security RPB"]).alias("current_face"),
        num(["Pool UPB"]).alias("pool_upb"),
        num(["RPB Factor"]).alias("security_factor"),
        num(["Security Interest Rate"]).alias("pass_through_rate"),
        num(["WA Interest Rate (WAC) at Issuance", "WA Interest Rate (WAC)"]).alias("wac"),
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
        _pick(["Social Indicator"], av, pl.String).alias("social_indicator"),
        pl.lit(None, dtype=pl.String).alias("green_indicator"),
        pl.lit(value=False).alias("is_resecuritization"),
        pl.lit("original").alias("record_source"),  # GNMA has no reissue twin
        issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64).alias("issue_year"),
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


def build_mbs_issuance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the tri-agency master MBS/pool issuance dataset (GNMA+FNMA+FHLMC).

    Spine = the per-agency single-class issuance records: GSE ``umbs/silver/
    {FNMA,FHLMC}/IS`` (new-issuance-only; identical 99-col schema) and GNMA
    ``gnma/silver/nimonSFPS`` PS pool headers (already decoded). One row per
    security/pool at issuance, keyed on CUSIP. Output is hive-partitioned by
    ``agency`` and ``issue_year`` under ``data/combined/mbs_issuance``.

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
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
    gnma_ps = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / "nimonSFPS" / "PS"
    gse_dirs = {"FNMA": umbs / "FNMA" / "IS", "FHLMC": umbs / "FHLMC" / "IS"}

    # Canonical schema = union of GSE + GNMA assembler outputs (both emit the same
    # columns; the union just guards any future divergence).
    gse_sample = sorted(gse_dirs["FNMA"].glob("*.parquet"))
    gnma_sample = sorted(gnma_ps.glob("*.parquet"))
    canon = pl.concat(
        [
            pl.DataFrame(
                schema=_assemble_gse_mbs_issuance(
                    pl.scan_parquet(str(gse_sample[-1])), agency="FNMA", month="209912"
                ).collect_schema()
            ).lazy(),
            pl.DataFrame(
                schema=_assemble_gnma_mbs_issuance(
                    pl.scan_parquet(str(gnma_sample[-1])), month="209912"
                ).collect_schema()
            ).lazy(),
        ],
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
    logger.info("  GNMA: %d nimonSFPS PS files", len(gnma_files))
    for f in gnma_files:
        month = f.stem.split("_")[-2]  # nimonSFPS_YYYYMM_PS
        chunk = _assemble_gnma_mbs_issuance(pl.scan_parquet(str(f)), month=month).collect(
            engine="streaming"
        )
        total += _write_mbs_chunk(
            _reindex_to_schema(chunk, canon), out_dir, "GNMA", f"gnma_{month}"
        )

    logger.info("mbs_issuance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


def _date_to_yyyymmdd(expr: pl.Expr) -> pl.Expr:
    """Convert a ``pl.Date`` column to an ``Int32`` ``YYYYMMDD`` (null-safe).

    ``dt.month``/``dt.day`` return ``Int8``; cast each component to ``Int32`` first
    so ``month * 100`` does not overflow the ``Int8`` range.
    """
    return (
        expr.dt.year().cast(pl.Int32) * 10000
        + expr.dt.month().cast(pl.Int32) * 100
        + expr.dt.day().cast(pl.Int32)
    ).cast(pl.Int32, strict=False)


def _assemble_gse_mbs_performance(scan: pl.LazyFrame, *, agency: str, month: str) -> pl.LazyFrame:
    """Map one GSE monthly factor file to the unified mbs_performance grain.

    Mapping + scaling only (no cross-month state), so it is unit-testable on one
    file. The FHLMC ``FD`` (pool factor) feed is the per-pool monthly panel — the
    ``IS`` feed is new-issuance-only for both GSEs and is NOT the paydown panel
    (verified Phase-1). ``FD`` stores the packed date encodings as ``Int64``
    (``Issue Date`` = ``MMDDYYYY``; ``Maturity Date`` = ``M(M)YYYY``), so they are
    parsed here rather than read straight through.

    Args:
        scan: A scan over one FD monthly file.
        agency: Agency partition value (``FHLMC`` today; ``FNMA`` once an ingest exists).
        month: The as-of ``YYYYMM`` (from the filename), used for ``as_of_date``.

    Returns:
        A LazyFrame at the canonical grain (pre-derivation columns only).
    """
    av = set(scan.collect_schema().names())
    corr = _pick(["Security Data Correction Indicator"], av, pl.String).cast(pl.String)
    issue_date = _date_to_yyyymmdd(mmddyyyy_to_date(_pick(["Issue Date"], av, pl.Int64)))
    return scan.select(
        pl.lit(agency).alias("agency"),
        pl.col("Security Identifier").cast(pl.String).alias("pool_id"),
        _pick(["CUSIP"], av, pl.String).cast(pl.String).alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        # UMBS post-2019-06; legacy Gold PCs (majority of the FD panel) are MBS.
        pl.when(issue_date >= 20190601)
        .then(pl.lit("UMBS"))
        .otherwise(pl.lit("MBS"))
        .alias("program"),
        _pick(["Prefix"], av, pl.String).cast(pl.String).alias("pool_type"),
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
        _pick(["Involuntary Loan Removal (Loan Count)"], av, pl.Int64)
        .cast(pl.Int64, strict=False)
        .alias("involuntary_removal_count"),
        _pick(["Involuntary Loan Removal (Prior Month UPB)"], av, pl.Float64)
        .cast(pl.Float64, strict=False)
        .alias("involuntary_removal_upb"),
        _pick(["Security Status Indicator"], av, pl.String)
        .cast(pl.String)
        .alias("security_status_raw"),
        pl.when(corr == "Y")
        .then(pl.lit("correction"))
        .otherwise(pl.lit("original"))
        .alias("record_source"),
        pl.lit("FD").alias("_source_dataset"),
    )


def _assemble_gnma_mbs_performance(scan: pl.LazyFrame, *, month: str, program: str) -> pl.LazyFrame:
    """Map one GNMA ``factorA{1,2}`` file to the unified mbs_performance grain.

    Mapping + scaling only. ``factorA`` is raw fixed-point (apply ``_gnma_*``
    scalers): ``RPB Factor`` /1e8, UPB /100, rate /1000, dates ``MMDDYY``. Header
    rows (blank CUSIP) and HMBS pools (``Pool Type`` in the reverse-mortgage set)
    are filtered out. The WA block + ``loan_count`` are left-enriched from
    ``monthlySFPS`` PS in the builder (this assembler emits the factor-side only).

    Args:
        scan: A scan over one factorA1/factorA2 monthly file.
        month: The as-of ``YYYYMM`` (from the filename).
        program: ``GINNIE_I`` (factorA1) or ``GINNIE_II`` (factorA2).

    Returns:
        A LazyFrame at the canonical grain (factor side; WA cols null pending enrichment).
    """
    av = set(scan.collect_schema().names())
    dataset = "factorA1" if program == "GINNIE_I" else "factorA2"
    return scan.filter(
        (pl.col("CUSIP Number").cast(pl.String).str.strip_chars() != "")
        & (~pl.col("Pool Type").cast(pl.String).str.strip_chars().is_in(list(GNMA_HMBS_POOL_TYPES)))
    ).select(
        pl.lit("GNMA").alias("agency"),
        pl.col("Pool Number").cast(pl.String).str.strip_chars().alias("pool_id"),
        pl.col("CUSIP Number").cast(pl.String).str.strip_chars().alias("cusip"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        pl.lit(program).alias("program"),
        pl.col("Pool Type").cast(pl.String).str.strip_chars().alias("pool_type"),
        _date_to_yyyymmdd(mmddyy_to_date(_pick(["Pool Issue Date (MMDDYY)"], av, pl.String))).alias(
            "issue_date"
        ),
        _date_to_yyyymmdd(
            mmddyy_to_date(_pick(["Pool Maturity Date (MMDDYY)"], av, pl.String))
        ).alias("maturity_date"),
        _gnma_upb(_pick(["Original Aggregate Amount"], av, pl.String)).alias("orig_security_upb"),
        _gnma_upb(_pick(["Remaining Security RPB"], av, pl.String)).alias("current_security_upb"),
        (_pick(["RPB Factor"], av, pl.String).cast(pl.Float64, strict=False) / 1e8).alias(
            "rpb_factor"
        ),
        _gnma_rate(_pick(["Pool Interest Rate"], av, pl.String)).alias("passthrough_rate"),
        pl.lit("original").alias("record_source"),
        pl.lit(dataset).alias("_source_dataset"),
    )


def _gnma_wa_enrichment(ps_file: Path, month: str) -> pl.LazyFrame:
    """Scan one ``monthlySFPS`` PS file to the WA-block enrichment (already decoded).

    ``monthlySFPS`` PS carries the WA rate/age/term block and loan count as plain
    decoded numbers (no scaling), keyed on ``Pool ID`` (== factorA ``Pool
    Number``). Coverage starts 2020-05; pre-2020-05 pool-months get a null WA
    block (the caller left-joins).

    Args:
        ps_file: Path to one ``monthlySFPS_YYYYMM_PS`` file.
        month: The as-of ``YYYYMM`` (used as the join key ``as_of_date``).

    Returns:
        A LazyFrame keyed on ``(pool_id, as_of_date)`` with the WA columns.
    """
    scan = pl.scan_parquet(str(ps_file))
    av = set(scan.collect_schema().names())

    def num(names: list[str]) -> pl.Expr:
        return _pick(names, av, pl.String).cast(pl.Float64, strict=False)

    return scan.select(
        pl.col("Pool ID").cast(pl.String).str.strip_chars().alias("pool_id"),
        pl.lit(int(month), dtype=pl.Int32).alias("as_of_date"),
        num(["WA Interest Rate (WAC)"]).alias("wac"),
        num(["WA Loan Age (WALA)"]).cast(pl.Int32, strict=False).alias("wala"),
        num(["WA Remaining Months to Maturity (WARM)"]).cast(pl.Int32, strict=False).alias("warm"),
        num(["WA Original Loan Term (WAOLT)"]).cast(pl.Int32, strict=False).alias("waolt"),
        num(["Number of loans in pool"]).cast(pl.Int64, strict=False).alias("loan_count"),
    )


def _apply_mbs_performance_derivations(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply the §4 windowed prepayment derivations (over ``[agency, pool_id]``).

    The input must already be sorted by ``[agency, pool_id, as_of_date]``. Mirrors
    ``analytics.calculated.scheduled_principal`` locally (never import
    ``analytics`` — the dependency is analytics->combined, one-way). Uses the
    backward-looking (prior-month) convention the spec mandates: the first
    observed month per pool leaves prior-dependent fields null. ``smm`` is clipped
    to ``[0, 1]`` with non-finite nulled; ``unscheduled_principal`` keeps its raw
    (possibly negative) value. Stages are sequential ``with_columns`` because each
    field feeds the next (``scheduled`` -> ``unscheduled`` -> ``smm``).

    Args:
        lf: The assembled pool-month panel, pre-sorted by ``(agency, pool_id, as_of_date)``.

    Returns:
        The panel with the derived prepayment columns populated.
    """
    upb = pl.col("current_security_upb")
    # order_by makes the shift correct regardless of physical row order after a
    # streaming re-partition (matches the cpr_3m rolling_mean call below).
    upb_prev = upb.shift(1).over(["agency", "pool_id"], order_by="as_of_date")
    warm_prev = (
        pl.col("warm").shift(1).over(["agency", "pool_id"], order_by="as_of_date").cast(pl.Float64)
    )
    i = pl.col("wac") / 100.0 / 12.0  # monthly fraction
    growth = (1.0 + i) ** warm_prev
    smm_raw = (pl.col("unscheduled_principal") / pl.col("_smm_denom")).clip(
        lower_bound=0.0, upper_bound=1.0
    )
    return (
        lf.with_columns(
            (upb_prev - upb).alias("total_principal_reduction"),
            # Level-payment principal portion; fall back to 0 in degenerate cases
            # (i<=0, WARM_prev<=0, or UPB_prev null) rather than straight-line.
            pl.when((i > 0) & (warm_prev > 0) & (growth > 1.0) & upb_prev.is_not_null())
            .then(upb_prev * i / (growth - 1.0))
            .otherwise(0.0)
            .clip(lower_bound=0.0)
            .alias("scheduled_principal"),
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

      * **FHLMC** = ``umbs/bronze/FHLMC/FD`` (the pool factor feed; ``IS`` is
        new-issuance-only and is NOT the paydown panel). WA block from ``FD`` itself.
      * **GNMA** = ``gnma/silver/factorA1`` (Ginnie I) ∪ ``factorA2`` (Ginnie II),
        raw fixed-point, HMBS filtered by ``Pool Type``, WA block left-enriched from
        ``gnma/silver/monthlySFPS`` PS; ``termination_date`` from ``ptermmon`` P.
      * **FNMA**: the per-pool monthly factor feed is NOT currently ingested
        (``FNM_IS`` is issuance-only; ``FNM_DPR_FCTR`` is a cohort aggregate, wrong
        grain). The FNMA side is intentionally skipped in v1 — see the deferred note.

    Because SMM/CPR need cross-month history, the per-file assemblers only map +
    scale; all monthly rows are spilled to a temp dir, then re-scanned to apply the
    §4 windowed derivation before the partitioned write.

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
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    canon = pl.Schema(CANONICAL_MBS_PERFORMANCE_SCHEMA)
    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs")
    gnma = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver"
    fhlmc_fd = umbs / "bronze" / "FHLMC" / "FD"
    gnma_ps = gnma / "monthlySFPS" / "PS"
    gnma_pterm = gnma / "ptermmon" / "P"
    gnma_programs = {"GINNIE_I": gnma / "factorA1" / "ALL", "GINNIE_II": gnma / "factorA2" / "ALL"}

    # Spill all mapped monthly rows to a temp dir (SMM needs cross-month history,
    # so we cannot derive per file). Reindex each chunk to the canonical union so
    # the temp tree scans as one uniform table.
    tmp_dir = out_dir.parent / "_mbs_performance_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    logger.info("Building combined mbs_performance (FHLMC + GNMA) -> %s", out_dir)
    logger.info(
        "  FNMA side skipped: no per-pool monthly factor feed ingested (FNM_IS is "
        "issuance-only; DPR is a cohort aggregate)."
    )

    def _spill(chunk: pl.DataFrame, tag: str) -> int:
        if chunk.is_empty():
            return 0
        _reindex_to_schema(chunk, canon).write_parquet(tmp_dir / f"{tag}.parquet")
        return chunk.height

    total_in = 0

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
            total_in += _spill(chunk, f"gnma_{program.lower()}_{month}")

    # ptermmon reports a pool once, in its termination month, keyed by CUSIP (a
    # numeric ``Pool ID`` in a DIFFERENT namespace from factorA's alphanumeric
    # ``Pool Number`` — verified zero-overlap, so join on CUSIP). A terminated
    # pool is absent from that month's factorA (already gone), so the date is
    # attached globally to every earlier row of the pool via CUSIP.
    pterm_files = sorted(gnma_pterm.glob("ptermmon_*_P.parquet"))
    gnma_terminations = (
        pl.concat(
            [
                pl.scan_parquet(str(pf)).select(
                    pl.col("CUSIP Number").cast(pl.String).str.strip_chars().alias("cusip"),
                    pl.col("Termination Date")
                    .cast(pl.Int64, strict=False)
                    .cast(pl.Int32, strict=False)
                    .alias("termination_date"),
                )
                for pf in pterm_files
            ],
            how="diagonal_relaxed",
        )
        .filter(pl.col("cusip") != "")
        .unique(subset=["cusip"], keep="first")
    )

    logger.info("  mapped %d pool-months; applying §4 derivations...", total_in)

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
        return (
            _apply_mbs_performance_derivations(shard_lf.sort(["agency", "pool_id", "as_of_date"]))
            .with_columns(
                # cpr_1m from smm; cpr_3m from the 3-month rolling-mean SMM (nullable).
                (100.0 * (1.0 - (1.0 - pl.col("smm")) ** 12)).alias("cpr_1m"),
                pl.col("smm")
                .rolling_mean(window_size=3, min_samples=1)
                .over(["agency", "pool_id"], order_by="as_of_date")
                .alias("_smm_bar3"),
            )
            .with_columns(
                (100.0 * (1.0 - (1.0 - pl.col("_smm_bar3")) ** 12)).alias("cpr_3m"),
                # pool_status: TERMINATED at a paid-off row; else ACTIVE. (Absence
                # after the last observed month is not re-materialized in v1.)
                pl.when(
                    (pl.col("rpb_factor") == 0)
                    | (pl.col("current_security_upb") == 0)
                    | (
                        pl.col("current_security_upb").is_null()
                        & pl.col("termination_date").is_not_null()
                    )
                )
                .then(pl.lit("TERMINATED"))
                .otherwise(pl.lit("ACTIVE"))
                .alias("pool_status"),
                (pl.col("as_of_date") // 100).cast(pl.Int32).alias("as_of_year"),
            )
            .drop("_smm_bar3")
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
    logger.info("mbs_performance built: %d pool-months (FHLMC + GNMA) -> %s", total, out_dir)
    return pl.scan_parquet(str(out_dir))


def build_issuer(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the issuer master / cross-source identity crosswalk.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        The combined LazyFrame.

    Raises:
        NotImplementedError: Placeholder.
    """
    raise NotImplementedError("combined.build_issuer is a scaffold.")


def msr_transfers_note() -> str:
    """Explain why MSR transfers is registered here but built in ``analytics``.

    MSR transfer detection was folded in from Jonathan's former standalone
    ``msr_transfers`` project and now lives in
    :func:`analytics.transfers.detect_servicer_transfers` (a within-source
    longitudinal diff over the GNMA/UMBS layers — distinct from any cross-source
    combined master). It is registered in ``config.DATASET_MAP``
    (``external=True``) for cross-reference, but the ``combined`` subpackage
    deliberately does not construct it.

    Returns:
        A short explanatory string (no side effects).
    """
    return (
        "msr_transfers is built by analytics.transfers.detect_servicer_transfers, "
        "not by the combined subpackage; call that instead of building it here."
    )


#: Builder dispatch for the datasets this package actually builds.
BUILDERS = {
    "loan_issuance": build_loan_issuance,
    "loan_performance": build_loan_performance,
    "loan_terminations": build_loan_terminations,
    "mbs_issuance": build_mbs_issuance,
    "mbs_performance": build_mbs_performance,
    "issuer": build_issuer,
}


def build_datasets(datasets: list[str] | None = None, *, overwrite: bool = False) -> list[str]:
    """Build the requested combined master datasets.

    Args:
        datasets: Subset of buildable masters. ``None`` = all buildable
            (excludes the external ``msr_transfers``).
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
