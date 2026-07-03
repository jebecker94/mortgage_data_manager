"""Unit tests for the MBS<->UMBS match-statistics denominator math.

``compute_match_statistics`` is the headline validation number for the MBS-UMBS crosswalk:
matched loans / total loans, computed by set-membership of each side's *unique* loan ids
against the crosswalk id columns. It is a silent-corruption seam because every step is a
plausible-but-wrong-if-broken decision that no type checker catches:

- the denominator is a ``.unique()`` count, so duplicate monthly rows must NOT inflate totals;
- matched count is the intersection of the *in-window* loan universe with the crosswalk, NOT
  ``len(crosswalk)`` — a crosswalk pair whose loan falls outside the window must not be counted;
- UMBS ids are stored ``Int64`` while the crosswalk stores strings, so the ``cast(Utf8)`` on
  both the id series and the membership set is load-bearing (drop it and matches silently -> 0);
- the rate has a zero-denominator guard (empty window -> ``0``, never ``ZeroDivisionError``);
- the 1:1 design invariant ``n_matched_pairs == n_matched_mbs`` must hold.

All expected values below were observed by running the function on these exact fixtures.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_mbs_umbs.validation import (
    compute_match_statistics,
)

MBS_ID = "Loan Sequence Number (MBS)"
UMBS_ID = "Loan Sequence Number (UMBS)"


def _crosswalk(mbs_ids: list[str], umbs_ids: list[str]) -> pl.DataFrame:
    """Two-column crosswalk keyed exactly as ``compute_match_statistics`` expects."""
    return pl.DataFrame({MBS_ID: mbs_ids, UMBS_ID: umbs_ids})


def test_basic_counts_rate_and_dedup() -> None:
    # Crosswalk pairs (M1, 100) and (M2, 200). The MBS side is monthly-style: M1 appears twice,
    # so the unique loan universe is {M1, M2, M3} (3), of which 2 are matched -> 2/3. UMBS ids
    # are Int64 and must be cast to Utf8 to match the string crosswalk -> {100, 200} matched.
    crosswalk = _crosswalk(["M1", "M2"], ["100", "200"])
    mbs_data = pl.LazyFrame({"Loan Identifier": ["M1", "M1", "M2", "M3"]})
    umbs_data = pl.DataFrame({"Loan Identifier": pl.Series([100, 200, 300], dtype=pl.Int64)})

    stats = compute_match_statistics(crosswalk, mbs_data, umbs_data)

    assert stats["n_total_mbs"] == 3  # duplicate M1 row collapsed by .unique()
    assert stats["n_matched_mbs"] == 2
    assert stats["mbs_match_rate"] == pytest.approx(2 / 3)

    # Int64 UMBS ids matched the string crosswalk only because both were cast to Utf8.
    assert stats["n_total_umbs"] == 3
    assert stats["n_matched_umbs"] == 2
    assert stats["umbs_match_rate"] == pytest.approx(2 / 3)


def test_matched_is_intersection_not_crosswalk_length() -> None:
    # Crosswalk carries 3 pairs, but MX / 999 fall outside the in-window loan universe. Matched
    # counts must be the intersection with the in-window loans (2), NOT len(crosswalk) == 3.
    crosswalk = _crosswalk(["M1", "M2", "MX"], ["100", "200", "999"])
    mbs_data = pl.LazyFrame({"Loan Identifier": ["M1", "M2", "M3"]})
    umbs_data = pl.DataFrame({"Loan Identifier": pl.Series([100, 200, 300], dtype=pl.Int64)})

    stats = compute_match_statistics(crosswalk, mbs_data, umbs_data)

    assert len(crosswalk) == 3
    assert stats["n_matched_pairs"] == 2  # bounded by the in-window universe, not the crosswalk
    assert stats["n_matched_mbs"] == 2
    assert stats["n_matched_umbs"] == 2


@pytest.mark.parametrize(
    ("mbs_ids", "umbs_ids"),
    [
        (["M1", "M2"], ["100", "200"]),  # every MBS loan matched
        (["M1", "M2", "M3"], ["100"]),  # 2 of 3 matched
        (["MX"], ["999"]),  # nothing matched
    ],
)
def test_one_to_one_pairs_identity(mbs_ids: list[str], umbs_ids: list[str]) -> None:
    # The 1:1 design invariant: in-window matched pairs == in-window matched MBS loans.
    crosswalk = _crosswalk(["M1", "M2"], ["100", "200"])
    mbs_data = pl.LazyFrame({"Loan Identifier": mbs_ids})
    umbs_data = pl.DataFrame(
        {"Loan Identifier": pl.Series([int(u) for u in umbs_ids], dtype=pl.Int64)}
    )

    stats = compute_match_statistics(crosswalk, mbs_data, umbs_data)

    assert stats["n_matched_pairs"] == stats["n_matched_mbs"]


@pytest.mark.parametrize("side", ["mbs", "umbs"])
def test_empty_window_rate_is_zero_no_zero_division(side: str) -> None:
    # An empty in-window universe must yield rate 0 via the guard, never a ZeroDivisionError.
    crosswalk = _crosswalk(["M1", "M2"], ["100", "200"])
    empty_mbs = pl.LazyFrame({"Loan Identifier": pl.Series([], dtype=pl.Utf8)})
    full_mbs = pl.LazyFrame({"Loan Identifier": ["M1", "M2"]})
    empty_umbs = pl.DataFrame({"Loan Identifier": pl.Series([], dtype=pl.Int64)})
    full_umbs = pl.DataFrame({"Loan Identifier": pl.Series([100, 200], dtype=pl.Int64)})

    mbs_data = empty_mbs if side == "mbs" else full_mbs
    umbs_data = empty_umbs if side == "umbs" else full_umbs

    stats = compute_match_statistics(crosswalk, mbs_data, umbs_data)

    if side == "mbs":
        assert stats["n_total_mbs"] == 0
        assert stats["mbs_match_rate"] == 0
    else:
        assert stats["n_total_umbs"] == 0
        assert stats["umbs_match_rate"] == 0
