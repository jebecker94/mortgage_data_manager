"""Freddie Mac UMBS pool acquisition type, from Freddie's Prefix Library.

Freddie's Prefix Library (``prefixlibrary.xlsx`` on capitalmarkets.freddiemac.com, tab
"55-Day Prefixes") labels every UMBS pool-number range of the 30/20/15/10-year prefixes
(``CL``/``CT``/``CI``/``CN``) as one of three pool classes:

- ``Cash``: pools Freddie formed from loans it bought for cash.
- ``MultiLender``: Freddie-formed pools that take swap deliveries from many sellers.
- ``Guar``: single-seller swap (guarantor) pools.

The ranges are transcribed in ``schemas/umbs/fhlmc_prefix_library_ranges.csv``.
`acquisition_type_expr` turns them into a pool-level ``acquisition_type`` column, which
UMBS silver carries on the Freddie ILLD, IS and FU files.

The class describes the pool, not the loan's channel. It equals the channel of the loans
inside only for 30- and 15-year pools issued through April 2025:

- The 20- and 10-year MultiLender pools (``RB5000``-``RB9999``, ``RD5000``-``RD9999``)
  have always held Freddie cash-window loans as well as swapped loans.
- Until April 2025 each month's 30/15-year MultiLender pool was paired with a generic
  Freddie cash pool inside a MultiLender Super. From May 2025 Freddie issues MultiLender
  UMBS (``RQ``, ``RR``) that hold both, so those pools mix cash and swap loans.

On the Freddie IS files from 2019-06, every ``CL`` pool in a Cash, MultiLender or Guar
series falls in exactly one range. Pool numbers no range covers (Supers, exchanged
mirror pools, other prefixes) get null. Fannie publishes no equivalent table.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import polars as pl

from mortgage_data_manager.sources.umbs.config import UMBSConfig

#: The hand-maintained range table (one row per prefix and pool-number range).
PREFIX_LIBRARY_RANGES: Path = UMBSConfig.SCHEMAS_DIR / "umbs" / "fhlmc_prefix_library_ranges.csv"

#: The three pool classes, as the Prefix Library names them.
ACQUISITION_TYPES: tuple[str, ...] = ("Cash", "MultiLender", "Guar")

_POOL_NUMBER = r"^[A-Z]{2}\d{4}$"


@lru_cache(maxsize=1)
def load_prefix_ranges(path: Path = PREFIX_LIBRARY_RANGES) -> pl.DataFrame:
    """Load and check the Prefix Library range table.

    Args:
        path: The range CSV.

    Returns:
        One row per range: ``prefix``, ``pool_number_first``, ``pool_number_last``,
        ``pool_term``, ``description``, ``acquisition_type``.

    Raises:
        ValueError: If a pool number is malformed, a range is inverted, a class is
            unknown, or two ranges of one prefix overlap (a pool would then have two
            classes, and none may be picked).
    """
    ranges = pl.read_csv(path, schema_overrides={"pool_term": pl.String})
    bad = ranges.filter(
        ~pl.col("pool_number_first").str.contains(_POOL_NUMBER)
        | ~pl.col("pool_number_last").str.contains(_POOL_NUMBER)
        | (pl.col("pool_number_first") > pl.col("pool_number_last"))
        | ~pl.col("acquisition_type").is_in(ACQUISITION_TYPES)
    )
    if bad.height:
        raise ValueError(f"Malformed Prefix Library ranges in {path.name}: {bad.rows()}")
    ordered = ranges.sort("prefix", "pool_number_first")
    overlap = ordered.filter(
        (pl.col("prefix") == pl.col("prefix").shift(-1))
        & (pl.col("pool_number_last") >= pl.col("pool_number_first").shift(-1))
    )
    if overlap.height:
        raise ValueError(f"Overlapping Prefix Library ranges in {path.name}: {overlap.rows()}")
    return ordered


def acquisition_type_expr(
    prefix: str = "Prefix", pool_number: str = "Security Identifier"
) -> pl.Expr:
    """Pool class (Cash / MultiLender / Guar) of a Freddie UMBS pool, or null.

    Ranges never overlap within a prefix (checked by `load_prefix_ranges`), so at most
    one range matches and the result does not depend on the order of the checks.

    Args:
        prefix: Column holding the pool prefix (``CL``, ``CT``, ``CI``, ``CN``, ...).
        pool_number: Column holding the six-character pool number (``RA1234``).

    Returns:
        An ``Enum(Cash, MultiLender, Guar)`` expression named ``acquisition_type``.
    """
    pool = pl.col(pool_number).cast(pl.String).str.strip_chars()
    pfx = pl.col(prefix).cast(pl.String).str.strip_chars()
    expr: pl.Expr = pl.lit(None, dtype=pl.String)
    for row in load_prefix_ranges().iter_rows(named=True):
        hit = (pfx == row["prefix"]) & pool.is_between(
            pl.lit(row["pool_number_first"]), pl.lit(row["pool_number_last"])
        )
        expr = pl.when(hit).then(pl.lit(row["acquisition_type"])).otherwise(expr)
    return expr.cast(pl.Enum(list(ACQUISITION_TYPES))).alias("acquisition_type")
