#!/usr/bin/env python3
"""Loading and analysis helpers for GNMA loan-level performance data.

Reads the single-family portfolio through `scan_portfolio`, so a series runs across the
2026 switch from ``llmon1``/``llmon2`` to ``llmon`` without a seam in the file layout.
Loan age and remaining term are as published; see `mortgage_data_manager.sources.gnma.load`.

Public API:
    - load_performance_data: Lazy loan x month portfolio rows for a month range
    - get_summary_statistics: Loan, UPB, pool and issuer counts
    - create_upb_time_series: UPB by month at total, pool, issuer or loan level
    - aggregate_by_pool: Aggregate UPB by pool and month
    - aggregate_by_issuer: Aggregate UPB by issuer and month
    - aggregate_total: Aggregate total UPB by month

"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.scalars import scalar_float

from .load import scan_portfolio

logger = get_logger(__name__)

__all__ = [
    "load_performance_data",
    "get_summary_statistics",
    "create_upb_time_series",
    "aggregate_by_pool",
    "aggregate_by_issuer",
    "aggregate_total",
]

#: Columns the summaries and series read.
UPB_COLUMN = "unpaid_principal_balance"
DATE_COLUMN = "as_of_date"


def load_performance_data(
    start_date: str | None = None,
    end_date: str | None = None,
    silver_dir: str | Path | None = None,
) -> pl.LazyFrame:
    """Scan the loan-level portfolio for a range of reporting months.

    Args:
        start_date: First reporting month, ``YYYYMM``; None for the earliest.
        end_date: Last reporting month, ``YYYYMM``; None for the latest.
        silver_dir: GNMA silver root; defaults to ``GNMAConfig.GNMA_SILVER_DIR``.

    Returns:
        Loan x month rows (see `scan_portfolio`), lazy.

    Raises:
        FileNotFoundError: If no portfolio file falls in the range.
    """
    return scan_portfolio(min_month=start_date, max_month=end_date, silver_dir=silver_dir)


def _lazy(df: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """The input as a LazyFrame."""
    return df.lazy() if isinstance(df, pl.DataFrame) else df


def get_summary_statistics(
    df: pl.DataFrame | pl.LazyFrame, upb_column: str = UPB_COLUMN
) -> dict[str, float | int]:
    """Get summary statistics for performance data.

    Args:
        df: Loan-level rows (eager or lazy)
        upb_column: UPB column to summarize

    Returns:
        Row count, UPB total/mean/median/min/max, and pool and issuer counts when those
        columns are present; empty when there are no rows.
    """
    lf = _lazy(df)
    names = lf.collect_schema().names()
    upb = pl.col(upb_column)
    aggs = [
        pl.len().alias("total_loans"),
        upb.sum().alias("total_upb"),
        upb.mean().alias("mean_upb"),
        upb.median().alias("median_upb"),
        upb.min().alias("min_upb"),
        upb.max().alias("max_upb"),
    ]
    if "pool_id" in names:
        aggs.append(pl.col("pool_id").n_unique().alias("unique_pools"))
    if "issuer_id" in names:
        aggs.append(pl.col("issuer_id").n_unique().alias("unique_issuers"))
    row = lf.select(aggs).collect(engine="streaming").row(0, named=True)
    if row["total_loans"] == 0:
        logger.warning("No rows provided for statistics")
        return {}
    counts = ("total_loans", "unique_pools", "unique_issuers")
    return {
        k: int(v) if k in counts else (scalar_float(v) if v is not None else float("nan"))
        for k, v in row.items()
    }


def create_upb_time_series(
    df: pl.DataFrame | pl.LazyFrame,
    aggregation_level: str = "total",
    upb_column: str = UPB_COLUMN,
    date_column: str = DATE_COLUMN,
) -> pd.DataFrame:
    """Create a time series of unpaid principal balances (UPB).

    Args:
        df: Loan-level rows (eager or lazy)
        aggregation_level: Level of aggregation:
            - 'total': Total UPB across all loans
            - 'pool': UPB by pool
            - 'issuer': UPB by issuer
            - 'loan': Individual loan-level UPB
        upb_column: UPB column to aggregate
        date_column: Reporting-month column

    Returns:
        Pandas DataFrame with the series

    Raises:
        ValueError: If ``aggregation_level`` is not one of the four levels.
    """
    lf = _lazy(df)
    logger.info("Creating UPB time series at %s level", aggregation_level)
    if aggregation_level == "total":
        series = aggregate_total(lf, upb_column, date_column)
    elif aggregation_level == "pool":
        series = aggregate_by_pool(lf, upb_column, date_column)
    elif aggregation_level == "issuer":
        series = aggregate_by_issuer(lf, upb_column, date_column)
    elif aggregation_level == "loan":
        series = lf.select(date_column, "pool_id", "disclosure_sequence_number", upb_column).sort(
            date_column, "pool_id", "disclosure_sequence_number"
        )
    else:
        raise ValueError(f"Invalid aggregation level: {aggregation_level!r}")
    result = series.collect(engine="streaming").to_pandas()
    logger.info("Created time series with %s records", len(result))
    return result


def aggregate_by_pool(
    df: pl.DataFrame | pl.LazyFrame,
    upb_column: str = UPB_COLUMN,
    date_column: str = DATE_COLUMN,
    pool_column: str = "pool_id",
) -> pl.LazyFrame:
    """Aggregate UPB by pool and month.

    Args:
        df: Loan-level rows (eager or lazy)
        upb_column: Name of UPB column
        date_column: Name of date column
        pool_column: Name of pool ID column

    Returns:
        UPB per (month, pool), lazy
    """
    return (
        _lazy(df)
        .group_by(date_column, pool_column)
        .agg(pl.col(upb_column).sum().alias("pool_upb"))
        .sort(date_column, pool_column)
    )


def aggregate_by_issuer(
    df: pl.DataFrame | pl.LazyFrame,
    upb_column: str = UPB_COLUMN,
    date_column: str = DATE_COLUMN,
    issuer_column: str = "issuer_id",
) -> pl.LazyFrame:
    """Aggregate UPB by issuer and month.

    Args:
        df: Loan-level rows (eager or lazy)
        upb_column: Name of UPB column
        date_column: Name of date column
        issuer_column: Name of issuer ID column

    Returns:
        UPB per (month, issuer), lazy
    """
    return (
        _lazy(df)
        .group_by(date_column, issuer_column)
        .agg(pl.col(upb_column).sum().alias("issuer_upb"))
        .sort(date_column, issuer_column)
    )


def aggregate_total(
    df: pl.DataFrame | pl.LazyFrame,
    upb_column: str = UPB_COLUMN,
    date_column: str = DATE_COLUMN,
) -> pl.LazyFrame:
    """Aggregate total UPB by month.

    Args:
        df: Loan-level rows (eager or lazy)
        upb_column: Name of UPB column
        date_column: Name of date column

    Returns:
        Total UPB per month, lazy
    """
    return (
        _lazy(df)
        .group_by(date_column)
        .agg(pl.col(upb_column).sum().alias("total_upb"))
        .sort(date_column)
    )
