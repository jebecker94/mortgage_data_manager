"""One decode table per cross-source mortgage code space.

Each agency publishes the same fact — loan purpose, occupancy, origination channel,
property type — in its own codes. Each concept here has one label vocabulary and one table
per source mapping that source's codes onto it; a consumer that needs another code space
(a matcher's join key, FHFA's numeric codes) maps from the labels, so what a code means is
written once. A code a table does not list decodes to null.

Example:
    >>> purpose = decode(pl.col("Loan Purpose"), GSE_LOAN_PURPOSE)
    >>> fhfa = recode(pl.col("Loan Purpose"), GSE_LOAN_PURPOSE, FHFA_LOAN_PURPOSE_CODE)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import polars as pl

# --- loan purpose -------------------------------------------------------------------

#: The loan-purpose vocabulary. ``refi_unspecified`` is a refinance whose cash-out status
#: the source does not disclose.
LOAN_PURPOSES: Final[tuple[str, ...]] = (
    "purchase",
    "refi_cash_out",
    "refi_rate_term",
    "refi_unspecified",
)

#: GSE letter codes (SF releases and the UMBS loan-level disclosures). Fannie publishes
#: C/P/R and Freddie C/N/P; Freddie's N (no-cash-out refinance) is Fannie's R. M (a
#: modification reissue) and 9 (not available) are not purposes.
GSE_LOAN_PURPOSE: Final[dict[str, str]] = {
    "P": "purchase",
    "C": "refi_cash_out",
    "R": "refi_rate_term",
    "N": "refi_rate_term",
}

#: HMDA ``loan_purpose`` from 2018: 1 purchase, 31 refinancing, 32 cash-out refinancing.
#: Home improvement (2), other (4) and not applicable (5) have no label.
HMDA_LOAN_PURPOSE: Final[dict[int, str]] = {
    1: "purchase",
    31: "refi_rate_term",
    32: "refi_cash_out",
}

#: FHFA's PUDB purpose codes, by label (1 purchase, 2 refinance, 7 cash-out refinance).
FHFA_LOAN_PURPOSE_CODE: Final[dict[str, int]] = {
    "purchase": 1,
    "refi_rate_term": 2,
    "refi_cash_out": 7,
}

#: The broad purchase / refinance class many matchers key on.
PURPOSE_CLASS: Final[dict[str, str]] = {
    "purchase": "P",
    "refi_cash_out": "R",
    "refi_rate_term": "R",
    "refi_unspecified": "R",
}


def gnma_loan_purpose(purpose: pl.Expr, refinance_type: pl.Expr | None) -> pl.Expr:
    """Decode Ginnie's (Loan Purpose, Refinance Type) pair to the loan-purpose vocabulary.

    Codes per Ginnie's loan-level layout, identical in every version V1.0-V1.8: Loan
    Purpose 1 purchase, 2 refinance, 3 / 4 loan modification (HAMP / non-HAMP), 5
    re-performing; Refinance Type 1 not streamlined and not cash-out, 2 cash-out, 3
    streamlined. A streamline takes no cash out, so 1 and 3 are both rate-term. Purposes
    3-5 are re-pooled loans, not originations, and decode to null.

    Args:
        purpose: Ginnie ``Loan Purpose`` as a string code.
        refinance_type: Ginnie ``Refinance Type`` as a string code, or None when the
            vintage lacks it.

    Returns:
        A String expression on `LOAN_PURPOSES`.
    """
    refi = refinance_type if refinance_type is not None else pl.lit(None, dtype=pl.String)
    is_refi = purpose == "2"
    return (
        pl.when(purpose == "1")
        .then(pl.lit("purchase"))
        .when(is_refi & (refi == "2"))
        .then(pl.lit("refi_cash_out"))
        .when(is_refi & refi.is_in(["1", "3"]))
        .then(pl.lit("refi_rate_term"))
        .when(is_refi)
        .then(pl.lit("refi_unspecified"))
        .otherwise(None)
    )


# --- occupancy ----------------------------------------------------------------------

#: The occupancy vocabulary.
OCCUPANCIES: Final[tuple[str, ...]] = ("principal", "second_home", "investment")

#: GSE letter codes.
GSE_OCCUPANCY: Final[dict[str, str]] = {"P": "principal", "S": "second_home", "I": "investment"}

#: The numeric codes HMDA (``occupancy_type`` from 2018) and FHFA share, by label.
OCCUPANCY_CODE: Final[dict[str, int]] = {"principal": 1, "second_home": 2, "investment": 3}

#: HMDA ``occupancy_type`` from 2018.
HMDA_OCCUPANCY: Final[dict[int, str]] = {code: label for label, code in OCCUPANCY_CODE.items()}

# --- origination channel ------------------------------------------------------------

#: The channel vocabulary. ``tpo`` is a third-party origination whose broker /
#: correspondent split the source does not disclose.
CHANNELS: Final[tuple[str, ...]] = ("retail", "correspondent", "broker", "tpo")

#: GSE letter codes. Only Freddie publishes T.
GSE_CHANNEL: Final[dict[str, str]] = {
    "R": "retail",
    "C": "correspondent",
    "B": "broker",
    "T": "tpo",
}

#: Ginnie ``Third-Party Origination Type``. Code 3 covers retail and any other
#: non-third-party origination; it is read as retail.
GNMA_TPO_CHANNEL: Final[dict[str, str]] = {"1": "broker", "2": "correspondent", "3": "retail"}

#: FHFA's PUDB channel codes, by enterprise: FHFA codes a Fannie correspondent loan 9
#: and a Freddie one 3.
FHFA_CHANNEL_CODE: Final[dict[str, dict[str, int]]] = {
    "FNMA": {"retail": 1, "broker": 2, "correspondent": 9},
    "FHLMC": {"retail": 1, "broker": 2, "correspondent": 3},
}

# --- property type ------------------------------------------------------------------

#: GSE property-type codes.
GSE_PROPERTY_TYPE: Final[dict[str, str]] = {
    "SF": "single_family",
    "PU": "pud",
    "CO": "condo",
    "CP": "coop",
    "MH": "manufactured",
}

#: FHFA's PUDB property type, by label: 1 site-built (any 1-4 unit structure), 2
#: manufactured.
FHFA_PROPERTY_TYPE_CODE: Final[dict[str, int]] = {
    "single_family": 1,
    "pud": 1,
    "condo": 1,
    "coop": 1,
    "manufactured": 2,
}

# --- credit score and flags ---------------------------------------------------------

#: The valid credit-score range; anything outside it (9999, 7777, 0, blank) is a
#: not-available code.
CREDIT_SCORE_RANGE: Final[tuple[int, int]] = (300, 850)


def credit_score(expr: pl.Expr) -> pl.Expr:
    """A credit score as Float64, null outside `CREDIT_SCORE_RANGE`.

    Casts first, so it accepts any representation (string, integer, float).

    Args:
        expr: The score column.

    Returns:
        A Float64 expression.
    """
    num = expr.cast(pl.Float64, strict=False)
    return pl.when(num.is_between(*CREDIT_SCORE_RANGE)).then(num).otherwise(None)


def flag_to_bool(expr: pl.Expr) -> pl.Expr:
    """A yes/no indicator in any representation, as Boolean.

    Built for frames that mix representations after silver: Boolean (SF releases),
    ``Enum`` with not-applicable codes (UMBS ``7`` / ``9``) and ``Y``/``N`` strings
    (Ginnie). Through String, ``Y`` / ``true`` are True, ``N`` / ``false`` are False and
    everything else is null. Case-sensitive; `mortgage_data_manager.core.types.yn_to_bool`
    is the strict decoder silver applies to raw ``Y``/``N`` text.

    Args:
        expr: The flag column.

    Returns:
        A Boolean expression.
    """
    s = expr.cast(pl.String, strict=False).str.strip_chars()
    return (
        pl.when(s.is_in(["Y", "true"]))
        .then(pl.lit(value=True))
        .when(s.is_in(["N", "false"]))
        .then(pl.lit(value=False))
        .otherwise(None)
    )


# --- applying a table ---------------------------------------------------------------


def decode(expr: pl.Expr, table: Mapping[str, str] | Mapping[int, str]) -> pl.Expr:
    """Map source codes to labels; a code the table does not list becomes null.

    A string-coded column is cast to String first, so an ``Enum`` silver column whose
    levels lack one of the table's codes (Fannie's purpose has no ``N``) decodes rather
    than failing the level check.

    Args:
        expr: The source code column.
        table: One of this module's decode tables.

    Returns:
        A String expression on the table's labels.
    """
    return _apply(expr, table, pl.String)


def invert[K: (str, int)](codes: Mapping[K, str]) -> dict[str, K]:
    """Label -> source code, for a decode table that gives each label one code.

    Args:
        codes: A one-to-one decode table.

    Returns:
        The inverse table.

    Raises:
        ValueError: If two codes share a label, so the inverse would have to pick one.
    """
    inverse = {label: code for code, label in codes.items()}
    if len(inverse) != len(codes):
        raise ValueError(f"not one-to-one: {dict(codes)}")
    return inverse


def compose[K: (str, int), T: (str, int)](
    codes: Mapping[K, str], targets: Mapping[str, T]
) -> dict[K, T]:
    """Source code -> target code, through the labels (a label with no target is left out).

    Args:
        codes: A decode table (source code -> label).
        targets: Label -> target code.

    Returns:
        The composed table.
    """
    return {code: targets[label] for code, label in codes.items() if label in targets}


def recode[K: (str, int), T: (str, int)](
    expr: pl.Expr,
    codes: Mapping[K, str],
    targets: Mapping[str, T],
    *,
    dtype: pl.DataType | type[pl.DataType] = pl.String,
) -> pl.Expr:
    """Map source codes straight into a consumer's code space, through the labels.

    Args:
        expr: The source code column.
        codes: A decode table (source code -> label).
        targets: Label -> target code.
        dtype: The target codes' dtype.

    Returns:
        An expression in the target code space, null where either table lacks the value.
    """
    return _apply(expr, compose(codes, targets), dtype)


def _apply(
    expr: pl.Expr, table: Mapping[Any, Any], dtype: pl.DataType | type[pl.DataType]
) -> pl.Expr:
    """``replace_strict`` with string-coded input cast to String first."""
    if all(isinstance(k, str) for k in table):
        expr = expr.cast(pl.String)
    return expr.replace_strict(dict(table), default=None, return_dtype=dtype)


def encode(
    labels: pl.Expr,
    table: Mapping[str, str] | Mapping[str, int],
    *,
    dtype: pl.DataType | type[pl.DataType] = pl.String,
) -> pl.Expr:
    """Map labels into a consumer's code space; a label the table does not list becomes null.

    Args:
        labels: A label expression (from `decode`).
        table: Label -> target code.
        dtype: The target codes' dtype.

    Returns:
        An expression in the target code space.
    """
    return _apply(labels, table, dtype)
