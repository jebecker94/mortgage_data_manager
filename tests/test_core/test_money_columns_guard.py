"""Data guard: no dollar-amount column in an analysed layer is stored as Int32 or UInt32.

Polars keeps Int32 for ``sum()``, ``group_by().sum()`` and ``sum_horizontal()`` and wraps
on overflow without an error, so an Int32 dollar column gives wrong totals past ~$2.1B
(about 10,000 mortgages). Specs used to narrow money to Int32 whenever every value fit;
FHA ``Mortgage Amount`` summed to -$1.78B across 17.6M loans before it was widened. The
rule now lives in ``core/types.py`` and :func:`narrow_money_columns` names the offenders.

Reads parquet schemas only, and within a hive-partitioned tree only the first and last
partition at each level: a spec types every partition alike, so the ends are enough.
Skips when the data volume is not mounted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import narrow_money_columns

_DATA_DIR = MortgageDataConfig.DATA_DIR

#: Every layer people analyse: each source's silver and gold, the cross-source outputs,
#: and usps_crosswalk's bronze (that source stops at bronze).
_ROOTS = sorted(
    [
        *_DATA_DIR.glob("*/silver"),
        *_DATA_DIR.glob("*/gold"),
        _DATA_DIR / "combined",
        _DATA_DIR / "matching" / "bridges",
        _DATA_DIR / "usps_crosswalk" / "bronze",
    ]
)


def _samples(d: Path) -> list[Path]:
    """First and last parquet file under ``d``, descending only the end year/month partitions."""
    files = sorted(d.glob("*.parquet"))
    out = [files[0], files[-1]] if files else []
    subdirs = sorted(p for p in d.iterdir() if p.is_dir())
    if subdirs and all(p.name.partition("=")[2].isdigit() for p in subdirs):
        subdirs = [subdirs[0], subdirs[-1]]
    for sub in subdirs:
        out.extend(_samples(sub))
    return out


@pytest.mark.skipif(not _DATA_DIR.exists(), reason="data volume not mounted")
@pytest.mark.parametrize("root", _ROOTS, ids=lambda p: str(p.relative_to(_DATA_DIR)))
def test_money_columns_are_not_32_bit(root: Path):
    if not root.exists():
        pytest.skip(f"{root} not built")
    offenders = {
        f"{f.relative_to(_DATA_DIR)}: {col}"
        for f in _samples(root)
        for col in narrow_money_columns(pl.read_parquet_schema(f))
    }
    assert not offenders, (
        "dollar columns stored as 32-bit ints (sums wrap silently); widen them to Int64 "
        "in the spec and rebuild:\n  " + "\n  ".join(sorted(offenders))
    )
