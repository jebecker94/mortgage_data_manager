"""Lazy loaders over GNMA single-family loan-level silver, across the 2026 feed switch.

Ginnie replaced its fixed-width loan-level files (layout v1.8) with pipe-delimited ones
(layout v2.0): the portfolio files ``llmon1`` (Ginnie I) and ``llmon2`` (Ginnie II) became
one ``llmon`` file from the 2026-07 reporting month, and the new-issuance file
``dailyllmni`` became ``dailyllmnipd`` from the 2026-08 issuance month. The old files
continue through 2026-08 but are not read from the cutover month on.

Both eras come out under the same column ids and dtypes, and since the v1.8 decoder writes
the implied decimal exactly, numbers agree bit for bit across the cutover. The one exception
is ``loan_gross_margin``, a String kept as published: thousandths without a point in v1.8
(``"2000"``), a decimal in v2.0 (``"2.0"``). The v1.8 ``L`` record is joined
to its pool's ``P`` record, which carries what v2.0 folds into every loan row
(``cusip_number``, ``issue_type``, ``pool_type``, ``pool_issue_date``). Fields only v2.0
publishes are null before the cutover. ``record_type`` is not returned.

Values are as published. In particular, from the 2026-07 portfolio ``loan_age_in_months``
is one month higher and ``remaining_loan_term_in_months`` one month lower than the v1.8
calculation gave the same loans (measured on all 12,423,780 loans of the 2026-07 overlap
month); these loaders do not adjust either.

A month from the cutover on is read only from the v2.0 feed. If the retired feed has such a
month and the v2.0 silver does not, the loaders raise instead of silently dropping it (build
the v2.0 silver first).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.reference.gnma_pool_types import GNMA_PROGRAM_BY_ISSUE_TYPE

from .canonical import _ARROW_TO_POLARS, CanonicalSchema
from .config import GNMAConfig

logger = get_logger(__name__)

__all__ = [
    "LOAN_KEY",
    "NEW_ISSUANCE_CUTOVER",
    "POOL_FIELDS",
    "PORTFOLIO_CUTOVER",
    "loan_level_schema",
    "loan_months",
    "one_row_per_loan",
    "scan_latest_issuer_names",
    "scan_new_issuance",
    "scan_portfolio",
]

#: First new-issuance month read from ``dailyllmnipd``; earlier months come from ``dailyllmni``.
NEW_ISSUANCE_CUTOVER = "202608"
#: First portfolio month read from ``llmon``; earlier months come from ``llmon1``/``llmon2``.
PORTFOLIO_CUTOVER = "202607"
#: A loan's key in a month: the Disclosure Sequence Number is unique only within a pool.
LOAN_KEY: tuple[str, str] = ("pool_id", "disclosure_sequence_number")
#: Pool-record fields the v1.8 files carry on ``P`` and v2.0 on every loan row.
POOL_FIELDS: tuple[str, ...] = ("cusip_number", "issue_type", "pool_type", "pool_issue_date")

#: v1.8 portfolio file -> Ginnie program; v2.0 derives it from ``issue_type``. In both
#: overlap months (2026-07, 2026-08) issue type X held exactly the ``llmon1`` loans and
#: C/M exactly the ``llmon2`` loans.
_PROGRAM_BY_FILE: dict[str, str] = {"llmon1": "ginnie_i", "llmon2": "ginnie_ii"}

#: Per feed: the v1.8 ``(prefix, record)`` files read before the cutover, the v2.0 file
#: read from it on, and the cutover month.
_FEEDS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, str], str]] = {
    "portfolio": ((("llmon1", "L"), ("llmon2", "L")), ("llmon", "LL"), PORTFOLIO_CUTOVER),
    "new_issuance": ((("dailyllmni", "L"),), ("dailyllmnipd", "LL"), NEW_ISSUANCE_CUTOVER),
}


def loan_level_schema(prefix: str = "llmon") -> pl.Schema:
    """The loan-level columns both eras are returned under, in v2.0 layout order.

    Args:
        prefix: The v2.0 prefix whose ``LL`` record defines the columns
            (``llmon`` or ``dailyllmnipd``; their layouts are the same).

    Returns:
        Column id -> polars dtype, without ``record_type``.
    """
    schema = CanonicalSchema.load(prefix, GNMAConfig.GNMA_CANONICAL_DIR)
    fields = schema.record_types["LL"]["fields"]
    return pl.Schema(
        {f["id"]: _ARROW_TO_POLARS[f["arrow_type"]] for f in fields if f["id"] != "record_type"}
    )


def _month_files(directory: Path, prefix: str, rt: str) -> dict[str, Path]:
    """``YYYYMM`` -> silver file for one prefix and record type."""
    out: dict[str, Path] = {}
    for f in directory.glob(f"{prefix}_*_{rt}.parquet"):
        month = f.stem[len(prefix) + 1 :].split("_")[0]
        if len(month) == 6 and month.isdigit():
            out[month] = f
    return out


def _in_range(month: str, min_month: str | None, max_month: str | None) -> bool:
    """Whether a ``YYYYMM`` month lies in the requested inclusive range."""
    return (min_month is None or month >= min_month) and (max_month is None or month <= max_month)


def _conform(lf: pl.LazyFrame, target: pl.Schema) -> pl.LazyFrame:
    """Select ``target``'s columns in order; a column the file lacks is a typed null."""
    present = lf.collect_schema()
    return lf.select(
        [
            pl.col(c).cast(t) if c in present else pl.lit(None, dtype=t).alias(c)
            for c, t in target.items()
        ]
    )


def _v18_month(silver_dir: Path, prefix: str, month: str, target: pl.Schema) -> pl.LazyFrame:
    """One v1.8 month: ``L`` joined to its pool's ``P`` record.

    A pool whose ``P`` rows disagree gets no pool fields rather than a picked row.
    """
    loans = pl.scan_parquet(silver_dir / prefix / "L" / f"{prefix}_{month}_L.parquet")
    p_file = silver_dir / prefix / "P" / f"{prefix}_{month}_P.parquet"
    loan_cols = [c for c in loans.collect_schema().names() if c not in POOL_FIELDS]
    loans = loans.select(loan_cols)
    if p_file.exists():
        pools = (
            pl.scan_parquet(p_file)
            .select("pool_id", *POOL_FIELDS)
            .unique()
            .filter(pl.len().over("pool_id") == 1)  # tie-safe: a pool with 2+ P rows is dropped
        )
        loans = loans.join(pools, on="pool_id", how="left")
    else:
        logger.warning("No P record file for %s %s; pool fields are null", prefix, month)
    return _conform(loans, target)


def _v20_month(silver_dir: Path, prefix: str, month: str, target: pl.Schema) -> pl.LazyFrame:
    """One v2.0 month: the ``LL`` record."""
    return _conform(
        pl.scan_parquet(silver_dir / prefix / "LL" / f"{prefix}_{month}_LL.parquet"), target
    )


def _served_months(
    feed: str, root: Path, min_month: str | None, max_month: str | None
) -> tuple[dict[str, list[str]], list[str]]:
    """Months each file serves: per v1.8 prefix before the cutover, then v2.0 from it on.

    Returns:
        v1.8 prefix -> its months before the cutover, and the v2.0 months from it on.

    Raises:
        FileNotFoundError: If a requested month from the cutover on exists only in the
            retired v1.8 files.
    """
    old_files, (new_prefix, new_rt), cutover = _FEEDS[feed]
    old = {
        prefix: sorted(
            m
            for m in _month_files(root / prefix / rt, prefix, rt)
            if _in_range(m, min_month, max_month)
        )
        for prefix, rt in old_files
    }
    new = sorted(
        m
        for m in _month_files(root / new_prefix / new_rt, new_prefix, new_rt)
        if m >= cutover and _in_range(m, min_month, max_month)
    )
    missing = sorted({m for months in old.values() for m in months if m >= cutover} - set(new))
    if missing:
        raise FileNotFoundError(
            f"GNMA {feed}: {', '.join(missing)} exist only in the retired "
            f"{'/'.join(old)} silver; from {cutover} the loaders read {new_prefix} — "
            f"build {new_prefix} silver for those months first"
        )
    return {prefix: [m for m in months if m < cutover] for prefix, months in old.items()}, new


def loan_months(
    feed: Literal["portfolio", "new_issuance"],
    *,
    min_month: str | None = None,
    max_month: str | None = None,
    silver_dir: str | Path | None = None,
) -> list[str]:
    """The ``YYYYMM`` months `scan_portfolio` or `scan_new_issuance` serves, in order.

    Args:
        feed: ``portfolio`` (``llmon1``/``llmon2``, then ``llmon``) or ``new_issuance``
            (``dailyllmni``, then ``dailyllmnipd``).
        min_month: First month, ``YYYYMM``; None for the earliest.
        max_month: Last month, ``YYYYMM``; None for the latest.
        silver_dir: GNMA silver root; defaults to ``GNMAConfig.GNMA_SILVER_DIR``.

    Returns:
        Sorted, distinct months.

    Raises:
        FileNotFoundError: If a month from the cutover on exists only in the retired files.
    """
    root = Path(silver_dir) if silver_dir is not None else GNMAConfig.GNMA_SILVER_DIR
    old, new = _served_months(feed, root, min_month, max_month)
    return sorted({m for months in old.values() for m in months} | set(new))


def _concat(frames: list[pl.LazyFrame], what: str) -> pl.LazyFrame:
    """Stack conformed month frames; raise if nothing was found."""
    if not frames:
        raise FileNotFoundError(f"No GNMA {what} silver files in the requested months")
    return pl.concat(frames, how="vertical")


def scan_new_issuance(
    *,
    min_month: str | None = None,
    max_month: str | None = None,
    loan_purposes: Sequence[str] | None = None,
    silver_dir: str | Path | None = None,
) -> pl.LazyFrame:
    """Scan single-family loans at issuance, one row per file row, across both feeds.

    Months before `NEW_ISSUANCE_CUTOVER` come from ``dailyllmni`` (``L`` joined to
    ``P``), later ones from ``dailyllmnipd`` (``LL``). A month is the file's issuance
    month. Rows are not deduplicated: a loan republished in a later month appears again
    (see `one_row_per_loan`).

    Args:
        min_month: First issuance month to read, ``YYYYMM``; None for the earliest.
        max_month: Last issuance month to read, ``YYYYMM``; None for the latest.
        loan_purposes: Keep only these ``loan_purpose`` codes (e.g. ``("1", "2")`` for
            purchases and refinances, dropping the 3/4 modifications and 5 re-performing
            loans); None keeps all.
        silver_dir: GNMA silver root; defaults to ``GNMAConfig.GNMA_SILVER_DIR``.

    Returns:
        Loan rows under `loan_level_schema` column ids.

    Raises:
        FileNotFoundError: If no file falls in the requested months, or a month from the
            cutover on exists only in the retired v1.8 files.

    Example:
        >>> lf = scan_new_issuance(min_month="201801", loan_purposes=("1", "2"))
        >>> lf.select("disclosure_sequence_number", "agency").collect()
    """
    root = Path(silver_dir) if silver_dir is not None else GNMAConfig.GNMA_SILVER_DIR
    target = loan_level_schema("dailyllmnipd")
    old, new = _served_months("new_issuance", root, min_month, max_month)
    frames = [_v18_month(root, "dailyllmni", m, target) for m in old["dailyllmni"]] + [
        _v20_month(root, "dailyllmnipd", m, target) for m in new
    ]
    lf = _concat(frames, "new-issuance")
    if loan_purposes is not None:
        lf = lf.filter(pl.col("loan_purpose").is_in(list(loan_purposes)))
    return lf


def scan_portfolio(
    *,
    min_month: str | None = None,
    max_month: str | None = None,
    silver_dir: str | Path | None = None,
) -> pl.LazyFrame:
    """Scan the single-family loan-level portfolio, loan x reporting month, across both feeds.

    Months before `PORTFOLIO_CUTOVER` stack ``llmon1`` and ``llmon2`` (``L`` joined to
    ``P``); later months read ``llmon`` (``LL``). A ``program`` column (``ginnie_i`` /
    ``ginnie_ii``) comes from the file before the cutover and from ``issue_type`` after
    it. Loan age and remaining term are as published (see the module docstring).

    Args:
        min_month: First reporting month to read, ``YYYYMM``; None for the earliest.
        max_month: Last reporting month to read, ``YYYYMM``; None for the latest.
        silver_dir: GNMA silver root; defaults to ``GNMAConfig.GNMA_SILVER_DIR``.

    Returns:
        Loan-month rows under `loan_level_schema` column ids plus ``program``.

    Raises:
        FileNotFoundError: If no file falls in the requested months, or a month from the
            cutover on exists only in the retired v1.8 files.

    Example:
        >>> lf = scan_portfolio(min_month="202601")
        >>> lf.group_by("program").agg(pl.col("unpaid_principal_balance").sum()).collect()
    """
    root = Path(silver_dir) if silver_dir is not None else GNMAConfig.GNMA_SILVER_DIR
    target = loan_level_schema("llmon")
    old, new = _served_months("portfolio", root, min_month, max_month)
    frames: list[pl.LazyFrame] = []
    for prefix, program in _PROGRAM_BY_FILE.items():
        frames += [
            _v18_month(root, prefix, m, target).with_columns(program=pl.lit(program))
            for m in old[prefix]
        ]
    by_issue_type = pl.col("issue_type").replace_strict(
        GNMA_PROGRAM_BY_ISSUE_TYPE, default=None, return_dtype=pl.String
    )
    frames += [
        _v20_month(root, "llmon", m, target).with_columns(program=by_issue_type) for m in new
    ]
    return _concat(frames, "portfolio")


def one_row_per_loan(lf: pl.LazyFrame, key: str | Sequence[str]) -> pl.LazyFrame:
    """Keep one row per loan: identical rows collapse, a loan whose rows disagree is dropped.

    Apply it after projecting to the columns that matter, so rows that differ only in
    columns you do not use still collapse. Never picks among disagreeing rows.

    Args:
        lf: Rows to reduce, already projected.
        key: Column(s) identifying a loan (e.g. ``"disclosure_sequence_number"``).

    Returns:
        One row per key value, for keys whose rows all agree.
    """
    keys = [key] if isinstance(key, str) else list(key)
    return lf.unique().filter(pl.len().over(keys) == 1)  # tie-safe: disagreeing keys dropped


def scan_latest_issuer_names(silver_dir: str | Path | None = None) -> pl.LazyFrame:
    """The issuer name on each GNMA issuer's latest ``issrinfo`` record.

    Issuer ids and names are whitespace-stripped. An issuer whose latest record date
    carries two different names has no single latest name and is left out.

    Args:
        silver_dir: GNMA silver root; defaults to ``GNMAConfig.GNMA_SILVER_DIR``.

    Returns:
        ``issuer_id`` (String) and ``issuer_name``, one row per issuer.
    """
    root = Path(silver_dir) if silver_dir is not None else GNMAConfig.GNMA_SILVER_DIR
    return (
        pl.scan_parquet(root / "issrinfo" / "**" / "*.parquet")
        .select(
            pl.col("issuer_id").cast(pl.Utf8).str.strip_chars(),
            pl.col("issuer_name").str.strip_chars(),
            "record_as_of_date",
        )
        .filter(pl.col("issuer_id").is_not_null() & pl.col("issuer_name").is_not_null())
        .filter(pl.col("record_as_of_date") == pl.col("record_as_of_date").max().over("issuer_id"))
        .select("issuer_id", "issuer_name")
        .unique()
        .filter(pl.len().over("issuer_id") == 1)  # tie-safe: two latest names -> dropped
    )
