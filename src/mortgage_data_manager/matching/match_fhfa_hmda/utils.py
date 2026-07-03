"""Shared helpers for the FHFA-HMDA matching workflow."""

from __future__ import annotations

import polars as pl

__all__ = ["assign_pre2018_hmda_index"]


def assign_pre2018_hmda_index(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Ensure a stable ``HMDAIndex`` on a pre-2018 HMDA frame.

    2017 HMDA silver carries a persisted row-position ``HMDAIndex`` (its native
    ``sequence_number`` is entirely null), while 2007-2016 predate the column.
    For those earlier years a synthetic-but-stable key is derived from the unique
    ``(activity_year, respondent_id, agency_code, sequence_number)`` tuple — that
    tuple is a perfect 1..N within each lender, so it is unique within year.

    Both the matcher (``clean_hmda_data``) and the validation loader call this so
    their ``HMDAIndex`` values agree and crosswalk joins line up. If the column is
    already present (2017, or any year rebuilt with a persisted index) the frame
    is returned unchanged — the operation is idempotent.

    Args:
        lf: Pre-2018 HMDA frame with at least the identity columns above.

    Returns:
        The frame with a guaranteed ``HMDAIndex`` column.
    """
    if "HMDAIndex" in lf.collect_schema().names():
        return lf
    return lf.with_columns(
        pl.format(
            "{}d_{}_{}_{}",
            pl.col("activity_year"),
            pl.col("respondent_id"),
            pl.col("agency_code"),
            pl.col("sequence_number"),
        ).alias("HMDAIndex")
    )
