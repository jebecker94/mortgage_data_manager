"""Regression tests for FHFA Freddie term-reporting conventions (matching/terms.py).

Locked to the measured evidence: FHFA reports Freddie terms at
{identity | ceiling-standard | product-envelope}, covering 99.998% of 14.0M
identity-known pairs. The former nearest-value mapping mis-mapped 144->120
(observed: 9,449 pairs land on 180, 10 on 120) — these tests pin the ceiling
behavior so it cannot regress.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.terms import (
    FREDDIE_PRODUCT_UPBIN,
    STANDARD_TERMS,
    TERM_TO_FHFA_BIN,
    freddie_term_diff_expr,
    term_ceiling,
    term_product,
)


def test_standard_terms_ascending() -> None:
    assert STANDARD_TERMS == sorted(STANDARD_TERMS)
    assert STANDARD_TERMS == [120, 180, 240, 360, 480]


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        (120, 120),  # standard terms map to themselves
        (180, 180),
        (240, 240),
        (360, 360),
        (480, 480),
        (144, 180),  # THE regression: ceiling, not nearest (nearest would be 120)
        (132, 180),
        (156, 180),
        (216, 240),  # ceiling == nearest here
        (228, 240),
        (264, 360),  # ceiling, not nearest (nearest would be 240)
        (276, 360),
        (288, 360),
        (300, 360),
        (348, 360),
        (361, 480),  # just over 360 -> next standard
        (500, 480),  # capped at the top standard term
    ],
)
def test_term_ceiling_mapping(term: int, expected: int) -> None:
    assert term_ceiling(term) == expected
    assert TERM_TO_FHFA_BIN[term] == expected


def test_product_envelope() -> None:
    assert term_product(120) == 180
    assert term_product(180) == 180
    assert term_product(240) == 360
    assert term_product(360) == 360
    assert term_product(361) == 480
    # Only 120 and 240 differ from their own value among standard terms
    assert FREDDIE_PRODUCT_UPBIN == {120: 180, 240: 360}


def _diff(true_term: int | None, fhfa_term: int | None, ent: int) -> int | None:
    df = pl.DataFrame(
        {
            "loan_term": pl.Series([true_term], dtype=pl.Int64),
            "loan_term_fhfa": pl.Series([fhfa_term], dtype=pl.Int64),
            "enterprise_flag": pl.Series([ent], dtype=pl.Int8),
        }
    )
    return df.select(freddie_term_diff_expr("loan_term", "loan_term_fhfa")).item()


@pytest.mark.parametrize(
    ("true_term", "fhfa_term", "ent", "expected"),
    [
        (240, 240, 2, 0),  # identity
        (240, 360, 2, 0),  # product-envelope up-bin: same loan
        (120, 180, 2, 0),  # product-envelope up-bin: same loan
        (144, 180, 2, 0),  # ceiling bin: same loan
        (300, 360, 2, 0),  # ceiling bin: same loan
        (240, 360, 1, 120),  # Fannie: conventions do NOT apply
        (180, 240, 2, 60),  # 180 never up-bins — keep the real difference
        (240, 180, 2, 60),  # down-bin is not a convention
        (360, 480, 2, 120),  # 360 never up-bins
        (None, 360, 2, None),  # nulls propagate (caller treats null as pass)
    ],
)
def test_freddie_term_diff_expr(
    true_term: int | None, fhfa_term: int | None, ent: int, expected: int | None
) -> None:
    assert _diff(true_term, fhfa_term, ent) == expected
