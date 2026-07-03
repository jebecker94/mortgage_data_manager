"""Unit test for ``core.manifest.parse_hive_partitions`` (pure path/regex logic, no IO)."""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.manifest import parse_hive_partitions


def test_parse_hive_partitions_extracts_nested_key_values():
    base = Path("data/hmda/silver")
    path = base / "activity_year=2020" / "state=CA" / "d.parquet"
    assert parse_hive_partitions(path, base) == {"activity_year": "2020", "state": "CA"}


def test_parse_hive_partitions_none_when_no_partitions_or_outside_base():
    base = Path("data/hmda/silver")
    assert parse_hive_partitions(base / "d.parquet", base) is None         # no partition dirs
    assert parse_hive_partitions(Path("/other/x=1/d.parquet"), base) is None  # relative_to() fails
