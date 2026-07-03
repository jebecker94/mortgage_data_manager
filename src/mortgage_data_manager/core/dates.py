"""Shared parsers for the fixed-point/packed date encodings used across sources.

GSE and GNMA disclosures store dates as zero-suppressed integers or digit
strings rather than ISO dates — ``M(M)YYYY`` (month-granularity), ``MMDDYYYY``
(GSE security issue dates), and ``YYYYMMDD`` (GNMA full dates). These helpers
turn an encoded column into a proper :class:`polars.Date` (month-only encodings
land on the first of the month). Each is robust to int-or-string inputs,
leading-zero loss (``92025`` == ``092025``), and zero/blank sentinels (which
parse to null under ``strict=False``).
"""

from __future__ import annotations

import polars as pl


def month_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse an ``M(M)YYYY`` / ``MMYYYY`` month encoding to a first-of-month ``pl.Date``.

    Handles both the integer form (``62026`` -> 2026-06-01, month not zero-padded)
    and the string form (``"082025"`` -> 2025-08-01). Casting through ``Int64``
    first normalizes leading-zero drift before re-padding to ``MMYYYY``.

    Args:
        expr: The encoded month column (Int or String).

    Returns:
        A ``pl.Date`` expression on the first of the month (null on sentinels).
    """
    return (
        expr.cast(pl.Int64, strict=False)
        .cast(pl.String)
        .str.zfill(6)
        .str.strptime(pl.Date, "%m%Y", strict=False)
    )


def mmddyyyy_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse an ``MMDDYYYY`` encoding to ``pl.Date`` (GSE security ``Issue Date``).

    The month is not zero-padded (``5012026`` -> 2026-05-01), so the value is
    normalized through ``Int64`` and re-padded to eight digits before parsing.

    Args:
        expr: The encoded ``MMDDYYYY`` column (Int or String).

    Returns:
        A ``pl.Date`` expression (null on sentinels).
    """
    return (
        expr.cast(pl.Int64, strict=False)
        .cast(pl.String)
        .str.zfill(8)
        .str.strptime(pl.Date, "%m%d%Y", strict=False)
    )


def yyyymmdd_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse a ``YYYYMMDD`` encoding to ``pl.Date`` (GNMA full dates).

    Args:
        expr: The encoded ``YYYYMMDD`` column (Int or String, possibly padded
            with whitespace).

    Returns:
        A ``pl.Date`` expression (null on blank/``00000000`` sentinels).
    """
    return (
        expr.cast(pl.Int64, strict=False)
        .cast(pl.String)
        .str.zfill(8)
        .str.strptime(pl.Date, "%Y%m%d", strict=False)
    )


def ccyymm_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse a ``CCYYMM`` (year-first ``YYYYMM``) month encoding to a first-of-month ``pl.Date``.

    Distinct from :func:`month_to_date`, which expects month-first ``MMYYYY`` —
    here the year leads (GNMA As-Of / report-period columns, ``"202512"`` ->
    2025-12-01).

    Args:
        expr: The encoded ``CCYYMM`` column (Int or String).

    Returns:
        A ``pl.Date`` expression on the first of the month (null on sentinels).
    """
    return (
        expr.cast(pl.Int64, strict=False)
        .cast(pl.String)
        .str.zfill(6)
        .str.strptime(pl.Date, "%Y%m", strict=False)
    )


def mmddyy_to_date(expr: pl.Expr) -> pl.Expr:
    """Parse an ``MMDDYY`` (two-digit year) encoding to ``pl.Date`` (GNMA factor files).

    The two-digit year is resolved by the standard strptime pivot. The value is
    normalized through ``Int64`` and re-padded to six digits before parsing.

    Args:
        expr: The encoded ``MMDDYY`` column (Int or String).

    Returns:
        A ``pl.Date`` expression (null on sentinels).
    """
    return (
        expr.cast(pl.Int64, strict=False)
        .cast(pl.String)
        .str.zfill(6)
        .str.strptime(pl.Date, "%m%d%y", strict=False)
    )


def to_date_from_datetime(expr: pl.Expr) -> pl.Expr:
    """Drop the (always-midnight) time component of a ``Datetime`` column to ``pl.Date``.

    For synthesized or upstream ``Datetime(us/ns)`` columns whose time is always
    ``00:00:00`` — casting removes the false time precision and saves 4 bytes/row.

    Args:
        expr: A ``Datetime`` column expression.

    Returns:
        A ``pl.Date`` expression.
    """
    return expr.cast(pl.Date)
