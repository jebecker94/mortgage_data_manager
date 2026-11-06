"""Tests for the shared candidate-pair resolution primitives (matching/resolve.py).

The load-bearing tests here are the equivalence ones. Each of the four rules had between
two and a dozen hand-rolled implementations across the workflows, and every one of those
call sites feeds a production crosswalk, so a migration is only safe if the primitive
reproduces the incumbent exactly. The reference implementations below are transcribed
from the code being replaced and the equivalence is asserted over randomized frames with
heavy score ties, which is where the four rules diverge from one another.
"""

from __future__ import annotations

import random

import polars as pl
import pytest

from mortgage_data_manager.matching.resolve import (
    assert_one_to_one,
    drop_ambiguous,
    margin_best,
    mutual_best,
    unique_best,
)

# ======================================================================================
# Reference implementations, transcribed from the call sites being replaced
# ======================================================================================


def _ref_drop_ambiguous(df: pl.DataFrame) -> pl.DataFrame:
    """``_keep_unique_matches`` (match_hmda_fha/matching_post2018.py)."""
    df = df.with_columns(
        [
            pl.len().over("h").alias("CountLoanFHA"),
            pl.len().over("g").alias("CountLoanHMDA"),
        ]
    )
    df = df.filter((pl.col("CountLoanFHA") == 1) & (pl.col("CountLoanHMDA") == 1))
    return df.drop(["CountLoanFHA", "CountLoanHMDA"])


def _ref_pin_tier_semi(cand: pl.DataFrame) -> pl.DataFrame:
    """The old pin-tier rule, in the group_by/semi-join spelling."""
    ok_h = cand.group_by("h").agg(pl.len().alias("n")).filter(pl.col("n") == 1)
    ok_g = cand.group_by("g").agg(pl.len().alias("n")).filter(pl.col("n") == 1)
    return cand.join(ok_h.select("h"), on="h", how="semi").join(
        ok_g.select("g"), on="g", how="semi"
    )


def _ref_mutual_best(df: pl.DataFrame) -> pl.DataFrame:
    """The dense-rank block shared by fhfa_mbs and fhlb_mbs."""
    df = df.with_columns(
        [
            pl.col("s").rank(method="dense").over("h").alias("rank_in_h"),
            pl.col("s").rank(method="dense").over("g").alias("rank_in_g"),
        ]
    )
    return df.filter((pl.col("rank_in_h") == 1) & (pl.col("rank_in_g") == 1)).drop(
        ["rank_in_h", "rank_in_g"]
    )


def _ref_unique_best(cand: pl.DataFrame) -> pl.DataFrame:
    """``_unique_best_1to1`` (matching_fha.py), formerly also the VA/USDA matcher's.

    Verbatim, including the trailing sort-and-fixpoint loop, which
    :func:`test_unique_best_fixpoint_loop_is_vestigial` shows can never remove a row.
    """
    cand = cand.with_columns(
        pl.col("s").max().over("h").alias("_hmax"),
        (pl.col("s") == pl.col("s").max().over("h")).sum().over("h").alias("_hn"),
    ).filter((pl.col("s") == pl.col("_hmax")) & (pl.col("_hn") == 1))
    cand = cand.with_columns(
        pl.col("s").max().over("g").alias("_gmax"),
        (pl.col("s") == pl.col("s").max().over("g")).sum().over("g").alias("_gn"),
    ).filter((pl.col("s") == pl.col("_gmax")) & (pl.col("_gn") == 1))
    cand = cand.drop(["_hmax", "_hn", "_gmax", "_gn"]).sort("s", descending=True)
    prev = -1
    while len(cand) != prev:
        prev = len(cand)
        cand = cand.unique(subset=["g"], keep="first")
        cand = cand.unique(subset=["h"], keep="first")
    return cand


# ======================================================================================
# Fixtures
# ======================================================================================


def _random_pairs(seed: int, n: int, key_space: int, score_space: int) -> pl.DataFrame:
    """Candidate pairs with deliberately heavy key collisions and score ties."""
    rng = random.Random(seed)
    return pl.DataFrame(
        {
            "h": [f"h{rng.randint(0, key_space)}" for _ in range(n)],
            "g": [f"g{rng.randint(0, key_space)}" for _ in range(n)],
            "s": [float(rng.randint(0, score_space)) for _ in range(n)],
        }
    ).unique(subset=["h", "g"])  # a pair appears at most once, as after a join


TRIALS = [(seed, 200, 40, 3) for seed in range(25)] + [(seed, 60, 8, 1) for seed in range(25, 40)]


def _sorted(df: pl.DataFrame) -> pl.DataFrame:
    """Row-order-independent comparison key."""
    return df.select(["h", "g", "s"]).sort(["h", "g", "s"])


# ======================================================================================
# Equivalence with the implementations being replaced
# ======================================================================================


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_drop_ambiguous_matches_reference(seed: int, n: int, keys: int, scores: int) -> None:
    pairs = _random_pairs(seed, n, keys, scores)
    assert _sorted(drop_ambiguous(pairs, "h", "g")).equals(_sorted(_ref_drop_ambiguous(pairs)))


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_drop_ambiguous_matches_semi_join_spelling(
    seed: int, n: int, keys: int, scores: int
) -> None:
    """The group_by/semi-join form is the same rule as the count-over form."""
    pairs = _random_pairs(seed, n, keys, scores)
    assert _sorted(drop_ambiguous(pairs, "h", "g")).equals(_sorted(_ref_pin_tier_semi(pairs)))


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_mutual_best_matches_reference(seed: int, n: int, keys: int, scores: int) -> None:
    pairs = _random_pairs(seed, n, keys, scores)
    assert _sorted(mutual_best(pairs, "s", "h", "g")).equals(_sorted(_ref_mutual_best(pairs)))


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_unique_best_matches_reference(seed: int, n: int, keys: int, scores: int) -> None:
    pairs = _random_pairs(seed, n, keys, scores)
    got = unique_best(pairs, "s", "h", "g", descending=True)
    assert _sorted(got).equals(_sorted(_ref_unique_best(pairs)))


# ======================================================================================
# The properties each rule promises
# ======================================================================================


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_strict_rules_are_one_to_one(seed: int, n: int, keys: int, scores: int) -> None:
    """drop_ambiguous and unique_best need no follow-up deduplication."""
    pairs = _random_pairs(seed, n, keys, scores)
    for got in (drop_ambiguous(pairs, "h", "g"), unique_best(pairs, "s", "h", "g")):
        assert got["h"].n_unique() == len(got)
        assert got["g"].n_unique() == len(got)


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_unique_best_fixpoint_loop_is_vestigial(seed: int, n: int, keys: int, scores: int) -> None:
    """The sort + while loop in the incumbent ``_unique_best`` can never remove a row.

    Both strict filters leave at most one row per key value, so the frame reaching the
    loop is already 1:1 and the loop exits on its first comparison. Dropping it in
    :func:`unique_best` is therefore behaviour-preserving, not a change to validate --
    and the ``unique(keep="first")`` output-order hazard it would otherwise carry (polars
    does not preserve row order through ``unique``) never bites.
    """
    pairs = _random_pairs(seed, n, keys, scores)
    before_loop = unique_best(pairs, "s", "h", "g", descending=True)
    after_loop = _ref_unique_best(pairs)
    assert len(before_loop) == len(after_loop)


def test_unique_best_is_not_a_stricter_mutual_best() -> None:
    """Pins the sequential-vs-mutual distinction the two rules actually encode.

    ``unique_best`` filters left then right, so the right-side contest only sees rows that
    already won on the left. Here ``g1`` strictly prefers ``h2``, but ``h2`` is spoken for
    by ``g2``, so the sequential rule still claims ``(h1, g1)`` while the mutual rule
    rejects it. Neither output contains the other. The incumbent implementations this
    module replaces have the sequential semantics (see the equivalence tests above), so
    this is a documented property, not a defect to fix.
    """
    pairs = pl.DataFrame({"h": ["h1", "h2", "h2"], "g": ["g1", "g1", "g2"], "s": [5.0, 1.0, 0.0]})
    sequential = unique_best(pairs, "s", "h", "g")
    mutual = mutual_best(pairs, "s", "h", "g")
    assert sorted(zip(sequential["h"], sequential["g"], strict=True)) == [
        ("h1", "g1"),
        ("h2", "g2"),
    ]
    assert sorted(zip(mutual["h"], mutual["g"], strict=True)) == [("h2", "g2")]


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_unique_best_drops_every_tied_contest(seed: int, n: int, keys: int, scores: int) -> None:
    """Whatever unique_best keeps, it never keeps a pair whose key had a tied winner."""
    pairs = _random_pairs(seed, n, keys, scores)
    kept = unique_best(pairs, "s", "h", "g")
    tied_left = (
        pairs.filter(pl.col("s") == pl.col("s").min().over("h"))
        .filter(pl.len().over("h") > 1)
        .select("h")
        .unique()
    )
    assert len(kept.join(tied_left, on="h", how="semi")) == 0


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_rules_are_row_order_independent(seed: int, n: int, keys: int, scores: int) -> None:
    """The property that motivated retiring the sort-based rule.

    Every rule here decides from values alone, so shuffling the candidate rows cannot change
    which pairs survive. A ``sort(score).group_by(key).first()`` rule fails this: it resolves
    a tie by whichever row landed first, which polars does not fix across runs or threads.
    The since-retired direct HMDA-UMBS matcher used that rule and disagreed with itself on
    ~23% of its pairs between identical runs.
    """
    pairs = _random_pairs(seed, n, keys, scores)
    shuffled = pairs.sample(fraction=1.0, shuffle=True, seed=seed + 991)
    for rule in (
        lambda f: drop_ambiguous(f, "h", "g"),
        lambda f: mutual_best(f, "s", "h", "g"),
        lambda f: unique_best(f, "s", "h", "g"),
        lambda f: drop_ambiguous(mutual_best(f, "s", "h", "g"), "h", "g"),
    ):
        assert _sorted(rule(pairs)).equals(_sorted(rule(shuffled)))


def test_mutual_best_then_drop_ambiguous_is_one_to_one() -> None:
    """mutual_best -> drop_ambiguous drops ties instead of picking."""
    # h1 has two candidates tied at the winning score; both are also best for their own g.
    pairs = pl.DataFrame({"h": ["h1", "h1", "h2"], "g": ["g1", "g2", "g3"], "s": [1.0, 1.0, 2.0]})
    kept = drop_ambiguous(mutual_best(pairs, "s", "h", "g"), "h", "g")
    assert sorted(zip(kept["h"], kept["g"], strict=True)) == [("h2", "g3")]
    assert kept["h"].n_unique() == len(kept)
    assert kept["g"].n_unique() == len(kept)


def test_mutual_best_ties_are_not_one_to_one() -> None:
    """The documented reason callers follow mutual_best with drop_ambiguous."""
    pairs = pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"], "s": [1.0, 1.0]})
    assert len(mutual_best(pairs, "s", "h", "g")) == 2
    assert len(unique_best(pairs, "s", "h", "g")) == 0
    assert len(drop_ambiguous(mutual_best(pairs, "s", "h", "g"), "h", "g")) == 0


def test_descending_flips_which_score_wins() -> None:
    pairs = pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"], "s": [1.0, 9.0]})
    assert unique_best(pairs, "s", "h", "g")["g"].to_list() == ["x"]
    assert unique_best(pairs, "s", "h", "g", descending=True)["g"].to_list() == ["y"]


# ======================================================================================
# margin_best -- a new rule with no incumbent to transcribe, so pinned by its contract
# ======================================================================================


def test_margin_best_refuses_a_winner_by_a_hair() -> None:
    pairs = pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"], "s": [3.0, 2.0]})
    assert margin_best(pairs, "s", "h", "g", margin=2, descending=True).is_empty()
    assert margin_best(pairs, "s", "h", "g", margin=1, descending=True)["g"].to_list() == ["x"]


def test_margin_best_lower_is_better_by_default() -> None:
    pairs = pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"], "s": [0.1, 0.9]})
    assert margin_best(pairs, "s", "h", "g", margin=0.5)["g"].to_list() == ["x"]


def test_margin_best_sole_candidate_needs_no_margin() -> None:
    """A key's only candidate qualifies on that side whatever it scores, null included."""
    pairs = pl.DataFrame(
        {"h": ["a"], "g": ["x"], "s": [None]},
        schema={"h": pl.String, "g": pl.String, "s": pl.Float64},
    )
    assert len(margin_best(pairs, "s", "h", "g", margin=1)) == 1


def test_margin_best_must_qualify_on_both_sides() -> None:
    """'a' clearly prefers x, but x is contested by 'b' at nearly the same score."""
    pairs = pl.DataFrame({"h": ["a", "a", "b"], "g": ["x", "y", "x"], "s": [5.0, 1.0, 4.5]})
    assert margin_best(pairs, "s", "h", "g", margin=2, descending=True).is_empty()


def test_margin_best_null_rivals_offer_no_margin() -> None:
    pairs = pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"], "s": [1.0, None]})
    assert margin_best(pairs, "s", "h", "g", margin=0.5, descending=True).is_empty()


def test_margin_best_rejects_a_non_positive_margin() -> None:
    pairs = pl.DataFrame({"h": ["a"], "g": ["x"], "s": [1.0]})
    with pytest.raises(ValueError, match="margin"):
        margin_best(pairs, "s", "h", "g", margin=0)


@pytest.mark.parametrize(("seed", "n", "keys", "scores"), TRIALS)
def test_margin_best_is_one_to_one_and_inside_mutual_best(
    seed: int, n: int, keys: int, scores: int
) -> None:
    """Every survivor is a strict best on both sides, so it is also a mutual best."""
    pairs = _random_pairs(seed, n, keys, scores)
    kept = margin_best(pairs, "s", "h", "g", margin=1)
    assert kept["h"].n_unique() == len(kept) == kept["g"].n_unique()
    assert kept.join(mutual_best(pairs, "s", "h", "g"), on=["h", "g"], how="anti").is_empty()


def test_margin_best_lazy_and_eager_agree() -> None:
    pairs = _random_pairs(7, 150, 30, 2)
    eager = margin_best(pairs, "s", "h", "g", margin=1)
    lazy = margin_best(pairs.lazy(), "s", "h", "g", margin=1)
    assert isinstance(lazy, pl.LazyFrame)
    assert _sorted(eager).equals(_sorted(lazy.collect()))


# ======================================================================================
# Calling conventions
# ======================================================================================


@pytest.mark.parametrize(
    "rule",
    [
        lambda f: drop_ambiguous(f, "h", "g"),
        lambda f: mutual_best(f, "s", "h", "g"),
        lambda f: unique_best(f, "s", "h", "g"),
    ],
)
def test_lazy_and_eager_agree(rule) -> None:  # noqa: ANN001
    """Each rule returns the type it was given, with the same contents."""
    pairs = _random_pairs(7, 150, 30, 2)
    eager = rule(pairs)
    lazy = rule(pairs.lazy())
    assert isinstance(eager, pl.DataFrame)
    assert isinstance(lazy, pl.LazyFrame)
    assert _sorted(eager).equals(_sorted(lazy.collect()))


def test_multi_column_keys() -> None:
    """A composite key (the FHFA record triple) behaves as one key."""
    pairs = pl.DataFrame(
        {
            "h": ["a", "a", "b"],
            "year": [2020, 2020, 2021],
            "ent": [1, 2, 1],
            "rec": [10, 10, 11],
            "s": [1.0, 2.0, 3.0],
        }
    )
    # 'a' is ambiguous on the left; the right triple differs on ent, so both are unique there.
    got = drop_ambiguous(pairs, "h", ["year", "ent", "rec"])
    assert got["h"].to_list() == ["b"]


def test_rules_add_no_columns() -> None:
    pairs = _random_pairs(3, 100, 20, 2)
    for got in (
        drop_ambiguous(pairs, "h", "g"),
        mutual_best(pairs, "s", "h", "g"),
        unique_best(pairs, "s", "h", "g"),
    ):
        assert got.columns == pairs.columns


def test_empty_frame_is_handled() -> None:
    empty = pl.DataFrame(schema={"h": pl.String, "g": pl.String, "s": pl.Float64})
    for got in (
        drop_ambiguous(empty, "h", "g"),
        mutual_best(empty, "s", "h", "g"),
        unique_best(empty, "s", "h", "g"),
    ):
        assert len(got) == 0


# ======================================================================================
# assert_one_to_one
# ======================================================================================


def test_assert_one_to_one_passes_and_returns_the_frame() -> None:
    df = pl.DataFrame({"h": ["a", "b"], "g": ["x", "y"]})
    assert assert_one_to_one(df, "h", "g") is df


@pytest.mark.parametrize(
    ("frame", "side"),
    [
        (pl.DataFrame({"h": ["a", "a"], "g": ["x", "y"]}), "left"),
        (pl.DataFrame({"h": ["a", "b"], "g": ["x", "x"]}), "right"),
    ],
)
def test_assert_one_to_one_names_the_offending_side(frame: pl.DataFrame, side: str) -> None:
    with pytest.raises(ValueError, match=f"{side} key"):
        assert_one_to_one(frame, "h", "g", context="test crosswalk")
