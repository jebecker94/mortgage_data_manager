"""Regression tests for the ``loan_outcomes`` map/reduce aggregation semantics.

The builder collapses a loan's monthly history in two levels (per panel file, then per
loan across files). The contract worth pinning is that the two-level fold gives the same
answer as a single pass, and that the null semantics are per agency: absence of a
modification flag on a disclosing agency means "not modified", while an agency that never
carries the flag gets a null column.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from mortgage_data_manager.combined.builders import (
    _finalize_loan_outcomes,
    _months_between,
    loan_outcome_partial_aggs,
    loan_outcome_reduce_aggs,
)


def _panel() -> pl.DataFrame:
    # Loan A: three months over two "files", 60 DPD peak, modified, prepays in 202002.
    # Loan B: two months, never delinquent, no flags, still active.
    return pl.DataFrame(
        {
            "loan_key": ["A", "A", "A", "B", "B"],
            "loan_key_kind": ["sf_seq"] * 5,
            "source_universe": ["gse_disclosure"] * 5,
            "reporting_period": [201911, 201912, 202002, 202001, 202002],
            "current_upb": [100.0, 99.0, 0.0, 50.0, 49.0],
            "loan_age": [1, 2, 4, 1, 2],
            "delinquency_months": [0, 2, 0, 0, 0],
            "servicer_name": ["S1", "S1", "S2", "S3", "S3"],
            "modification_flag": [None, "Y", None, None, None],
            "number_of_modifications": [None, None, None, None, None],
            "borrower_assistance_plan": [None, "F", None, None, None],
            "event_flag": [False, False, True, False, False],
            "event_type": [None, None, "prepaid_or_matured", None, None],
            "event_class": [None, None, "voluntary_payoff", None, None],
            "is_credit_event": [None, None, False, None, None],
            "zero_balance_effective_date": [None, None, date(2020, 2, 1), None, None],
        },
        schema_overrides={
            "modification_flag": pl.String,
            "number_of_modifications": pl.Int8,
            "borrower_assistance_plan": pl.String,
            "is_credit_event": pl.Boolean,
        },
    )


def _reduce(partials: list[pl.DataFrame], info: dict[str, bool]) -> pl.DataFrame:
    stacked = pl.concat(partials).lazy()
    reduced = stacked.group_by("loan_key").agg(loan_outcome_reduce_aggs())
    return _finalize_loan_outcomes(reduced, info).collect().sort("loan_key")


def test_two_level_fold_matches_single_pass() -> None:
    panel = _panel()
    info = {"_mod_info": True, "_plan_info": True}
    single = _reduce([panel.group_by("loan_key").agg(loan_outcome_partial_aggs())], info)
    split = _reduce(
        [
            panel.filter(pl.col("reporting_period") < 202001)
            .group_by("loan_key")
            .agg(loan_outcome_partial_aggs()),
            panel.filter(pl.col("reporting_period") >= 202001)
            .group_by("loan_key")
            .agg(loan_outcome_partial_aggs()),
        ],
        info,
    )
    assert single.equals(split)

    a = single.filter(pl.col("loan_key") == "A").row(0, named=True)
    assert a["first_reporting_period"] == 201911
    assert a["last_reporting_period"] == 202002
    assert a["n_months_observed"] == 3
    assert a["last_upb"] == 0.0
    assert a["last_servicer_name"] == "S2"
    assert a["peak_delinquency_months"] == 2
    assert (a["ever_30"], a["ever_60"], a["ever_90"]) == (True, True, False)
    assert a["first_90_period"] is None
    assert a["ever_modified"] is True
    assert a["ever_forbearance"] is True
    assert a["terminated"] is True
    assert a["termination_period"] == 202002
    assert a["event_class"] == "voluntary_payoff"
    assert a["loan_age_at_termination"] == 4

    b = single.filter(pl.col("loan_key") == "B").row(0, named=True)
    # a disclosing agency: no flag ever seen means False, not null
    assert b["ever_modified"] is False
    assert b["ever_assistance_plan"] is False
    assert b["terminated"] is False
    assert b["termination_period"] is None
    assert b["event_type"] is None


def test_undisclosed_inputs_null_the_column_for_the_agency() -> None:
    panel = _panel().with_columns(
        pl.lit(None, dtype=pl.String).alias("modification_flag"),
        pl.lit(None, dtype=pl.String).alias("borrower_assistance_plan"),
    )
    partial = panel.group_by("loan_key").agg(loan_outcome_partial_aggs())
    info = partial.select(pl.col("_mod_info").any(), pl.col("_plan_info").any()).row(0, named=True)
    assert info == {"_mod_info": False, "_plan_info": False}
    out = _reduce([partial], info)
    assert out["ever_modified"].is_null().all()
    assert out["ever_forbearance"].is_null().all()
    assert out["ever_30"].to_list() == [True, False]  # delinquency is always disclosed


def test_months_between_period_and_date() -> None:
    df = pl.DataFrame({"p": [202002, 202101], "d": [date(2019, 11, 1), date(2019, 11, 1)]})
    assert df.select(_months_between(pl.col("p"), pl.col("d"))).to_series().to_list() == [3, 14]
