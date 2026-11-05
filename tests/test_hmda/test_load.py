"""Release-vintage selection for HMDA silver reads.

The layout under test is synthetic: one-row parquet files in the
``activity_year=/file_type=`` hive layout, with the divergent column sets the real
2025 ``c``/``e`` partitions carry, so the preference order, the pinned-to-index
read and the name-aligned multi-year concat are checked without the data volume.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.hmda.load import (
    FILE_TYPE_PREFERENCE,
    best_file_type,
    scan_hmda,
    scan_hmda_for_index,
    scan_hmda_years,
)
from mortgage_data_manager.sources.hmda.utils import hmda_index_file_type, hmda_index_year


def _write(root: Path, year: int, file_type: str | None, **extra: list) -> None:
    part = root / f"activity_year={year}"
    if file_type is not None:
        part = part / f"file_type={file_type}"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "HMDAIndex": [f"{year}{file_type or 'x'}_000000001"],
            "activity_year": pl.Series([year], dtype=pl.Int16),
            "file_type": [file_type],
            **extra,
        }
    ).write_parquet(part / "data.parquet")


@pytest.fixture
def post2018(tmp_path: Path) -> Path:
    root = tmp_path / "post2018"
    for ft in ("a", "b", "c"):
        _write(root, 2020, ft)
    _write(root, 2025, "c", conforming_loan_limit=[1])
    _write(root, 2025, "e", applicant_credit_scoring_model=["x"])
    return root


def test_best_file_type_follows_preference(post2018: Path) -> None:
    assert best_file_type("post2018", 2020, root=post2018) == "a"
    assert best_file_type("post2018", 2025, root=post2018) == "c"


def test_best_file_type_pre2018_prefers_a_over_d(tmp_path: Path) -> None:
    root = tmp_path / "period_2007_2017"
    _write(root, 2012, "d")
    _write(root, 2017, "a")
    _write(root, 2017, "d")
    assert best_file_type("period_2007_2017", 2012, root=root) == "d"
    assert best_file_type("period_2007_2017", 2017, root=root) == "a"


def test_unpartitioned_year_scans_the_year_dir(tmp_path: Path) -> None:
    root = tmp_path / "panel"
    _write(root, 2010, None)
    assert best_file_type("period_2007_2017", 2010, dataset="panel", root=root) is None
    assert scan_hmda("period_2007_2017", 2010, dataset="panel", root=root).collect().height == 1


def test_unclassified_release_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "post2018"
    _write(root, 2030, "z")
    with pytest.raises(ValueError, match="FILE_TYPE_PREFERENCE"):
        best_file_type("post2018", 2030, root=root)


def test_missing_year_or_release_raises(post2018: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_hmda("post2018", 1999, root=post2018)
    with pytest.raises(FileNotFoundError):
        scan_hmda("post2018", 2020, file_type="e", root=post2018)


def test_scan_hmda_reads_exactly_one_partition(post2018: Path) -> None:
    best = scan_hmda("post2018", 2025, root=post2018).collect()
    assert best["file_type"].to_list() == ["c"]
    assert "applicant_credit_scoring_model" not in best.columns
    pinned = scan_hmda("post2018", 2025, file_type="e", root=post2018).collect()
    assert pinned["file_type"].to_list() == ["e"]


def test_scan_hmda_years_aligns_by_name_and_skips_absent(post2018: Path) -> None:
    df = scan_hmda_years("post2018", 2020, 2025, root=post2018).collect()
    assert df["activity_year"].to_list() == [2020, 2025]
    assert df["file_type"].to_list() == ["a", "c"]
    assert df["conforming_loan_limit"].to_list() == [None, 1]
    # an open bound means "whatever is on disk"
    assert scan_hmda_years("post2018", root=post2018).collect().height == 2
    assert scan_hmda_years("post2018", min_year=2021, root=post2018).collect().height == 1


def test_scan_hmda_years_raises_when_nothing_found(post2018: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_hmda_years("post2018", 2030, 2031, root=post2018)


def test_scan_hmda_for_index_pins_the_release(post2018: Path) -> None:
    index = pl.DataFrame(
        {"HMDAIndex": ["2020b_000000001", "2025e_000000001", "2025e_000000009", None]}
    )
    df = scan_hmda_for_index(index, "post2018", root=post2018).collect()
    assert sorted(zip(df["activity_year"], df["file_type"])) == [(2020, "b"), (2025, "e")]
    assert scan_hmda_for_index(index["HMDAIndex"], "post2018", root=post2018).collect().height == 2
    with pytest.raises(ValueError):
        scan_hmda_for_index(index.clear(), "post2018", root=post2018)


def test_index_prefix_parsers() -> None:
    df = pl.DataFrame({"HMDAIndex": ["2020a_000000001", "2017d_000000042"]}).select(
        hmda_index_year().alias("year"), hmda_index_file_type().alias("file_type")
    )
    assert df["year"].to_list() == [2020, 2017]
    assert df["year"].dtype == pl.Int32
    assert df["file_type"].to_list() == ["a", "d"]


def test_preference_is_keyed_by_era() -> None:
    assert FILE_TYPE_PREFERENCE["post2018"] == ("a", "b", "c", "e")
    assert FILE_TYPE_PREFERENCE["period_2007_2017"] == ("a", "d")


def test_null_sentinel_codes_nulls_only_documented_absent_values() -> None:
    from mortgage_data_manager.sources.hmda.load import null_sentinel_codes

    lf = pl.LazyFrame(
        {
            "interest_rate": [-99999.0, 3.5],
            "aus_1": pl.Series([1111, 1], dtype=pl.Int16),
            "applicant_age": pl.Series([8888, 9999], dtype=pl.Int16),
            "co_applicant_age": pl.Series([9999, 8888], dtype=pl.Int16),
            "total_loan_costs": [9999.0, 1111.0],  # dollar amounts, not codes
            "purchaser_type": pl.Series([0, 1], dtype=pl.Int8),
        }
    )
    out = null_sentinel_codes(lf, "post2018").collect()
    assert out["interest_rate"].to_list() == [None, 3.5]
    assert out["aus_1"].to_list() == [None, 1]
    assert out["applicant_age"].to_list() == [None, None]  # 9999 misfiled from co-applicant
    assert out["co_applicant_age"].to_list() == [None, None]
    assert out["total_loan_costs"].to_list() == [9999.0, 1111.0]
    assert out["purchaser_type"].to_list() == [0, 1]
    assert out.schema == lf.collect_schema()

    only = null_sentinel_codes(lf, "post2018", columns=["aus_1", "not_a_column"]).collect()
    assert only["interest_rate"].to_list() == [-99999.0, 3.5]

    pre = pl.LazyFrame({"income": [9_999_000, 55_000]})
    assert null_sentinel_codes(pre, "period_2007_2017").collect()["income"].to_list() == [
        None,
        55_000,
    ]
