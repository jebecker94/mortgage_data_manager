"""Plan-table integrity: every leg plan names only readers its table defines."""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.combined import loan_issuance_schema as lis
from mortgage_data_manager.combined import loan_performance_schema as lps
from mortgage_data_manager.combined import mbs_issuance_schema as mis
from mortgage_data_manager.combined import mbs_performance_schema as mps
from mortgage_data_manager.combined._common import LegField, Readers, _plan_exprs
from mortgage_data_manager.combined.loan_issuance import _gnma_issuance_readers
from mortgage_data_manager.combined.loan_performance import _gnma_perf_readers
from mortgage_data_manager.combined.mbs_issuance import _mbs_readers

_MBS_PLANS = [
    mis.GSE_MBS_ISSUANCE_PLAN,
    mis.GNMA_MBS_ISSUANCE_PLAN,
    mis.GNMA_LEGACY_MBS_ISSUANCE_PLAN,
    mis.NISSUES_U_PLAN,
    mis.GNMA_PLATINUM_MBS_ISSUANCE_PLAN,
    mis.PLATINUM_WA_ISSUANCE_PLAN,
    mps.GSE_MBS_PERFORMANCE_PLAN,
    mps.GNMA_MBS_PERFORMANCE_PLAN,
    mps.GNMA_WA_PERFORMANCE_PLAN,
    mps.GNMA_PLATINUM_MBS_PERFORMANCE_PLAN,
    mps.GN_MEGA_PERFORMANCE_PLAN,
]
_CASES: list[tuple[list[LegField], Readers]] = [
    *[(plan, _mbs_readers(set())) for plan in _MBS_PLANS],
    (lis.GNMA_LOAN_ISSUANCE_PLAN, _gnma_issuance_readers(pl.Schema())),
    (lps.GNMA_LOAN_PERFORMANCE_PLAN, _gnma_perf_readers(pl.Schema())),
]


@pytest.mark.parametrize(("plan", "readers"), _CASES)
def test_plan_readers_exist_and_names_are_unique(plan: list[LegField], readers: Readers) -> None:
    unknown = {f.read for f in plan} - {*readers, "const", "derived"}
    assert not unknown, f"readers not in the table: {sorted(unknown)}"
    names = [f.name for f in plan]
    assert len(names) == len(set(names))


def test_plan_exprs_builds_in_plan_order_and_checks_derived() -> None:
    plan = [
        LegField("a", "derived"),
        LegField("b", "const", value="x"),
        LegField("c", "num", ("src",)),
        LegField("d", "num"),
    ]
    readers = _mbs_readers({"src"})
    lf = pl.LazyFrame({"src": ["1.5"]})
    out = lf.select(_plan_exprs(plan, readers, {"a": pl.lit(1)})).collect()
    assert out.columns == ["a", "b", "c", "d"]
    assert out.row(0) == (1, "x", 1.5, None)
    assert out.schema["d"] == pl.Float64  # a column with no source keeps its reader's dtype
    with pytest.raises(ValueError, match="has no expression"):
        _plan_exprs(plan, readers, {})
    with pytest.raises(ValueError, match="no plan column"):
        _plan_exprs(plan, readers, {"a": pl.lit(1), "z": pl.lit(2)})
