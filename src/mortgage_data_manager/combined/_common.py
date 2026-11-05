"""Helpers shared by the combined master builders.

Sentinel bounds and scrubs, schema conforming, the atomic dataset build, partitioned
writes, the plan-table select, the mbs_umbs crosswalk reader and the GNMA silver readers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NamedTuple

import polars as pl

from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.core.harmonized import credit_score, flag_to_bool
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import (
    atomic_output_dir,
    should_process_file,
)
from mortgage_data_manager.sources.gnma.load import loan_months

logger = get_logger(__name__)


# Underwriting-ratio sentinels (FNMA: 999 = NA; DTI also -99999 = Exempt).
_RATIO_SENTINELS = (999, -99999)


# Perf-panel sentinel/junk bounds (2026-07-09 schema audit). ELTV 999 = "not available"
# (42M FHLMC rows); 99.999 = the FU frontier-month rate placeholder (re-injected each
# incremental month until the real rate discloses); loan_age -1..-3 is the benign SF
# pre-first-payment convention but a FHLMC junk cluster sits at -859..-965; RMM junk
# reaches -840 (FNMA) and 967 (GNMA) against a 480-month product ceiling.
_LTV_SENTINEL_FLOOR = 900


_RATE_SENTINEL = 99.999


_MI_PCT_SENTINEL = 999


_LOAN_AGE_FLOOR = -24


_COUNT_SENTINEL = 99  # number of borrowers / units: 99 = not available


_ARM_CAP_SENTINEL_FLOOR = 70  # FNMA fills 77.777 into the initial cap-down field


_GOV_MIP_RATE_CEILING = 10  # Ginnie MIP percents above 10 are typed in the wrong units


_RMM_LO, _RMM_HI = -120, 720


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


def _build_dataset(name: str, write: Callable[[Path], None], *, overwrite: bool) -> pl.LazyFrame:
    """Build one master unless present: write a temp tree, then swap it in whole.

    A killed build leaves the previous tree in place, never a partial one that the
    existence check would skip forever after.

    Args:
        name: The dataset (``CombinedConfig.dataset_path`` key).
        write: Writes the full tree into the directory it is given.
        overwrite: Rebuild when the dataset already exists.

    Returns:
        A LazyFrame scanning the dataset.
    """
    out_dir = CombinedConfig.dataset_path(name)
    if should_process_file(out_dir, overwrite=overwrite):
        CombinedConfig.ensure_directories()
        with atomic_output_dir(out_dir) as tmp:
            write(tmp)
    else:
        logger.info("%s already built at %s; skipping (use overwrite).", name, out_dir)
    return pl.scan_parquet(str(out_dir))


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


def _flag_str(expr: pl.Expr) -> pl.Expr:
    """Normalize a flag column to String, mapping Boolean true/false back to Y/N.

    For flags whose code space is richer than yes/no (e.g. FHLMC ``Modification Flag``
    carries ``P`` = modified in a prior period), where Booleanizing would lose codes:
    FNMA's Boolean silver becomes Y/N and richer letter spaces pass through unchanged.

    Returns:
        A String expression carrying the flag's own code space.
    """
    return expr.cast(pl.String, strict=False).replace({"true": "Y", "false": "N"})


def _reindex_to_schema[F: (pl.DataFrame, pl.LazyFrame)](frame: F, schema: pl.Schema) -> F:
    """Conform a chunk to a canonical schema so all written files are identical.

    Per-file chunks carry only their vintage's columns; this adds any missing columns
    as typed nulls, casts present ones to the canonical dtype, and orders them, so a
    single ``scan_parquet`` over the whole dataset succeeds. A LazyFrame stays lazy, so
    a streaming sink never materializes the chunk.

    Args:
        frame: The assembled chunk, eager or lazy.
        schema: Canonical output schema (the cross-vintage union).

    Returns:
        The chunk with exactly ``schema``'s columns, dtypes, and order.
    """
    have = set(frame.collect_schema().names())
    return frame.select(
        [
            pl.col(name).cast(dtype, strict=False)
            if name in have
            else pl.lit(None, dtype=dtype).alias(name)
            for name, dtype in schema.items()
        ]
    )


def _union_schema(schemas: list[pl.Schema]) -> pl.Schema:
    """The column union of several output schemas, in first-seen order, dtypes supertyped."""
    return pl.concat(
        [pl.DataFrame(schema=s).lazy() for s in schemas], how="diagonal_relaxed"
    ).collect_schema()


def _write_partitioned(
    df: pl.DataFrame, canon: pl.Schema, out_dir: Path, key: str, agency: str, tag: str
) -> int:
    """Conform a chunk to ``canon`` and write it into the ``agency=/{key}=`` hive layout.

    Rows with a null ``key`` have no partition directory and are dropped, with a warning.

    Args:
        df: The assembled chunk (carries ``agency`` and ``key``).
        canon: The dataset's canonical schema.
        out_dir: Dataset root.
        key: The year partition column.
        agency: Agency partition value.
        tag: File stem, unique per agency and source file.

    Returns:
        Rows written.
    """
    df = _reindex_to_schema(df, canon)
    no_key = df.filter(pl.col(key).is_null()).height
    if no_key:
        logger.warning("  %s/%s: dropped %d rows with a null %s", agency, tag, no_key, key)
        df = df.filter(pl.col(key).is_not_null())
    for (year,), part in df.partition_by(key, as_dict=True).items():
        part_dir = out_dir / f"agency={agency}" / f"{key}={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part.drop("agency", key).write_parquet(part_dir / f"{tag}.parquet")
    return df.height


class LegField(NamedTuple):
    """One output column of a single-source leg: the reader that builds it and its sources.

    ``read`` names an entry in the leg's reader table, which turns ``src`` (source column
    aliases, the first present one winning) into an expression; with no ``src`` a reader
    yields its typed null. Two names are reserved: ``const`` emits ``value``, and
    ``derived`` takes an expression the assembler builds in code under ``name``.
    """

    name: str
    read: str
    src: tuple[str, ...] = ()
    value: str | float | bool | None = None


#: A leg's readers: reader name -> builder from source aliases to an expression.
Readers = Mapping[str, Callable[[tuple[str, ...]], pl.Expr]]


def _plan_exprs(
    plan: list[LegField], readers: Readers, derived: Mapping[str, pl.Expr] | None = None
) -> list[pl.Expr]:
    """The select list of a leg plan, in plan order.

    Args:
        plan: The leg's output columns.
        readers: The leg's reader table.
        derived: The expression for every ``derived`` column, by name.

    Returns:
        One aliased expression per plan entry.

    Raises:
        ValueError: If a ``derived`` column has no expression or an expression has no
            ``derived`` column.
    """
    pending = dict(derived or {})
    exprs = []
    for f in plan:
        if f.read == "derived":
            if f.name not in pending:
                raise ValueError(f"plan column {f.name!r} is derived but has no expression")
            expr = pending.pop(f.name)
        elif f.read == "const":
            expr = pl.lit(f.value)
        else:
            expr = readers[f.read](f.src)
        exprs.append(expr.alias(f.name))
    if pending:
        raise ValueError(f"derived expressions with no plan column: {sorted(pending)}")
    return exprs


def _gse_crosswalk(path: Path) -> pl.LazyFrame:
    """Scan a GSE mbs_umbs crosswalk as ``sf_id`` ↔ ``umbs_id`` (both string, 1:1)."""
    return pl.scan_parquet(path).select(
        pl.col("Loan Sequence Number (MBS)").cast(pl.String).alias("sf_id"),
        pl.col("Loan Sequence Number (UMBS)").cast(pl.String).alias("umbs_id"),
    )


def _g_raw(name: str, schema: pl.Schema) -> pl.Expr | None:
    """A GNMA loan-level field as a trimmed string, or None when this vintage lacks it.

    Late-added fields (origination date V1.6+, seller issuer id V1.6+, index type
    V1.7+) are missing from early files; callers null-fill via the ``_opt_*`` and
    decode helpers, which accept ``None``. A typed date is rendered back to the
    ``YYYYMMDD`` string the date helper parses.

    Returns:
        A trimmed-string expression, or None when the field is absent this vintage.
    """
    if name not in schema:
        return None
    if schema[name] == pl.Date:
        return pl.col(name).dt.strftime("%Y%m%d")
    return pl.col(name).cast(pl.String).str.strip_chars()


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
# The helpers below take those already-decoded values and only map 0/blank -> null (no
# scaling).
def _gnma_num(expr: pl.Expr | None, *, keep_zero: bool = False) -> pl.Expr:
    """A rate or amount from canonical silver; blank -> null, and 0 -> null unless ``keep_zero``.

    Zero is Ginnie's blank for a rate or an original amount, but a real value for a
    current balance: a paid-off pool's last row reports 0.

    Returns:
        A Float64 expression.
    """
    if expr is None:
        return pl.lit(None, dtype=pl.Float64)
    v = expr.cast(pl.Float64, strict=False)
    return pl.when((v >= 0) if keep_zero else (v > 0)).then(v).otherwise(None)


def _plan_src(plan: list[LegField], name: str) -> tuple[str, ...]:
    """The source columns a plan reads for output column ``name``."""
    return next(f.src for f in plan if f.name == name)


def _gnma_loan_readers(schema: pl.Schema) -> Readers:
    """The reader table the GNMA loan-level plans name, over one silver file's schema.

    Every reader takes its value through `_g_raw` (a trimmed string, None when this
    vintage lacks the field), so the decode helpers see one representation.

    Args:
        schema: The silver file's schema.

    Returns:
        Reader name -> builder from source aliases to an expression.
    """

    def g(src: tuple[str, ...]) -> pl.Expr | None:
        return next((e for s in src if (e := _g_raw(s, schema)) is not None), None)

    def flag_y(src: tuple[str, ...]) -> pl.Expr:
        e = g(src)
        return (e == "Y").fill_null(value=False) if e is not None else pl.lit(value=False)

    def raw(src: tuple[str, ...]) -> pl.Expr:
        e = g(src)
        return e if e is not None else pl.lit(None, dtype=pl.String)

    return {
        "raw": raw,
        "str": lambda src: _opt_str(g(src)),
        "int": lambda src: _opt_int(g(src)),
        "gnma_num": lambda src: _gnma_num(g(src)),
        "yn": lambda src: flag_to_bool(_opt_str(g(src))),
        "fico": lambda src: credit_score(_opt_str(g(src))),
        "flag_y": flag_y,
    }


def _gnma_program(expr: pl.Expr) -> pl.Expr:
    """Map P-record Issue Type to Ginnie I / Ginnie II."""
    e = expr.cast(pl.String).str.strip_chars()
    return (
        pl.when(e == "X")
        .then(pl.lit("ginnie_i"))
        .when(e.is_in(["C", "M"]))
        .then(pl.lit("ginnie_ii"))
        .otherwise(None)
    )


def _gnma_loan_months(feed: Literal["portfolio", "new_issuance"]) -> list[str]:
    """The ``YYYYMM`` months `scan_portfolio` / `scan_new_issuance` serve, in order.

    The builders read the loan-level feeds one month at a time, so peak memory stays one
    month wide. Raises if a month past the feed's cutover exists only in the retired files
    (see `sources.gnma.load.loan_months`).

    Args:
        feed: ``portfolio`` (``llmon1``/``llmon2``, then ``llmon``) or ``new_issuance``
            (``dailyllmni``, then ``dailyllmnipd``).

    Returns:
        Sorted, distinct months.
    """
    return loan_months(feed)


def _period_index(period: pl.Expr) -> pl.Expr:
    """A YYYYMM int period as a running month count, so differences are whole months."""
    return period // 100 * 12 + period % 100


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
