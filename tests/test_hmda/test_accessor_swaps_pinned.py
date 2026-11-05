"""Data guard: the shared HMDA readers reproduce the matcher code they replaced, on real silver.

Matchers were moved onto `scan_originations` and `null_sentinel_codes` only where the
output is identical. Each test here keeps the replaced matcher expression inline as the
reference and compares it to the accessor on a real silver year, row for row (row order
included). Reads only a few columns. Skips when the data volume is not mounted.
"""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from mortgage_data_manager.core.types import null_sentinels
from mortgage_data_manager.sources.hmda.config import HMDAConfig
from mortgage_data_manager.sources.hmda.load import (
    null_sentinel_codes,
    scan_hmda,
    scan_hmda_years,
    scan_originations,
)

pytestmark = pytest.mark.skipif(
    not HMDAConfig.get_dataset_dir("silver", "loans", "post2018").exists(),
    reason="HMDA silver not mounted",
)

_POST_YEAR = 2022
_PRE_YEAR = 2012


def _ids(lf: pl.LazyFrame) -> pl.DataFrame:
    return lf.select("HMDAIndex").collect(engine="streaming")


@pytest.mark.parametrize(
    ("period", "year", "loan_types", "file_type"),
    [
        ("post2018", _POST_YEAR, None, None),
        ("post2018", _POST_YEAR, [1], None),
        ("post2018", _POST_YEAR, [3, 4], None),
        ("period_2007_2017", _PRE_YEAR, [2], None),
        ("period_2007_2017", 2017, None, "d"),
    ],
)
def test_scan_originations_matches_inline_filter(period, year, loan_types, file_type):
    old = scan_hmda(period, year, file_type=file_type).filter(
        (pl.col("action_taken") == 1)
        & (pl.lit(True) if loan_types is None else pl.col("loan_type").is_in(loan_types))
    )
    new = scan_originations(period, year, year, loan_types=loan_types, file_type=file_type)
    assert_frame_equal(_ids(new), _ids(old))


def test_scan_originations_split_filter_matches_va_usda_conjunction():
    # match_hmda_gnma.matching_va_usda: one conjunction became origination + residual filters.
    old = scan_hmda_years("post2018", _POST_YEAR, _POST_YEAR).filter(
        pl.col("loan_type").is_in([3, 4])
        & (pl.col("action_taken") == 1)
        & (pl.col("reverse_mortgage") != 1)
        & (pl.col("total_units").fill_null(1) <= 4)
    )
    new = scan_originations("post2018", _POST_YEAR, _POST_YEAR, loan_types=[3, 4]).filter(
        (pl.col("reverse_mortgage") != 1) & (pl.col("total_units").fill_null(1) <= 4)
    )
    assert_frame_equal(_ids(new), _ids(old))


def _post_year(columns: list[str]) -> pl.LazyFrame:
    return scan_hmda("post2018", _POST_YEAR).select(columns)


def test_null_sentinel_codes_matches_hmda_fhlb_post2018_screen():
    cols = [
        "income",
        "loan_term",
        "interest_rate",
        "combined_loan_to_value_ratio",
        "debt_to_income_ratio",
    ]
    lf = _post_year(cols)
    old = lf.with_columns(
        [null_sentinels(pl.col(c), [-99999]).cast(pl.Float64).alias(c) for c in cols]
    )
    new = null_sentinel_codes(lf, "post2018", cols).with_columns(pl.col(cols).cast(pl.Float64))
    assert_frame_equal(new.collect(engine="streaming"), old.collect(engine="streaming"))


def test_null_sentinel_codes_matches_hmda_fha_post2018_exemptions():
    cols = ["interest_rate", "submission_of_application"]
    lf = _post_year(cols)
    exemption_codes = {"interest_rate": -99999, "submission_of_application": 1111}
    old = lf.with_columns(
        [null_sentinels(pl.col(c), [code]).alias(c) for c, code in exemption_codes.items()]
    )
    new = null_sentinel_codes(lf, "post2018", cols)
    assert_frame_equal(new.collect(engine="streaming"), old.collect(engine="streaming"))


@pytest.mark.parametrize("year", [2019, _POST_YEAR])  # 2019 carries applicant_age 9999
def test_null_sentinel_codes_matches_hmda_fhfa_post2018_codes(year):
    numeric = [
        "loan_term", "interest_rate", "intro_rate_period", "debt_to_income_ratio",
        "combined_loan_to_value_ratio", "discount_points", "income", "property_value",
    ]  # fmt: skip
    categorical = [
        "applicant_age", "co_applicant_age", "applicant_sex", "co_applicant_sex",
        "applicant_ethnicity_1", "co_applicant_ethnicity_1", "applicant_race_1",
        "co_applicant_race_1",
    ]  # fmt: skip
    lf = scan_hmda("post2018", year).select(numeric + categorical)
    old = lf.with_columns(
        [null_sentinels(pl.col(c), [-99999]).alias(c) for c in numeric]
        + [null_sentinels(pl.col(c), [8888, 9999]).alias(c) for c in categorical]
    )
    new = null_sentinel_codes(lf, "post2018", numeric + categorical)
    assert_frame_equal(new.collect(engine="streaming"), old.collect(engine="streaming"))
