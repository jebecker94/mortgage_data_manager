"""Unit tests for census-tract key construction in the HMDA<->FHLB matcher.

``create_census_tract_string`` builds the 11-char GEOID and is the seam where the documented
AMA dotted-decimal tract bug lives: the FHLB ``CensusTractIdentifier`` is a float like
``5203.01`` that must be ``*100``-scaled to an integer BEFORE the GEOID is assembled, or a
plain integer cast truncates it and silently misplaces the tract digits.

``apply_post2018_filters`` is the other silent-corruption seam: on an already-joined frame it
maps the ``-99999`` sentinel to null, applies negated tolerance/enum filters (income, term, rate,
LTV, a DTI band that only fires for ``36 <= dti < 50``, purpose/gender contradictions), and
enforces a 1:1 ``(HMDAIndex, LoanCharacteristicsID)`` dedup. Each is a deterministic decision where
a wrong band, an unmapped sentinel, or a one-sided dedup yields a plausible-but-wrong crosswalk no
type checker catches. Every expected count below was observed live before being pinned.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_fhlb.matching import apply_post2018_filters
from mortgage_data_manager.matching.match_hmda_fhlb.utils import create_census_tract_string


def test_create_census_tract_string_preserves_leading_zeros():
    df = pl.LazyFrame({"s": [6, 1], "c": [37, 1], "t": [520301, 100]})
    out = create_census_tract_string(df, "s", "c", "t").collect()["census_tract"].to_list()
    assert out == ["06037520301", "01001000100"]
    assert all(len(x) == 11 for x in out)


def test_ama_tract_x100_scaling_regression():
    # FHLB CensusTractIdentifier 5203.01 must be *100-scaled to 520301 before the GEOID is built.
    raw = pl.LazyFrame(
        {"FIPSStateNumericCode": [6], "FIPSCountyCode": [37], "CensusTractIdentifier": [5203.01]}
    )
    fixed = raw.with_columns(
        (pl.col("CensusTractIdentifier") * 100)
        .round(0)
        .cast(pl.Int64)
        .alias("CensusTractIdentifier")
    )
    cols = ("FIPSStateNumericCode", "FIPSCountyCode", "CensusTractIdentifier")
    got = create_census_tract_string(fixed, *cols).collect()["census_tract"][0]
    assert got == "06037520301"

    # Anti-regression: skipping the *100 scaling truncates 5203.01 -> 5203 and yields a wrong
    # GEOID — exactly the bug that once zeroed HMDA tract joins nationwide.
    wrong = create_census_tract_string(raw, *cols).collect()["census_tract"][0]
    assert wrong == "06037005203"


# ---------------------------------------------------------------------------
# apply_post2018_filters — tolerance / enum / sentinel filters + 1:1 dedup.
# ---------------------------------------------------------------------------

# Exact HMDA + FHLB columns apply_post2018_filters references. Base values pass every
# filter (zero difference on each tolerance band, non-contradictory enums, unique keys)
# so overriding a single field isolates exactly one rule at a time.
_BASE_ROW = {
    # HMDA side (sentinel-cleaned numerics + enums)
    "income": 60000.0,
    "loan_term": 360.0,
    "interest_rate": 4.5,
    "combined_loan_to_value_ratio": 80.0,
    "debt_to_income_ratio": 40.0,
    "loan_purpose": 1,
    "loan_type": 1,  # conventional -> no financed-fee LTV allowance (see fee-aware test below)
    "applicant_sex": 1,
    "co_applicant_sex": 1,
    "loan_amount": 200000.0,
    # FHLB side
    "LoanPurposeType": 1,
    "TotalMonthlyIncomeAmount": 60000.0,
    "LoanAmortizationMaxTermMonths": 360.0,
    "NoteRatePercent": 4.5,
    "LTVRatioPercent": 80.0,
    "TotalDebtExpenseRatioPercent": 40.0,
    "Borrower1SexType": 1,
    "Borrower2SexType": 1,
    "NoteAmount": 200000.0,
    # match keys
    "HMDAIndex": "2020a_000000001",
    "LoanCharacteristicsID": 1,
}


def _joined_row(**overrides):
    return {**_BASE_ROW, **overrides}


def _survivors(rows, round_number=1):
    lf = pl.DataFrame(rows).lazy()
    return apply_post2018_filters(lf, round_number=round_number).collect()


def test_within_tolerance_row_survives():
    # A perfectly-agreeing joined pair passes every filter and the 1:1 dedup.
    out = _survivors([_joined_row()])
    assert out.height == 1
    assert out["HMDAIndex"].to_list() == ["2020a_000000001"]


@pytest.mark.parametrize(
    ("dti", "tde", "survives"),
    [
        (40.0, 45.0, False),  # in [36,50): 5-pt gap > dti_tol=1 -> DROPPED
        (49.0, 54.0, False),  # 49 still < 50 -> band fires -> DROPPED
        (60.0, 65.0, True),  # dti >= 50 -> band never fires -> SURVIVES
        (35.0, 30.0, True),  # dti < 36 -> band never fires -> SURVIVES
    ],
)
def test_dti_band_only_fires_within_36_to_50(dti, tde, survives):
    out = _survivors([_joined_row(debt_to_income_ratio=dti, TotalDebtExpenseRatioPercent=tde)])
    assert out.height == (1 if survives else 0)


@pytest.mark.parametrize("dti_value", [-99999.0, None])
def test_dti_sentinel_and_null_are_excluded_by_negation(dti_value):
    # -99999 maps to null; the DTI band is a *negated* predicate, so a null value
    # propagates to a null mask and the row is DROPPED (not kept). If the sentinel
    # were left as -99999 it would fall OUTSIDE [36,50) and wrongly survive.
    out = _survivors(
        [_joined_row(debt_to_income_ratio=dti_value, TotalDebtExpenseRatioPercent=40.0)]
    )
    assert out.height == 0


def test_real_out_of_band_dti_still_survives():
    # Flip side of the sentinel test: a genuine (non-null) out-of-band dti must NOT be
    # dropped even with a large TDE gap, proving the drop above is null-specific.
    out = _survivors([_joined_row(debt_to_income_ratio=25.0, TotalDebtExpenseRatioPercent=99.0)])
    assert out.height == 1


@pytest.mark.parametrize(
    ("loan_type", "survives"),
    [(2, True), (3, True), (4, True), (1, False)],
)
def test_ltv_fee_allowance_applies_to_government_loans_only(loan_type, survives):
    # AMA discloses the fee-INCLUSIVE LTV for government loans while HMDA reports the base
    # CLTV, so 80.0 vs 81.4 (= 80 x 1.0175, the financed FHA UFMIP) is a true pair on the
    # government programs and a miss on a conventional loan. The 1.4pp gap exceeds the round-1
    # ltv_tol of 1.05, so the allowance is the only thing that can rescue the row: extending it
    # to loan_type 1 would wave through conventional pairs that genuinely disagree on LTV.
    #
    # Only the government/conventional split is pinned here. At an 80 CLTV the FHA (1.0175),
    # VA (1.0215-1.036) and USDA (1.01) bands all overlap within the 1.05pp fee tolerance, so
    # this fixture deliberately does NOT claim the factors discriminate between programs.
    out = _survivors(
        [_joined_row(loan_type=loan_type, combined_loan_to_value_ratio=80.0, LTVRatioPercent=81.4)]
    )
    assert (out.height == 1) is survives


def test_dedup_drops_both_sides_of_a_key_collision():
    # 1:1 rule: two rows sharing HMDAIndex are BOTH removed (count != 1); only the
    # uniquely-keyed row survives. A one-sided drop here would silently double-count.
    rows = [
        _joined_row(HMDAIndex="H1", LoanCharacteristicsID=1),
        _joined_row(HMDAIndex="H1", LoanCharacteristicsID=2),
        _joined_row(HMDAIndex="H3", LoanCharacteristicsID=3),
    ]
    out = _survivors(rows)
    assert out.select("HMDAIndex", "LoanCharacteristicsID").rows() == [("H3", 3)]


@pytest.mark.parametrize(
    ("note_amount", "survives"),
    [(210000.0, True), (220000.0, False)],  # amount_tol=15000: |200k - note| <= 15k
)
def test_round2_amount_tolerance_only_applies_in_round2(note_amount, survives):
    # The extra amount band exists only in round 2: a 10k gap passes, a 20k gap fails.
    out = _survivors([_joined_row(NoteAmount=note_amount)], round_number=2)
    assert out.height == (1 if survives else 0)


@pytest.mark.parametrize("boundary_matched", [True, False])
def test_fhlb_side_counts_each_loan_once(boundary_matched):
    # Regression: the post-2018 acquisitions file copies every exact-$10k loan
    # (i_Duplicate = 1) so it can meet HMDA in either amount bin. Validation used that
    # file as the loan universe and printed 57.0% (317,673 / 557,526) for 270,018 pairs
    # of 472,444 loans. A loan and its copy are one loan on both sides of the rate.
    from mortgage_data_manager.matching.match_hmda_fhlb.validation import (
        ERAS,
        compute_overall_stats,
        era_sides,
    )

    post = next(e for e in ERAS if e.key == "post2018")
    fhlb = pl.DataFrame(
        {
            "LoanCharacteristicsID": [1, 1, 2, 3],
            "NoteAmount": [200_000, 200_000, 215_500, 187_300],
            "i_Duplicate": [0, 1, 0, 0],
        }
    )
    matched = [1, 2] if boundary_matched else [2]
    matches = pl.DataFrame(
        {"LoanCharacteristicsID": matched, "HMDAIndex": ["a", "b"][: len(matched)]}
    )
    stats = compute_overall_stats(matches, era_sides(post, matches, None, fhlb))
    assert stats["n_fhlb"] == 3
    assert stats["n_fhlb_matched"] == len(matched)
