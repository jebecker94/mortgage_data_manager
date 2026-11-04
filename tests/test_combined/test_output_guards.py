"""Data guards on the built combined masters: row floors, grain keys, vocabularies, ranges.

Each test reads the persisted output and asserts a property every build must keep, so a
rebuild that silently loses a population, duplicates a key or lets a sentinel through
fails here instead of in an analysis. Row counts and column ranges come from parquet
footers; the key and vocabulary checks scan one or two columns.

All tests skip when the data volume is not mounted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_performance import _GNMA_REMOVAL_REASON
from mortgage_data_manager.combined.loan_performance_schema import EVENT_CLASS, ZBC_EVENT_TYPE
from mortgage_data_manager.combined.mbs_performance_schema import STRUCTURE_CLASSES

_ROOT = CombinedConfig.COMBINED_DATA_DIR

#: Rows per master at the 2026-10 build, less 5%: ordinary growth passes, a lost
#: population (an agency, a source file family) fails.
_ROW_FLOORS: dict[str, int] = {
    "loan_issuance": 150_000_000,
    "loan_performance": 7_700_000_000,
    "loan_terminations": 88_000_000,
    "loan_outcomes": 137_000_000,
    "mbs_issuance": 680_000,
    "mbs_performance": 86_000_000,
}


def _files(dataset: str) -> list[Path]:
    return sorted((_ROOT / dataset).rglob("*.parquet"))


def _scan(dataset: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(_ROOT / dataset / "**" / "*.parquet"), hive_partitioning=True)


def _footer_range(dataset: str, column: str) -> tuple[float | None, float | None]:
    """Min and max of ``column`` over every row group, from footer statistics alone."""
    lo: float | None = None
    hi: float | None = None
    for f in _files(dataset):
        md = pq.ParquetFile(f).metadata
        idx = [i for i in range(md.num_columns) if md.schema.column(i).name == column]
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(idx[0]).statistics if idx else None
            if st is not None and st.has_min_max:
                lo = st.min if lo is None else min(lo, st.min)
                hi = st.max if hi is None else max(hi, st.max)
    return lo, hi


def _duplicate_keys(dataset: str, keys: list[str]) -> int:
    """Rows beyond the first per key, counted on a 64-bit hash of the key columns."""
    h = _scan(dataset).select(pl.struct(keys).hash().alias("h"))
    counts = h.select(pl.len(), pl.col("h").n_unique()).collect(engine="streaming")
    return counts.item(0, 0) - counts.item(0, 1)


def _outside(dataset: str, column: str, allowed: set[str]) -> list[str]:
    seen = _scan(dataset).select(pl.col(column).unique()).collect(engine="streaming")
    return sorted(v for v in seen[column].drop_nulls().to_list() if v not in allowed)


pytestmark = pytest.mark.skipif(not _ROOT.exists(), reason="combined outputs not mounted")


@pytest.mark.parametrize("dataset", list(_ROW_FLOORS))
def test_row_floor(dataset: str) -> None:
    rows = sum(pq.ParquetFile(f).metadata.num_rows for f in _files(dataset))
    assert rows >= _ROW_FLOORS[dataset], f"{dataset}: {rows:,} rows"


@pytest.mark.parametrize(
    ("dataset", "keys"),
    [
        ("loan_issuance", ["agency", "loan_key"]),
        ("loan_terminations", ["agency", "loan_key"]),
        ("mbs_performance", ["agency", "pool_id", "as_of_month"]),
        ("loan_outcomes", ["agency", "loan_key"]),
        ("mbs_issuance", ["cusip"]),
    ],
)
def test_grain_is_unique(dataset: str, keys: list[str]) -> None:
    assert _duplicate_keys(dataset, keys) == 0


def test_vocabularies() -> None:
    assert _outside("loan_issuance", "channel", {"retail", "correspondent", "broker", "tpo"}) == []
    purposes = {"purchase", "refi_cash_out", "refi_rate_term", "refi_unspecified"}
    assert _outside("loan_issuance", "loan_purpose", purposes) == []
    assert _outside("loan_outcomes", "event_class", set(EVENT_CLASS.values())) == []
    for dataset in ("mbs_issuance", "mbs_performance"):
        assert _outside(dataset, "structure_class", set(STRUCTURE_CLASSES)) == []


@pytest.mark.parametrize("agency", ["FNMA", "FHLMC"])
def test_outcomes_count_a_crosswalked_loan_once(agency: str) -> None:
    # A loan the mbs_umbs crosswalk ties to an SF loan belongs under its SF key; a
    # umbs_seq row for it as well counts the loan twice (598,779 rows in the 2026-09
    # outcomes, built against an older crosswalk).
    path = (
        CombinedConfig.CROSSWALK_DIR / "mbs_umbs" / f"mbs_umbs_crosswalk_{agency.lower()}.parquet"
    )
    umbs_ids = pl.scan_parquet(path).select(
        pl.col("Loan Sequence Number (UMBS)").cast(pl.String).alias("loan_key")
    )
    twice = (
        _scan("loan_outcomes")
        .filter(pl.col("agency") == agency, pl.col("loan_key_kind") == "umbs_seq")
        .join(umbs_ids, on="loan_key", how="semi")
        .select(pl.len())
    )
    assert twice.collect(engine="streaming").item() == 0


def test_terminations_event_types() -> None:
    events = set(ZBC_EVENT_TYPE.values()) | set(_GNMA_REMOVAL_REASON.values())
    assert _outside("loan_terminations", "event_type", events) == []


def test_credit_scores_in_range() -> None:
    for column in ("representative_credit_score", "borrower_credit_score"):
        lo, hi = _footer_range("loan_issuance", column)
        assert lo is not None and hi is not None
        assert 300 <= lo and hi <= 850, f"{column}: [{lo}, {hi}]"


def test_issuance_sentinels_scrubbed() -> None:
    caps = {"ltv": 900, "cltv": 900, "mi_percent": 999, "number_of_borrowers": 99}
    for column, cap in caps.items():
        _, hi = _footer_range("loan_issuance", column)
        assert hi is None or hi < cap, f"{column} max {hi}"
    quarters = _scan("loan_issuance").select(pl.col("orig_quarter").str.slice(0, 4).max())
    assert quarters.collect(engine="streaming").item() <= "2030"
