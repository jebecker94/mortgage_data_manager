"""Shared lender-name normalization helpers for matching workflows.

Two comparison styles over institution names (HMDA panel respondent names, FHA
mortgagee/sponsor names, GNMA issuer names, GSE seller names):

- `normalize_name` — string normalization that preserves word order, used
  where names are compared for equality or containment (the direct
  HMDA↔issuance matcher's seller names and HMDA-side lender names must normalize
  identically to join at all).
- `name_tokens` / `jaccard` — token-set comparison with
  corporate-suffix stopwords removed, for identity-alignment scoring and the
  aggregator-issuer validation checks.
"""

from __future__ import annotations

import polars as pl

NAME_STOP: list[str] = [
    "LLC",
    "INC",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "NA",
    "FSB",
    "LP",
    "LLP",
    "PLLC",
    "NATIONAL",
    "ASSOCIATION",
    "THE",
    "AND",
    "OF",
    "DBA",
    "FKA",
    "LTD",
]


def normalize_name(expr: pl.Expr) -> pl.Expr:
    """Uppercase, normalize punctuation, collapse whitespace.

    Commas become spaces because a comma always separates tokens, and the GSEs
    are inconsistent about the space that follows it: FNMA and Freddie both ship
    ``CITIMORTGAGE,INC`` while HMDA has ``CitiMortgage, Inc.``. Deleting the comma
    outright glued the first into ``CITIMORTGAGEINC`` and split one lender across
    two name keys, which no downstream join could reunite.

    Periods and apostrophes are deleted rather than spaced, so initialisms close
    up the way both sources write them (``U.S. BANK`` → ``US BANK``).

    Args:
        expr: The name column expression to normalize.

    Returns:
        The normalized name expression.
    """
    return (
        expr.str.to_uppercase()
        .str.replace_all(",", " ")
        .str.replace_all(r"[.']", "")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )


def name_tokens(colname: str) -> pl.Expr:
    """Uppercased, punctuation-stripped, stopword-filtered token list for a name column.

    Args:
        colname: Name of the column holding the name.

    Returns:
        A token-list expression for that column.
    """
    c = (
        pl.col(colname)
        .str.to_uppercase()
        .str.replace_all(r"[^A-Z0-9]", " ")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .str.split(" ")
    )
    return c.list.eval(
        pl.element().filter(~pl.element().is_in(NAME_STOP) & (pl.element().str.len_chars() > 0))
    )


def jaccard(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    """Token-set Jaccard similarity between two token-list expressions.

    Args:
        a: First token-list expression.
        b: Second token-list expression.

    Returns:
        A Float64 expression yielding the token-set Jaccard similarity.
    """
    return a.list.set_intersection(b).list.len() / a.list.set_union(b).list.len().cast(pl.Float64)
