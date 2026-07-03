"""Unit tests for the deterministic decisions in the post-2018 FHFA<->HMDA matcher.

``assign_pre2018_hmda_index`` must be idempotent (leave an existing HMDAIndex alone) and,
when absent, synthesize the exact ``{year}d_{respondent}_{agency}_{seq}`` key the validation
loader also builds — a format drift silently breaks pre-2018 crosswalk joins.

The remaining tests pin the post-merge dedup/filter rules that turn a many-to-many join into a
1:1 crosswalk. Each is a silent-corruption guard: a bug here produces a plausible-but-wrong pairing
that no type checker catches.

- ``apply_uniqueness`` — a collision on *either* the HMDAIndex or the composite FHFA key
  ``(fhfa_year, enterprise_flag, record_number)`` must drop *all* colliding rows, never keep one.
- ``apply_income_tiebreak`` — breaks same-fingerprint ties by closest borrower income, but only
  when the winning ``|income - borrower_annual_income|`` is within $1k; already-unique rows always
  survive regardless of income.
- ``apply_quality_selection`` — when a key has any tight (rate_diff<=0.01) candidate, its >0.05 then
  non-tight candidates are dropped; a null rate_diff counts as tight.
- ``apply_post_merge_filters`` — the loan-purpose code remap across the FHFA (1/2/7) vs HMDA
  (1/31/32) code spaces must block Purchase<->Refi/CashOut cross-category pairings.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_fhfa_hmda.match_post2018 import (
    apply_income_tiebreak,
    apply_post_merge_filters,
    apply_quality_selection,
    apply_uniqueness,
)
from mortgage_data_manager.matching.match_fhfa_hmda.round_config import PostMergeFilters
from mortgage_data_manager.matching.match_fhfa_hmda.utils import assign_pre2018_hmda_index


def test_synthesizes_index_from_identity_tuple():
    lf = pl.LazyFrame(
        {
            "activity_year": [2015],
            "respondent_id": ["1234"],
            "agency_code": [7],
            "sequence_number": [42],
        }
    )
    assert assign_pre2018_hmda_index(lf).collect()["HMDAIndex"].to_list() == ["2015d_1234_7_42"]


def test_idempotent_when_index_present():
    lf = pl.LazyFrame({"HMDAIndex": ["already"], "activity_year": [2015]})
    out = assign_pre2018_hmda_index(lf).collect()
    assert out["HMDAIndex"].to_list() == ["already"]
    assert out.columns == ["HMDAIndex", "activity_year"]  # unchanged; no synthesized column added


def test_apply_uniqueness_drops_all_colliding_rows():
    # h1/rec100 is 1:1 -> kept. h2 collides on HMDAIndex (two FHFA records) -> both dropped.
    # rec400 collides on the composite FHFA key (two HMDA loans) -> both dropped. A collision on
    # *either* side must remove *all* participants; keeping an arbitrary winner is the silent bug.
    merged = pl.LazyFrame(
        {
            "HMDAIndex": ["h1", "h2", "h2", "h4", "h5"],
            "fhfa_year": [2020, 2020, 2020, 2020, 2020],
            "enterprise_flag": [1, 1, 1, 1, 1],
            "record_number": [100, 200, 300, 400, 400],
        }
    )
    out = apply_uniqueness(merged).collect()
    assert out["HMDAIndex"].to_list() == ["h1"]
    assert out["record_number"].to_list() == [100]


def test_apply_income_tiebreak_keeps_strong_and_alreadyunique():
    # fhfa key (…,100) has two HMDA candidates: h1 diff=500 (<=1000, the per-key min) wins; h2
    # diff=5000 is not the fhfa-side min -> dropped. fhfa key (…,200) ties h3/h4 but the closest
    # diff (10000) exceeds the $1k gate -> both dropped (left for a later cross-year round). h5 is
    # already 1:1 (n_hmda==n_fhfa==1) so it survives despite a huge income gap.
    merged = pl.LazyFrame(
        {
            "HMDAIndex": ["h1", "h2", "h3", "h4", "h5"],
            "fhfa_year": [2020, 2020, 2020, 2020, 2020],
            "enterprise_flag": [1, 1, 1, 1, 1],
            "record_number": [100, 100, 200, 200, 300],
            "income": [100000, 100000, 50000, 50000, 10000],
            "borrower_annual_income": [100500, 105000, 60000, 70000, 9000000],
        }
    )
    out = apply_income_tiebreak(merged).collect()
    assert sorted(out["HMDAIndex"].to_list()) == ["h1", "h5"]


def test_apply_quality_selection_prefers_tight_and_treats_null_as_tight():
    # h1 has a tight candidate (0.005): its good-but-not-tight (0.03) and non-good (0.10) siblings are
    # both dropped, leaving only the tight one. h10's null rate_diff counts as tight, so its 0.10
    # sibling is dropped too. Result: the tight winner per key (record 100) and the null one (1000).
    merged = pl.LazyFrame(
        {
            "HMDAIndex": ["h1", "h1", "h1", "h10", "h10"],
            "fhfa_year": [2020, 2020, 2020, 2020, 2020],
            "enterprise_flag": [1, 1, 1, 1, 1],
            "record_number": [100, 200, 300, 1000, 1100],
            "rate_diff": [0.005, 0.03, 0.10, None, 0.10],
        }
    )
    out = apply_quality_selection(merged).collect()
    assert sorted(out["record_number"].to_list()) == [100, 1000]
    # The surviving h1 row is the tight one; the null-rate h10 row survives as tight-by-fill.
    assert out.filter(pl.col("HMDAIndex") == "h1")["rate_diff"].to_list() == [0.005]
    assert out.filter(pl.col("HMDAIndex") == "h10")["rate_diff"].to_list() == [None]


@pytest.mark.parametrize(
    ("strict", "expected"),
    [
        # Non-strict blocks Purchase<->Refi/CashOut but *allows* Refi<->CashOut (row G kept).
        (False, ["A", "F", "G"]),
        # Strict requires an exact category, so FHFA Refi (2) vs HMDA CashOut (32) (row G) drops.
        (True, ["A", "F"]),
    ],
)
def test_apply_post_merge_filters_loan_purpose_remap(strict: bool, expected: list[str]):
    # loan_purpose_right is FHFA (1=Purchase, 2=Refi, 7=CashOut); loan_purpose is HMDA
    # (1=Purchase, 31=Refi, 32=CashOut). B/C (FHFA purchase vs HMDA refi/cashout) and D/E
    # (FHFA refi/cashout vs HMDA purchase) are cross-category and must always drop.
    merged = pl.LazyFrame(
        {
            "rid": ["A", "B", "C", "D", "E", "F", "G"],
            "loan_purpose_right": [1, 1, 1, 2, 7, 2, 2],
            "loan_purpose": [1, 31, 32, 1, 1, 31, 32],
            # rate/term kept well within tolerance so only the purpose filter can drop rows.
            "interest_rate": [3.0] * 7,
            "interest_rate_at_origination": [3.0] * 7,
            "loan_term": [360] * 7,
            "loan_term_right": [360] * 7,
        }
    )
    config = PostMergeFilters(
        rate_tolerance=10.0,
        term_tolerance=1000,
        require_demographics=False,
        require_current_year_orig=False,
        filter_loan_purpose=not strict,
        filter_loan_purpose_strict=strict,
        filter_dti=False,
    )
    out = apply_post_merge_filters(merged, config).collect()
    assert sorted(out["rid"].to_list()) == expected
