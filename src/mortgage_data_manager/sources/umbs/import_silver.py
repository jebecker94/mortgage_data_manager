"""Import UMBS bronze data to silver layer, applying GSE reissue corrections.

GSEs republish loan-level and security-level disclosures in R-prefixed files
(e.g. FNM_RILLD, FNM_RIS, FRE_RILLD, FRE_RIS) when they need to fix data.
Empirically these reissues are 100% same-month with their originals.

For each (GSE, kind) pair defined in `CORRECTION_PAIRS`, the silver importer
produces ONE monthly file: it takes the original for any key not present in
that month's correction file, and the correction's row otherwise. A synthetic
`_record_source` column ('original' or 'correction') is added so consumers
can identify reissue-sourced rows.

The monthly loan-level performance feeds — FNMA `MLLD` and FHLMC `FU` — have no
correction twin, so they are passed straight through to silver one file per
reporting month (with the same `M(M)YYYY` date parsing), driven by `_PERF_KINDS`
rather than `CORRECTION_PAIRS`.

Skipped: the ISS/RISS pairs. Bronze splits those headerless files into one parquet
per record type, and silver for that layout is not built yet; their
`CORRECTION_PAIRS` entries are marked BLOCKED.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.types import SilverSpec, apply_silver_types, enforce_schema
from mortgage_data_manager.sources.umbs.config import (
    CORRECTION_PAIRS,
    UMBS_SILVER_SPECS,
    CorrectionPair,
    UMBSConfig,
)
from mortgage_data_manager.sources.umbs.prefix_library import acquisition_type_expr

logger = get_logger(__name__)

_MONTH_RE = re.compile(r"_(\d{6})\.parquet$")
_SOURCE_COL = "_record_source"

#: Freddie silver kinds that get the pool-level ``acquisition_type`` (Prefix Library).
_ACQUISITION_TYPE_KINDS: frozenset[tuple[str, str]] = frozenset(
    {("FHLMC", "ILLD"), ("FHLMC", "IS"), ("FHLMC", "FU")}
)


def _with_acquisition_type(lf: pl.LazyFrame, gse: str, kind: str) -> pl.LazyFrame:
    """Add Freddie's pool class for the kinds in `_ACQUISITION_TYPE_KINDS` (else no-op)."""
    if (gse, kind) not in _ACQUISITION_TYPE_KINDS:
        return lf
    return lf.with_columns(acquisition_type_expr())


def _apply_spec(lf: pl.LazyFrame, spec: SilverSpec | None, name: str) -> pl.LazyFrame:
    """Apply a kind's close-to-final dtype spec and assert the schema (no-op if None).

    Parses the encoded date columns to ``pl.Date`` (``M(M)YYYY`` month -> first-of-
    month; ``Issue Date`` is the day-granularity ``mmddyyyy``), right-sizes ints
    (holding the live credit-score/LTV/rate sentinels the matchers detect), casts
    rates to Float32 and money to Float64, enums the closed GSE code sets, and
    conforms to the per-kind target. The look-alike ``Months to Next ... Date``
    columns are month COUNTS and stay integers (declared as such in the spec).

    Returns:
        The frame conformed to the kind's target schema, or unchanged when
        ``spec`` is None.
    """
    if spec is None:
        return lf
    lf = apply_silver_types(lf, spec)
    enforce_schema(lf.collect_schema(), spec.target, name=name)
    return lf


def _month_from_path(path: Path) -> str | None:
    match = _MONTH_RE.search(path.name)
    return match.group(1) if match else None


def _merge_month(
    original_path: Path,
    correction_path: Path | None,
    key: list[str],
    output_path: Path,
    silver_spec: SilverSpec | None = None,
    name: str = "umbs",
    *,
    gse: str = "",
    kind: str = "",
) -> tuple[int, int, int]:
    """Merge one month's original + correction pair into a unified parquet.

    Args:
        original_path: Bronze original monthly parquet.
        correction_path: Bronze correction monthly parquet, or None.
        key: Join key columns for the original/correction merge.
        output_path: Silver output path.
        silver_spec: The kind's close-to-final dtype spec (applied + enforced).
        name: Dataset label for schema-enforcement log messages.
        gse: GSE of the pair (selects the derived columns).
        kind: Canonical kind of the pair (selects the derived columns).

    Returns:
        (rows_from_original, rows_from_correction, total_rows)
    """
    orig = pl.scan_parquet(original_path)

    if correction_path is None:
        unified = orig.with_columns(pl.lit("original").alias(_SOURCE_COL))
        unified = _with_acquisition_type(unified, gse, kind)
        with atomic_output(output_path) as tmp:
            _apply_spec(unified, silver_spec, name).sink_parquet(tmp, compression="zstd")
        total = pl.scan_parquet(output_path).select(pl.len()).collect().item()
        return total, 0, total

    corr = pl.scan_parquet(correction_path)
    corr_keys = corr.select(key).unique()

    from_orig = orig.join(corr_keys, on=key, how="anti").with_columns(
        pl.lit("original").alias(_SOURCE_COL)
    )
    from_corr = corr.with_columns(pl.lit("correction").alias(_SOURCE_COL))

    unified = pl.concat([from_orig, from_corr], how="diagonal_relaxed")
    unified = _with_acquisition_type(unified, gse, kind)
    with atomic_output(output_path) as tmp:
        _apply_spec(unified, silver_spec, name).sink_parquet(tmp, compression="zstd")

    # Recover the original/correction split from the written file — a lazy
    # scan over one column, so it stays cheap even as months grow.
    counts = dict(pl.scan_parquet(output_path).group_by(_SOURCE_COL).len().collect().iter_rows())
    n_orig = int(counts.get("original", 0))
    n_corr = int(counts.get("correction", 0))
    return n_orig, n_corr, n_orig + n_corr


def _process_pair(
    gse: str,
    kind: str,
    spec: CorrectionPair,
    bronze_dir: Path,
    silver_dir: Path,
    overwrite: bool,
) -> dict[str, int]:
    """Process all monthly files for one (GSE, kind) pair.

    Returns:
        Counts: ``months``, ``wrote``, ``skipped_existing``, ``missing_pair`` and
        ``failed`` (months whose merge raised; logged, and nothing published for them).
    """
    empty = {"months": 0, "skipped_existing": 0, "missing_pair": 0, "wrote": 0, "failed": 0}
    if "BLOCKED" in spec["description"]:
        logger.info("  %s/%s: SKIPPED (%s)", gse, kind, spec["description"])
        return empty

    orig_dir = bronze_dir / gse / spec["original"]
    corr_dir = bronze_dir / gse / spec["correction"]
    out_dir = silver_dir / gse / kind
    out_dir.mkdir(parents=True, exist_ok=True)

    if not orig_dir.exists():
        logger.warning("  %s/%s: missing bronze original at %s", gse, kind, orig_dir)
        return empty

    months_processed = 0
    skipped_existing = 0
    missing_pair = 0
    wrote = 0
    failed = 0
    total_o = total_c = 0

    for orig_path in sorted(orig_dir.glob(f"{spec['original']}_*.parquet")):
        month = _month_from_path(orig_path)
        if month is None:
            logger.warning("  %s/%s: could not parse month from %s", gse, kind, orig_path.name)
            continue
        months_processed += 1

        out_path = out_dir / f"{kind}_{month}.parquet"
        if not should_process_file(out_path, overwrite):
            skipped_existing += 1
            continue

        corr_path = corr_dir / f"{spec['correction']}_{month}.parquet"
        if not corr_path.exists():
            missing_pair += 1
            corr_path_arg = None
        else:
            corr_path_arg = corr_path

        try:
            n_o, n_c, _ = _merge_month(
                orig_path,
                corr_path_arg,
                spec["key"],
                out_path,
                UMBS_SILVER_SPECS.get(kind),
                f"umbs/{gse}/{kind}",
                gse=gse,
                kind=kind,
            )
            total_o += n_o
            total_c += n_c
            wrote += 1
        except Exception:
            failed += 1
            logger.exception("  %s/%s/%s: failed", gse, kind, month)

    logger.info(
        "  %s/%s: months=%s wrote=%s failed=%s "
        "skipped_existing=%s no_correction_for_month=%s "
        "rows_from_original=%s rows_from_correction=%s",
        gse,
        kind,
        months_processed,
        wrote,
        failed,
        skipped_existing,
        missing_pair,
        f"{total_o:,}",
        f"{total_c:,}",
    )
    return {
        "months": months_processed,
        "skipped_existing": skipped_existing,
        "missing_pair": missing_pair,
        "wrote": wrote,
        "failed": failed,
    }


#: Monthly loan-level UMBS disclosures with NO correction twin: FNMA ``MLLD`` and
#: FHLMC ``FU`` (the performance feeds). These pass through to silver one file per
#: reporting month with date parsing — no original/correction merge. ``glob`` and
#: ``style`` capture each feed's bronze filename convention (MLLD =
#: ``FNM_MLLD_YYYYMM``; FU = lowercase ``fuYYMMDD``, so its month needs a separate
#: parser).
_PERF_KINDS: tuple[dict[str, str], ...] = (
    {
        "gse": "FNMA",
        "kind": "MLLD",
        "bronze_folder": "FNM_MLLD",
        "glob": "FNM_MLLD_*.parquet",
        "style": "underscore_yyyymm",
    },
    {
        "gse": "FHLMC",
        "kind": "FU",
        "bronze_folder": "FU",
        "glob": "fu*.parquet",
        "style": "fu_yymmdd",
    },
    {
        "gse": "FNMA",
        "kind": "MF",
        "bronze_folder": "FNM_MF",
        "glob": "FNM_MF_*.parquet",
        "style": "underscore_yyyymm",
    },
    {
        "gse": "FNMA",
        "kind": "GN_MEGA",
        "bronze_folder": "FNM_GN_MEGA",
        "glob": "FNM_GN_MEGA_*.parquet",
        "style": "underscore_yyyymm",
    },
)


def _perf_month_from_path(name: str, style: str) -> str | None:
    """Resolve a perf-feed bronze filename to its reporting month ``YYYYMM``."""
    if style == "underscore_yyyymm":
        match = _MONTH_RE.search(name)
        return match.group(1) if match else None
    if style == "fu_yymmdd":  # fuYYMMDD.parquet -> 20YY + MM (the DD is the disclosure day)
        digits = name.removeprefix("fu").removesuffix(".parquet")
        return f"20{digits[:4]}" if len(digits) >= 4 and digits[:4].isdigit() else None
    return None


def _process_perf_kind(
    spec: dict[str, str], bronze_dir: Path, silver_dir: Path, overwrite: bool
) -> dict[str, int]:
    """Pass one monthly (no-correction) perf feed through to silver with date parsing.

    Returns:
        Counts: ``months``, ``wrote``, ``skipped_existing`` and ``failed`` (months whose
        write raised; logged, and nothing published for them).
    """
    gse, kind = spec["gse"], spec["kind"]
    src_dir = bronze_dir / gse / spec["bronze_folder"]
    out_dir = silver_dir / gse / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    if not src_dir.exists():
        logger.warning("  %s/%s: missing bronze at %s", gse, kind, src_dir)
        return {"months": 0, "skipped_existing": 0, "wrote": 0, "failed": 0}

    silver_spec = UMBS_SILVER_SPECS.get(kind)
    months = skipped = wrote = failed = 0
    for src in sorted(src_dir.glob(spec["glob"])):
        month = _perf_month_from_path(src.name, spec["style"])
        if month is None:
            logger.warning("  %s/%s: could not parse month from %s", gse, kind, src.name)
            continue
        months += 1
        out_path = out_dir / f"{kind}_{month}.parquet"
        if not should_process_file(out_path, overwrite):
            skipped += 1
            continue
        try:
            lf = pl.scan_parquet(src).with_columns(pl.lit("original").alias(_SOURCE_COL))
            lf = _with_acquisition_type(lf, gse, kind)
            with atomic_output(out_path) as tmp:
                _apply_spec(lf, silver_spec, f"umbs/{gse}/{kind}").sink_parquet(
                    tmp, compression="zstd"
                )
            wrote += 1
        except Exception:
            failed += 1
            logger.exception("  %s/%s/%s: failed", gse, kind, month)
    logger.info(
        "  %s/%s: months=%s wrote=%s failed=%s skipped_existing=%s",
        gse,
        kind,
        months,
        wrote,
        failed,
        skipped,
    )
    return {"months": months, "skipped_existing": skipped, "wrote": wrote, "failed": failed}


def run_silver_import(
    bronze_dir: Path,
    silver_dir: Path,
    *,
    overwrite: bool = False,
    gse: str | None = None,
    kinds: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Build silver datasets by merging each (original, correction) pair.

    Args:
        bronze_dir: Root bronze directory containing per-GSE folders.
        silver_dir: Root silver output directory.
        overwrite: If True, overwrite existing silver files; if False, skip them.
        gse: Optional GSE filter ('FNMA' or 'FHLMC').
        kinds: Optional list of canonical kind names (e.g. ['ILLD', 'IS']).

    Returns:
        Per-pair counts keyed by 'GSE/kind'. Every entry carries ``wrote``,
        ``skipped_existing`` and ``failed``; correction pairs also carry
        ``missing_pair``.
    """
    if not bronze_dir.exists():
        raise FileNotFoundError(f"Bronze directory does not exist: {bronze_dir}")

    silver_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "UMBS silver import: bronze=%s  silver=%s  overwrite=%s", bronze_dir, silver_dir, overwrite
    )

    results: dict[str, dict[str, int]] = {}
    kind_filter = set(kinds) if kinds else None

    for g, pairs in CORRECTION_PAIRS.items():
        if gse and g != gse:
            continue
        logger.info("Processing %s", g)
        for kind, spec in pairs.items():
            if kind_filter and kind not in kind_filter:
                continue
            results[f"{g}/{kind}"] = _process_pair(g, kind, spec, bronze_dir, silver_dir, overwrite)

    # Monthly performance feeds (MLLD/FU): no correction twin, passthrough + dates.
    for perf in _PERF_KINDS:
        if gse and perf["gse"] != gse:
            continue
        if kind_filter and perf["kind"] not in kind_filter:
            continue
        logger.info("Processing %s/%s (monthly perf, no correction)", perf["gse"], perf["kind"])
        results[f"{perf['gse']}/{perf['kind']}"] = _process_perf_kind(
            perf, bronze_dir, silver_dir, overwrite
        )
    return results


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")
    run_silver_import(
        bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
        silver_dir=UMBSConfig.UMBS_SILVER_DIR,
        overwrite=False,
    )
