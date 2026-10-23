"""FHFA reporting conventions for loan amounts and Freddie Mac loan terms (shared helpers).

Loan amounts are published as the midpoint of a $10k bin (``$171k`` -> ``$175k``), the
convention FHFA and post-2018 public HMDA share. An amount sitting exactly on a $10k
multiple could have been binned to either side, so matchers offer it to both bins.

FHFA's Single-Family Census Tract file does not carry Freddie Mac loan terms
verbatim. Two conventions apply (evidence: 14.0M identity-known pairs, 99.998%
coverage):

* **Ceiling binning** — non-standard terms are reported at the smallest
  standard term (120/180/240/360/480) at or above the true term. This is a
  ceiling, not nearest-value: observed 144→180 (9,449 vs 10 to the nearer 120)
  and 264/276/288→360 (vs the nearer 240).
* **Product-envelope up-binning** — a seller-dependent subset of standard
  120- and 240-month loans is reported at the security-product term (15yr /
  30yr envelope): 120→180 and 240→360. 180- and 360-month loans are never
  up-binned. Cross-validated: HMDA agrees with the Freddie term on 100.0% of
  tract-verified pairs, and the loans' own maturity dates imply the Freddie
  term exactly.

Fannie Mae terms are preserved verbatim by FHFA — these helpers are
Freddie-specific.
"""

from __future__ import annotations

import polars as pl


def round_to_fhfa_bin(amount: pl.Expr) -> pl.Expr:
    """Round a loan amount to FHFA's $10k bin midpoint (``$171k`` -> ``$175k``).

    Args:
        amount: The loan-amount expression.

    Returns:
        An expression yielding the $10k bin midpoint.
    """
    return ((amount / 10000).floor() * 10000 + 5000).cast(pl.Float64)


def is_bin_edge(amount: pl.Expr) -> pl.Expr:
    """True for an amount sitting exactly on a $10k boundary (ambiguous bin).

    Args:
        amount: The loan-amount expression.

    Returns:
        A boolean expression, true on an exact $10k boundary.
    """
    return (amount % 10000) == 0


#: FHFA standard reporting terms (months), ascending.
STANDARD_TERMS: list[int] = [120, 180, 240, 360, 480]

_MAX_TERM = 600


def term_ceiling(term: int) -> int:
    """Smallest FHFA standard term at or above ``term`` (480 caps the top).

    Args:
        term: Loan term in months.

    Returns:
        The smallest FHFA standard term at or above ``term``.
    """
    for s in STANDARD_TERMS:
        if s >= term:
            return s
    return STANDARD_TERMS[-1]


def term_product(term: int) -> int:
    """Security-product envelope term (15yr / 30yr / 40yr) for ``term``.

    Args:
        term: Loan term in months.

    Returns:
        The security-product envelope term.
    """
    if term <= 180:
        return 180
    if term <= 360:
        return 360
    return 480


#: Freddie term → FHFA ceiling-standard term, for every plausible raw term.
#: Replaces the former nearest-value mapping, which provably mis-mapped 144→120.
TERM_TO_FHFA_BIN: dict[int, int] = {t: term_ceiling(t) for t in range(1, _MAX_TERM)}

#: Terms (post ceiling-mapping) that FHFA sometimes reports at the product
#: envelope instead: the phase-2 candidate mapping.
FREDDIE_PRODUCT_UPBIN: dict[int, int] = {120: 180, 240: 360}

#: Raw HMDA/Freddie term → the FHFA product-envelope value that the
#: up-binning convention could produce (only where it differs from ceiling).
_PRODUCT_FROM_RAW: dict[int, int] = {
    t: term_product(t)
    for t in range(1, _MAX_TERM)
    if term_product(t) != term_ceiling(t) or t in FREDDIE_PRODUCT_UPBIN
}


def freddie_term_diff_expr(
    true_term_col: str,
    fhfa_term_col: str,
    enterprise_col: str = "enterprise_flag",
) -> pl.Expr:
    """|true − FHFA| term difference, treated as 0 under Freddie conventions.

    For enterprise_flag==2 records the difference is zeroed when the FHFA term
    equals the ceiling-standard or product-envelope term of the true term —
    those pairs are reporting artifacts, not mismatches. Non-Freddie records
    get the plain absolute difference.

    Args:
        true_term_col: Column holding the true term.
        fhfa_term_col: Column holding the FHFA-reported term.
        enterprise_col: Column holding the enterprise flag.

    Returns:
        The term-difference expression, zeroed for Freddie reporting artifacts.
    """
    true_t = pl.col(true_term_col)
    fhfa_t = pl.col(fhfa_term_col)
    plain = (true_t - fhfa_t).abs()
    ceiling_ok = fhfa_t == true_t.replace_strict(TERM_TO_FHFA_BIN, default=true_t)
    product_ok = fhfa_t == true_t.replace_strict(_PRODUCT_FROM_RAW, default=None)
    is_freddie = pl.col(enterprise_col) == 2
    return (
        pl.when(is_freddie & (ceiling_ok | product_ok))
        .then(0)
        .otherwise(plain)
        .alias(f"{true_term_col}_fhfa_diff")
    )
