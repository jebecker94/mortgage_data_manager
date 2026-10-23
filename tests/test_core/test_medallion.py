"""Unit tests for the medallion output guards.

``should_process_file`` is the mandated skip/overwrite guard, and ``atomic_output`` /
``write_hive_partitioned`` are what keep a killed writer from leaving a partial file
under a final name — which existence-based skipping would otherwise treat as complete
forever after. The atomicity contract (final name appears only complete, temps are
cleaned up) is pinned here.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.core.medallion import (
    atomic_output,
    should_process_file,
    write_hive_partitioned,
)


def test_should_process_file_truth_table(tmp_path):
    existing = tmp_path / "out.parquet"
    existing.touch()
    missing = tmp_path / "missing.parquet"

    assert should_process_file(missing, overwrite=False) is True  # absent -> build
    assert should_process_file(existing, overwrite=False) is False  # present -> skip
    assert should_process_file(existing, overwrite=True) is True  # overwrite -> always build


def test_atomic_output_publishes_on_success(tmp_path):
    final = tmp_path / "out.parquet"
    with atomic_output(final) as tmp:
        assert tmp.parent == final.parent  # same dir -> same filesystem -> atomic rename
        tmp.write_text("payload")
        assert not final.exists()  # nothing under the final name mid-write
    assert final.read_text() == "payload"
    assert not tmp.exists()


def test_atomic_output_cleans_up_on_failure(tmp_path):
    final = tmp_path / "out.parquet"
    with pytest.raises(RuntimeError):
        with atomic_output(final) as tmp:
            tmp.write_text("partial")
            raise RuntimeError("writer died")
    # a failed write publishes nothing and leaves no temp -> the next run rebuilds
    assert not final.exists()
    assert not tmp.exists()
    assert should_process_file(final, overwrite=False) is True


def test_atomic_output_removes_stale_temp_from_killed_run(tmp_path):
    final = tmp_path / "out.parquet"
    stale = final.with_name(final.name + ".tmp")
    stale.write_text("leftover from a killed run")
    with atomic_output(final) as tmp:
        assert not tmp.exists()  # stale temp was cleared, not reused
        tmp.write_text("fresh")
    assert final.read_text() == "fresh"


def test_write_hive_partitioned_leaves_no_temp_tree(tmp_path):
    out = tmp_path / "silver"
    lf = pl.LazyFrame({"year": [2020, 2020, 2021], "value": [1, 2, 3]})
    write_hive_partitioned(lf, out, partition_cols=["year"], include_key=True)

    assert sorted(p.name for p in out.glob("year=*")) == ["year=2020", "year=2021"]
    got = pl.scan_parquet(out / "**/*.parquet").collect()
    assert got.sort("value")["value"].to_list() == [1, 2, 3]
    assert not (tmp_path / ".silver.tmp").exists()  # temp tree removed after publish
