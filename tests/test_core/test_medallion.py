"""Unit test for the medallion skip/overwrite guard ``should_process_file``.

Every builder routes its output guard through this function (CLAUDE.md mandates it over a
hand-rolled ``path.exists() and not overwrite``), so its two-line truth table is worth
pinning once, here.
"""

from __future__ import annotations

from mortgage_data_manager.core.medallion import should_process_file


def test_should_process_file_truth_table(tmp_path):
    existing = tmp_path / "out.parquet"
    existing.touch()
    missing = tmp_path / "missing.parquet"

    assert should_process_file(missing, overwrite=False) is True    # absent -> build
    assert should_process_file(existing, overwrite=False) is False  # present -> skip
    assert should_process_file(existing, overwrite=True) is True     # overwrite -> always build
