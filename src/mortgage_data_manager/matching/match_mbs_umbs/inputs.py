"""Cross-source dtype/encoding harmonization for the MBS-UMBS matcher.

The matcher joins and filters across five disclosure sources (fnma silver issuances,
fhlmc bronze origination, FNM_ILLD / FRE_ILLD bronze, and the FNM_MLLD / FU first
monthly snapshots) whose dtypes and encodings drift independently whenever an import
layer is rebuilt. Every match key and filter column is normalized to one canonical
representation here, at the matcher boundary, so upstream rebuilds surface as no-ops
instead of silent match-rate collapses.

Each rule pins a drift class that breaks (or silently degrades) the matcher:

- FNMA silver join keys are ``Enum`` (``SchemaError`` on join against the UMBS
  ``String`` columns) and First Time Home Buyer Indicator is ``Boolean`` (a Y/N
  equality filter never passes).
- FNMA silver stores uninsured MIP as null while FNM_ILLD stores 0.0; the strict
  ``(a - b) == 0`` MIP filter is null-poisoned, which would silently make the strict-MIP
  rounds insured-only (FHLMC discloses 0.0 on both sides).
- Float64 representation noise (~1e-7) on Original Interest Rate breaks the exact
  5-key join for ~1.1k loans (measured 2026-07-04).
- Packed-date columns (MMYYYY int/str) vs ``pl.Date`` drift across silver rebuilds.

Regression-tested in ``tests/test_matching/test_mbs_umbs_harmonize.py``.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

#: Exact join keys / exact-equality filter columns that must be String on both sides.
#: Enum/Categorical/other string-like dtypes are cast; values are preserved.
STRING_MATCH_COLUMNS: list[str] = [
    "Loan Sequence Number",
    "Loan Purpose",
    "Property State",
    "Channel",
    "Occupancy Status",
    "First Time Home Buyer Indicator",
    "Property Type",
    "Amortization Type",
]

#: Columns entering difference filters or used as numeric join keys — Float64 both sides
#: (UMBS bronze drifts to string under schema inference; snapshots store f64 where ILLD
#: stores i64).
NUMERIC_MATCH_COLUMNS: list[str] = [
    "Mortgage Loan Amount",
    "Original Interest Rate",
    "Loan-To-Value",
    "Combined Loan-To-Value",
    "Debt-To-Income",
    "Loan Term",
    "Credit Score 1",
    "Credit Score 2",
    "Number of Borrowers",
    "Number of Units",
    "Mortgage Insurance Percentage",
]

#: Decimals for the exact numeric join keys. Rounding kills float representation noise
#: while preserving the disclosure grid (rates are disclosed at 3dp, amounts in whole
#: dollars) — values on the grid are unchanged.
JOIN_KEY_DECIMALS: dict[str, int] = {
    "Original Interest Rate": 3,
    "Mortgage Loan Amount": 2,
}

FTHB = "First Time Home Buyer Indicator"
MIP = "Mortgage Insurance Percentage"


def parse_packed_date(lf: pl.LazyFrame, column: str) -> pl.LazyFrame:
    """Normalize a packed MYYYY / MMYYYY date column to ``pl.Date``.

    Passthrough when the column is absent or already a Date (current silver parses
    packed encodings at import; bronze sources still carry the packed form).

    Args:
        lf: The frame to normalize.
        column: Name of the packed-date column.

    Returns:
        The frame with ``column`` parsed to ``pl.Date``.
    """
    schema = lf.collect_schema()
    if column not in schema.names() or schema[column] == pl.Date:
        return lf
    return lf.with_columns(
        pl.col(column)
        .cast(pl.String)
        .str.zfill(6)
        .str.to_date(format="%m%Y", strict=False)
        .cast(pl.Date)
    )


def harmonize_match_frame(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize dtypes and encodings of every match column present in the frame.

    Applied to both sides of the match immediately after column standardization, so
    the join/filter logic downstream always sees one canonical representation:

    1. Boolean First Time Home Buyer Indicator → ``"Y"``/``"N"`` (UMBS encoding).
    2. String-like match columns (Enum/Categorical/…) → ``String``.
    3. Numeric match columns → ``Float64`` (non-strict).
    4. Exact numeric join keys rounded to the disclosure grid (rate 3dp, amount 2dp).
    5. Mortgage Insurance Percentage nulls → 0.0 (uninsured; aligns FNMA silver's
       null encoding with the UMBS/FHLMC 0.0 encoding so strict-MIP rounds cover
       uninsured loans).

    Args:
        lf: The frame to normalize.

    Returns:
        The frame with every present match column normalized.
    """
    schema = lf.collect_schema()
    names = schema.names()

    if FTHB in names and schema[FTHB] == pl.Boolean:
        lf = lf.with_columns(
            pl.when(pl.col(FTHB)).then(pl.lit("Y")).otherwise(pl.lit("N")).alias(FTHB)
        )

    lf = lf.with_columns([pl.col(c).cast(pl.String) for c in STRING_MATCH_COLUMNS if c in names])
    lf = lf.with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in NUMERIC_MATCH_COLUMNS if c in names]
    )
    lf = lf.with_columns([pl.col(c).round(d) for c, d in JOIN_KEY_DECIMALS.items() if c in names])
    if MIP in names:
        lf = lf.with_columns(pl.col(MIP).fill_null(0.0))
    return lf
