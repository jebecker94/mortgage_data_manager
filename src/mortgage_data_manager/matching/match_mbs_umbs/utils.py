"""Shared load-side helpers for the MBS-UMBS matcher.

Both GSE legs run one round engine (``matching.py``) and share the entire load side: rename
maps built from the per-GSE column-mapping CSV, the ``[lo, hi)`` First Payment Date window, and the scan → standardize → harmonize →
window frame loader. Those shared pieces live here; dtype/encoding normalization
itself stays in ``inputs.py`` (the documented drift contract these loaders call).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import polars as pl

from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_mbs_umbs.inputs import (
    harmonize_match_frame,
    parse_packed_date,
)

__all__ = [
    "COMPARED_COLUMNS",
    "CORE_EXACT_COLUMNS",
    "apply_null_tolerance",
    "build_rename_maps",
    "compute_date_window",
    "core_mismatch_count",
    "drop_conflicting_duplicates",
    "load_standardized_frame",
]

#: Every standardized column the engine's round logic compares: the exact
#: join keys plus the tolerance and equality filters. A duplicate disclosure that
#: disagrees on any of these could decide a match, so it is excluded, never picked.
COMPARED_COLUMNS: tuple[str, ...] = (
    "Loan Purpose",
    "Property State",
    "Mortgage Loan Amount",
    "Original Interest Rate",
    "First Payment Date",
    "Loan-To-Value",
    "Combined Loan-To-Value",
    "Debt-To-Income",
    "Number of Borrowers",
    "Number of Units",
    "Loan Term",
    "Channel",
    "Occupancy Status",
    "First Time Home Buyer Indicator",
    "Property Type",
    "Credit Score 1",
    "Mortgage Insurance Percentage",
)

#: The tolerance-band filters on which a *true* pair agrees exactly. Measured on base-pass
#: pairs, both sides populated: LTV 100.00/100.00%, CLTV 100.00/99.99%, DTI 99.96/100.00%,
#: Loan Term 100.00/100.00% (FNMA/FHLMC). The +/-1 and +/-6 bands are therefore essentially
#: never used by a real pair, which is what makes exactness on all four a *margin* rather
#: than a tie-break. ``Credit Score 1`` is deliberately excluded even though it is also a
#: tolerance filter: FNMA true pairs agree exactly only 78% of the time because of the
#: VantageScore-4 / Classic-FICO score-type transition round 3 exists to absorb, so a score
#: gap is not evidence.
CORE_EXACT_COLUMNS: tuple[str, ...] = (
    "Loan-To-Value",
    "Combined Loan-To-Value",
    "Debt-To-Income",
    "Loan Term",
)


def core_mismatch_count() -> pl.Expr:
    """Count the `CORE_EXACT_COLUMNS` a candidate pair disagrees on, over a merged frame.

    Expects the suffixed column names the engine's round join produces
    (``"Loan-To-Value (MBS)"`` / ``"Loan-To-Value (UMBS)"``, and so on for the other three).
    A one-sided null is **not** a mismatch: the two disclosures cover
    the same loan under different rules, so an absent value is no evidence either way —
    the same reading `apply_null_tolerance` takes for the filters themselves.

    Returns:
        An ``Int32`` expression, 0 when the pair is exact on all four attributes.
    """
    return pl.sum_horizontal(
        [
            ((pl.col(f"{c} (MBS)") - pl.col(f"{c} (UMBS)")).abs() > 0)
            .fill_null(value=False)
            .cast(pl.Int32)
            for c in CORE_EXACT_COLUMNS
        ]
    )


def apply_null_tolerance(conditions: list[pl.Expr], *, enabled: bool = True) -> list[pl.Expr]:
    """Make comparison predicates admit rows where either side is missing.

    A polars comparison against a null yields null, and ``filter`` drops nulls — so a
    single missing attribute vetoes a candidate that agrees on every other key. Both
    the positive (``diff <= tol``) and negated (``~(diff > tol)``) forms behave this
    way; only an explicit ``fill_null(True)`` admits the row.

    That veto is wrong for this matcher: the two sides disclose the same loan under
    different coverage rules, so a genuine pair can carry a value on one side and not
    the other. Being permissive on the missing attribute and letting the remaining
    keys plus the strict 1:1 dedup carry the decision is the conservative reading —
    though it is not free, since admitting more candidates can also break a pair that
    was previously uniquely resolved.

    Args:
        conditions: Comparison expressions to relax.
        enabled: When ``False`` the conditions are returned unchanged (null-vetoing
            baseline, retained so the two behaviors can be diffed on real data).

    Returns:
        The conditions, each admitting nulls when ``enabled``.
    """
    if not enabled:
        return conditions
    return [c.fill_null(value=True) for c in conditions]


def build_rename_maps(
    variable_file: Path, match_columns: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the MBS-side and UMBS-side source → standardized-name rename maps.

    Reads the per-GSE column-mapping CSV (``mbs_column``, ``umbs_column``,
    ``standardized_name``) and keeps only rows whose standardized name the matcher
    actually uses; rows mapping columns outside ``match_columns`` are ignored.

    Args:
        variable_file: Path to the column-mapping CSV shipped next to the matcher.
        match_columns: Standardized names used as match keys or filters.

    Returns:
        ``(mbs_rename_map, umbs_rename_map)`` dicts of source column → standardized name.
    """
    variable_crosswalk = pl.read_csv(variable_file)

    def _side(source_col: str) -> dict[str, str]:
        return {
            row[source_col]: row["standardized_name"]
            for row in variable_crosswalk.select([source_col, "standardized_name"])
            .drop_nulls()
            .filter(pl.col("standardized_name").is_in(match_columns))
            .to_dicts()
        }

    return _side("mbs_column"), _side("umbs_column")


def compute_date_window(
    max_year: int, max_month: int, snapshot_file: Path | None
) -> tuple[datetime.datetime, datetime.datetime, datetime.datetime]:
    """Compute the First Payment Date window for one matcher run.

    The UMBS ILLD issuance disclosure begins at ``UMBS_ISSUANCE_START``; when a first
    monthly snapshot is supplied the MBS lower bound opens to recover loans that
    predate ILLD. The upper bound is exclusive-of-next-month.

    Args:
        max_year: Last year to include in matching.
        max_month: Last month to include in matching.
        snapshot_file: First monthly UMBS snapshot, or ``None`` for ILLD-only matching.

    Returns:
        ``(issuance_start, mbs_first_date, last_date)`` where the MBS side is windowed
        to ``[mbs_first_date, last_date)`` and the UMBS ILLD side to
        ``[issuance_start, last_date)``.
    """
    issuance_start = datetime.datetime.fromisoformat(Config.UMBS_ISSUANCE_START)
    if max_month == 12:
        last_date = datetime.datetime(max_year + 1, 1, 1)
    else:
        last_date = datetime.datetime(max_year, max_month + 1, 1)
    mbs_first_date = datetime.datetime(1900, 1, 1) if snapshot_file is not None else issuance_start
    return issuance_start, mbs_first_date, last_date


def load_standardized_frame(
    pattern: str,
    rename_map: dict[str, str],
    fpd_lo: datetime.datetime,
    fpd_hi: datetime.datetime,
) -> pl.LazyFrame:
    """Scan a disclosure source, standardize + harmonize columns, window FPD to [lo, hi).

    ``missing_columns="insert"`` / ``extra_columns="ignore"`` absorb schema differences
    across source vintages. All dtype/encoding normalization (Enum → String, Boolean
    FTHB → Y/N, Float64 casts, join-key rounding, MIP null → 0) lives in
    ``harmonize_match_frame``; ``parse_packed_date`` passes through already-``Date``
    columns.

    Args:
        pattern: ``scan_parquet`` path or glob for one side of the match.
        rename_map: Source column → standardized name map from ``build_rename_maps``.
        fpd_lo: Inclusive First Payment Date lower bound.
        fpd_hi: Exclusive First Payment Date upper bound.

    Returns:
        The standardized, harmonized, FPD-windowed LazyFrame.
    """
    lf = pl.scan_parquet(pattern, missing_columns="insert", extra_columns="ignore")
    lf = lf.select(list(rename_map.keys())).rename(rename_map)
    lf = harmonize_match_frame(parse_packed_date(lf, "First Payment Date"))
    return lf.filter(
        (pl.col("First Payment Date") >= fpd_lo) & (pl.col("First Payment Date") < fpd_hi)
    )


def drop_conflicting_duplicates(
    lf: pl.LazyFrame, key: str, compared: tuple[str, ...] = COMPARED_COLUMNS
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """One row per ``key``, excluding every key whose rows disagree on a compared column.

    Rows identical on ``key`` and every compared column collapse to one: the matcher
    reads no other column and emits only loan ids, so which copy survives cannot change
    a match. A key whose rows still differ -- the same loan disclosed twice with
    different values -- is dropped, because which disclosure is right is unknowable
    and keeping either one would decide the loan's match arbitrarily.

    Args:
        lf: A standardized frame from `load_standardized_frame`.
        key: The loan identifier column.
        compared: Columns the matcher compares; those absent from ``lf`` are skipped.

    Returns:
        ``(deduped, excluded_keys)``: one row per surviving key, and the keys dropped.
    """
    names = set(lf.collect_schema().names())
    subset = [key, *(c for c in compared if c in names)]
    # tie-safe: survivors are identical on every compared column and only ids leave the matcher
    collapsed = lf.unique(subset=subset, keep="any")
    excluded = collapsed.group_by(key).len().filter(pl.col("len") > 1).select(key)
    return collapsed.unique(subset=key, keep="none"), excluded
