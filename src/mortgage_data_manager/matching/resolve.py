"""Candidate-pair resolution: reducing scored candidate pairs to a one-to-one crosswalk.

Every matching workflow ends the same way. A join has produced a frame of candidate
pairs, each row carrying a left-side loan key and a right-side loan key, and that frame
has to be reduced to pairs that are one-to-one on both sides before it can be written as
a crosswalk. Four rules are in use, and they differ in what they do with a loan that has
several surviving candidates:

- `drop_ambiguous` — no score. Keep a pair only when neither of its keys appears on
  any other row. Contested loans are abandoned, to be picked up by a later round.
- `mutual_best` — keep a pair that is best-ranked for both of its keys, judged
  against every candidate. Score ties are kept, so the result is not necessarily
  one-to-one; a crosswalk caller must follow it with `drop_ambiguous`, which drops
  the tied rows.
- `unique_best` — keep a pair that is the *strictly unique* best for its left key,
  then, among those survivors, for its right key. Ties are dropped rather than kept, so
  the result is one-to-one on its own.
- `margin_best` — keep a pair that leads every rival by at least a stated margin,
  judged on both keys at once. A winner by a hair is refused as well as a tie, for
  workflows whose evidence is too coarse to trust a narrow lead.

`unique_best` is **not** simply a stricter `mutual_best`. Its two filters run
in sequence, so the right-side contest is judged only among rows that already won on the
left, which lets it admit pairs `mutual_best` rejects: given ``(h1,g1,5)``,
``(h2,g1,1)`` and ``(h2,g2,0)`` with lower better, ``unique_best`` claims ``(h1,g1)``
because ``h2`` is already spoken for by ``g2``, while ``mutual_best`` drops it because
``g1`` strictly prefers ``h2``. It is a greedy sequential assignment, not a mutual one.
Neither rule's output contains the other's.

All four accept either a ``DataFrame`` or a ``LazyFrame`` and return the same type, take
single- or multi-column keys, and add no columns to the frame they are given.

The choice among them is a matcher design decision with real precision/recall
consequences, so this module deliberately provides the primitives rather than one
configurable function: a workflow states which rule it uses by which one it calls.

**Ties are dropped, never broken.** A tie means the evidence cannot tell the candidates
apart, and any rule that picks one anyway -- row order, ``keep="first"``, or a "total" sort
key such as a loan id -- asserts a match the data does not support. Determinism is not the
fix: appending an id to the sort makes the pick repeatable without making it right. Every
rule here either drops tied candidates or (`mutual_best`) keeps them all for
`drop_ambiguous` to drop.

There is deliberately no rule that takes the single best candidate per key on each side
via ``sort(score).group_by(key).first()`` and keeps the pairs both sides chose: it
resolves a tie by row order, and row order is not even stable. Polars neither sorts
stably by default nor orders ``group_by`` deterministically across threads, so two runs
of the same code on the same inputs can disagree on a large share of pairs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "assert_one_to_one",
    "drop_ambiguous",
    "margin_best",
    "mutual_best",
    "unique_best",
]


def _as_list(key: str | Sequence[str]) -> list[str]:
    """Normalize a single column name or a sequence of them to a list."""
    return [key] if isinstance(key, str) else list(key)


def _best_over(score: str, keys: list[str], descending: bool) -> pl.Expr:
    """The winning score within each ``keys`` group (max if ``descending`` else min)."""
    col = pl.col(score)
    return col.max().over(keys) if descending else col.min().over(keys)


def drop_ambiguous[FrameT: (pl.DataFrame, pl.LazyFrame)](
    frame: FrameT,
    left_key: str | Sequence[str],
    right_key: str | Sequence[str],
) -> FrameT:
    """Keep only pairs whose keys each appear exactly once in the frame.

    The unscored 1:1 rule: a pair survives only when its left key is on no other row and
    its right key is on no other row. Every contested candidate is dropped, including the
    one that would have won on any tie-break — the loan is left for a later round rather
    than assigned on weak evidence.

    Null key values group together like any other value, so a frame with nulls in a key
    column will keep at most one null-keyed pair. Drop or fill null keys beforehand if
    that is not what you want.

    Args:
        frame: Candidate pairs.
        left_key: Left-side loan key — one column name or several.
        right_key: Right-side loan key — one column name or several.

    Returns:
        The pairs that are one-to-one on both keys, in the input's column order.

    Example:
        >>> import polars as pl
        >>> pairs = pl.DataFrame({"h": ["a", "a", "b"], "g": ["x", "y", "z"]})
        >>> drop_ambiguous(pairs, "h", "g").to_dict(as_series=False)
        {'h': ['b'], 'g': ['z']}
    """
    left, right = _as_list(left_key), _as_list(right_key)
    return frame.filter((pl.len().over(left) == 1) & (pl.len().over(right) == 1))


def mutual_best[FrameT: (pl.DataFrame, pl.LazyFrame)](
    frame: FrameT,
    score: str,
    left_key: str | Sequence[str],
    right_key: str | Sequence[str],
    *,
    descending: bool = False,
) -> FrameT:
    """Keep pairs that are best-ranked for both of their keys.

    Dense-ranks ``score`` within each key group on both sides and keeps the rows ranked
    first in both. Ties are *kept*, so a loan whose two candidates score identically
    contributes both rows and the result is not yet one-to-one. A crosswalk caller must
    follow it with `drop_ambiguous`, which drops the tied rows, or use `unique_best` to
    drop ties in one step -- never resolve the kept ties any other way.

    Args:
        frame: Candidate pairs.
        score: Column holding the match score.
        left_key: Left-side loan key — one column name or several.
        right_key: Right-side loan key — one column name or several.
        descending: True when a higher score is a better match. Defaults to False, the
            deviation-style convention in which lower is better.

    Returns:
        The mutually best-ranked pairs, ties included.
    """
    left, right = _as_list(left_key), _as_list(right_key)
    rank = pl.col(score).rank(method="dense", descending=descending)
    return frame.filter((rank.over(left) == 1) & (rank.over(right) == 1))


def unique_best[FrameT: (pl.DataFrame, pl.LazyFrame)](
    frame: FrameT,
    score: str,
    left_key: str | Sequence[str],
    right_key: str | Sequence[str],
    *,
    descending: bool = False,
) -> FrameT:
    """Keep pairs that are the strictly unique best for their left key, then their right.

    A pair survives only when no other candidate for the same key attains the same winning
    score, so a key whose best score is tied contributes nothing and the surviving pairs
    are one-to-one on both keys without further deduplication.

    The two filters run **in sequence** — left first, then right over the survivors —
    which is what the call sites this replaces do, and the order is load-bearing. Because
    the right-side contest only sees rows that already won on the left, this is a greedy
    sequential assignment rather than a mutual one, and it can admit a pair that
    `mutual_best` rejects (see this module's docstring for the worked case). Neither
    rule's output contains the other's; they are different assignment rules, not two
    strictnesses of one rule.

    Args:
        frame: Candidate pairs.
        score: Column holding the match score.
        left_key: Left-side loan key — one column name or several.
        right_key: Right-side loan key — one column name or several.
        descending: True when a higher score is a better match. Defaults to False, the
            deviation-style convention in which lower is better.

    Returns:
        Pairs that are one-to-one on both keys.

    Example:
        >>> import polars as pl
        >>> pairs = pl.DataFrame(
        ...     {"h": ["a", "a", "b", "b"], "g": ["w", "x", "y", "z"], "s": [1, 2, 3, 3]}
        ... )
        >>> unique_best(pairs, "s", "h", "g").to_dict(as_series=False)
        {'h': ['a'], 'g': ['w'], 's': [1]}
    """
    left, right = _as_list(left_key), _as_list(right_key)
    col = pl.col(score)

    for keys in (left, right):
        winner = _best_over(score, keys, descending)
        n_winners = (col == winner).sum().over(keys)
        frame = frame.filter((col == winner) & (n_winners == 1))

    return frame


def margin_best[FrameT: (pl.DataFrame, pl.LazyFrame)](
    frame: FrameT,
    score: str,
    left_key: str | Sequence[str],
    right_key: str | Sequence[str],
    *,
    margin: float,
    descending: bool = False,
) -> FrameT:
    """Keep pairs that lead every rival by at least ``margin`` for both of their keys.

    A mutual rule with a confidence gap. For each key, a pair qualifies when it is the key's
    only candidate, or when its score is the key's unique best and clears the runner-up by
    ``margin`` or more. A pair survives only when it qualifies on both sides, each judged
    against the whole frame at once (`unique_best` judges the right side only among the
    left side's winners). Each key keeps at most one qualifier, so the result is one-to-one
    on its own.

    Where `unique_best` accepts any strict winner, this rule refuses a winner by a hair --
    the case in which a rival scoring nearly as well is too often the true match.

    A null score never wins, and rivals scoring null do not count toward the runner-up. A
    key whose rivals all score null therefore offers no margin to clear, and only a sole
    candidate survives there.

    Args:
        frame: Candidate pairs.
        score: Column holding the match score.
        left_key: Left-side loan key — one column name or several.
        right_key: Right-side loan key — one column name or several.
        margin: How far the winner must lead the runner-up, in ``score`` units.
        descending: True when a higher score is a better match. Defaults to False, the
            deviation-style convention in which lower is better.

    Returns:
        Pairs that are one-to-one on both keys.

    Raises:
        ValueError: If ``margin`` is not positive.

    Example:
        >>> import polars as pl
        >>> pairs = pl.DataFrame(
        ...     {"h": ["a", "a", "b", "b", "c"], "g": ["v", "w", "x", "y", "z"], "s": [3, 1, 2, 2, 0]}
        ... )
        >>> margin_best(pairs, "s", "h", "g", margin=2, descending=True).to_dict(as_series=False)
        {'h': ['a', 'c'], 'g': ['v', 'z'], 's': [3, 0]}
    """
    if margin <= 0:
        raise ValueError(f"margin must be positive, got {margin}")
    left, right = _as_list(left_key), _as_list(right_key)
    col = pl.col(score) if descending else -pl.col(score)

    qualifies = pl.lit(value=True)
    for keys in (left, right):
        best = col.max().over(keys)
        runner_up = pl.when(col < best).then(col).max().over(keys)
        leads = (col == best) & ((col == best).sum().over(keys) == 1) & (best - runner_up >= margin)
        qualifies = qualifies & ((pl.len().over(keys) == 1) | leads.fill_null(value=False))

    return frame.filter(qualifies)


def assert_one_to_one(
    frame: pl.DataFrame,
    left_key: str | Sequence[str],
    right_key: str | Sequence[str],
    *,
    context: str = "crosswalk",
) -> pl.DataFrame:
    """Raise unless the frame is one-to-one on both keys; otherwise return it unchanged.

    For use where a crosswalk is assembled from parts whose disjointness is an invariant
    rather than something the reduction just enforced — combining per-round outputs, for
    instance, where an overlap means two rounds claimed the same loan.

    Args:
        frame: The assembled crosswalk.
        left_key: Left-side loan key — one column name or several.
        right_key: Right-side loan key — one column name or several.
        context: Name used in the error message.

    Returns:
        The frame, unchanged.

    Raises:
        ValueError: If either key repeats, naming the side and the number of extra rows.
    """
    for side, keys in (("left", _as_list(left_key)), ("right", _as_list(right_key))):
        n_distinct = frame.select(keys).n_unique()
        if n_distinct != len(frame):
            raise ValueError(
                f"{context} is not one-to-one on the {side} key {keys}: "
                f"{len(frame):,} rows over {n_distinct:,} distinct values "
                f"({len(frame) - n_distinct:,} duplicated)"
            )
    return frame
