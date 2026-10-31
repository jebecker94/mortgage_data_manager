"""Shared dtype standardization for silver layers.

This module is the project-wide home for the close-to-final column-type rules:
geographic identifiers as zero-padded ``Utf8`` (never integer), sentinel
null-mapping, fixed-point rescaling, smallest-int category codes, ``pl.Enum`` for
stable code sets, and a declarative per-dataset `SilverSpec` that
`apply_silver_types` turns into expressions and `enforce_schema` validates against
a target schema. It sits beside `mortgage_data_manager.core.dates`, which owns the
date-encoding parsers this module dispatches to.

Each subpackage declares one `SilverSpec` per silver dataset in its
``config.py`` and applies it in ``import_silver.py``:

    >>> from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
    >>> lf = apply_silver_types(pl.scan_parquet(bronze), SINGLE_FAMILY_SILVER)
    >>> enforce_schema(lf.collect_schema(), SINGLE_FAMILY_SILVER.target, name="fha/single_family")
    >>> lf.sink_parquet(out)  # doctest: +SKIP
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import polars as pl

from mortgage_data_manager.core.dates import (
    ccyymm_to_date,
    iso_to_date,
    mmddyy_to_date,
    mmddyyyy_to_date,
    month_to_date,
    to_date_from_datetime,
    yyyymmdd_to_date,
)
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

#: A polars dtype as either a class (``pl.Int32``) or an instance (``pl.Enum([...])``).
type PolarsType = pl.DataType | type[pl.DataType]

#: Date-encoding name -> parser, dispatched by `SilverSpec.dates`.
_DATE_PARSERS = {
    "mmyyyy": month_to_date,
    "mmddyyyy": mmddyyyy_to_date,
    "yyyymmdd": yyyymmdd_to_date,
    "ccyymm": ccyymm_to_date,
    "mmddyy": mmddyy_to_date,
    "iso": iso_to_date,
    "from_datetime": to_date_from_datetime,
}


# --------------------------------------------------------------------------- #
# Fixed-width codes — zero-padded Utf8, never integer (audit rule 1)
# --------------------------------------------------------------------------- #
def pad_geo(expr: pl.Expr, width: int) -> pl.Expr:
    """Normalize a numeric-or-string fixed-width code to zero-padded ``Utf8`` of ``width``.

    The shared width normalizer for every code whose canonical external form is
    zero-padded: geographic codes (ZIP, FIPS, tract — hence the name) and the
    institutional identifiers of `SilverSpec.identifiers_padded` (agency
    mortgagee numbers, EINs). Recovers leading zeros lost to integer storage
    (``602`` -> ``"00602"``) and drops float-inferred ``.0`` suffixes (``1.0``
    -> ``"01"``), while leaving genuinely non-numeric codes untouched (no data
    loss). Nulls stay null.

    Idempotent: re-applying it to an already-padded value is a no-op, so a
    consumer that pads by hand is unaffected. Values wider than ``width`` are
    left as they are rather than truncated.

    Args:
        expr: The code column (Int, Float, or String).
        width: The canonical zero-padded width (e.g. 5 for ZIP, 2 for state FIPS).

    Returns:
        A ``Utf8`` expression zero-padded to ``width``.
    """
    numeric = expr.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).cast(pl.String)
    return numeric.fill_null(expr.cast(pl.String)).str.zfill(width)


def to_zip(expr: pl.Expr) -> pl.Expr:
    """Standardize a 5-digit ZIP code to zero-padded ``Utf8``.

    Args:
        expr: The column expression to standardize.

    Returns:
        A Utf8 expression zero-padded to 5 characters.
    """
    return pad_geo(expr, 5)


def to_zip3(expr: pl.Expr) -> pl.Expr:
    """Standardize a 3-digit ZIP prefix to zero-padded ``Utf8``.

    Args:
        expr: The column expression to standardize.

    Returns:
        A Utf8 expression zero-padded to 3 characters.
    """
    return pad_geo(expr, 3)


# --------------------------------------------------------------------------- #
# Sentinels (audit rule 9)
# --------------------------------------------------------------------------- #
def null_sentinels(expr: pl.Expr, sentinels: Iterable, dtype: PolarsType | None = None) -> pl.Expr:
    """Map missing-value sentinels to null before downstream numeric/date casts.

    This stage runs BEFORE the numeric casts, so a column that a spec declares numeric may
    still arrive as ``String`` — several UMBS multifamily vintages ship the weighted-average
    credit-score columns as text, and ``is_in`` raises rather than returning False on that
    type mismatch (``'is_in' cannot check for List(Int64) values in String``). When the
    incoming dtype is ``String``, the sentinels are compared in their stripped text form so
    the mask still lands; whitespace padding is tolerated because these feeds are
    fixed-width exports.

    Args:
        expr: The column to clean.
        sentinels: Values to treat as null (e.g. ``(9999, 999)`` or ``("-",)``).
        dtype: The column's incoming dtype, when known. Only ``String`` changes behavior.

    Returns:
        ``expr`` with any value in ``sentinels`` replaced by null.
    """
    values = list(sentinels)
    if dtype == pl.String:
        text = [str(v) for v in values]
        return pl.when(expr.str.strip_chars().is_in(text)).then(None).otherwise(expr)
    return pl.when(expr.is_in(values)).then(None).otherwise(expr)


# --------------------------------------------------------------------------- #
# Fixed-point rescaling — GNMA / legacy fixed-width feeds (audit rule 4)
# --------------------------------------------------------------------------- #
def fixed_point(expr: pl.Expr, scale: int, dtype: PolarsType = pl.Float32) -> pl.Expr:
    """Rescale a raw fixed-point ASCII integer to its real decimal value.

    Args:
        expr: The raw zero-padded integer-string column.
        scale: Implied-decimal divisor (e.g. 1000 for rates, 100 for cents/ratios).
        dtype: Output float dtype (``Float32`` for rates/ratios, ``Float64`` for money).

    Returns:
        A float expression equal to ``int(expr) / scale`` (null on blank/sentinel).
    """
    return expr.cast(pl.Int64, strict=False).cast(dtype) / scale


# --------------------------------------------------------------------------- #
# Integers and booleans (audit rules 5, 10)
#
# Dollar amounts are Int64 (or Float64), never Int32, even when every value fits.
# Polars keeps Int32 for sum(), group_by().sum() and sum_horizontal() and wraps on
# overflow without an error (only Int8/Int16 are widened first), so an Int32 dollar
# column silently corrupts any total above ~$2.1B: about 10,000 mortgages.
# Narrow integers are for codes, flags and small counts.
# --------------------------------------------------------------------------- #
#: Column names that denote a dollar amount, and the words that mark a same-named
#: column as something else (a ratio, a code, a count). Used by the data guards.
_MONEY_NAME = re.compile(
    r"amount|upb|balance|income|price|value|cost|premium|coverage|proceeds|payment|fee",
    re.IGNORECASE,
)
_NOT_MONEY_NAME = re.compile(
    r"ratio|rate|term|percent|pct|code|flag|indicator|type|count|number", re.IGNORECASE
)


def narrow_money_columns(schema: Mapping[str, PolarsType]) -> list[str]:
    """Dollar-amount columns stored as Int32 or UInt32, which overflow when summed.

    Args:
        schema: Column name to dtype, e.g. a ``collect_schema()`` result.

    Returns:
        The offending column names, in schema order.

    Example:
        >>> narrow_money_columns({"loan_amount": pl.Int32, "loan_type": pl.Int8})
        ['loan_amount']
    """
    return [
        name
        for name, dtype in schema.items()
        if dtype in (pl.Int32, pl.UInt32)
        and _MONEY_NAME.search(name)
        and not _NOT_MONEY_NAME.search(name)
    ]


def yn_to_bool(expr: pl.Expr) -> pl.Expr:
    """Map a clean ``Y``/``N`` flag to ``pl.Boolean`` (anything else -> null).

    Use only for strictly binary flags; tri-valued ``Y``/``N``/``U`` columns must
    stay categorical so the third state is not lost.

    Args:
        expr: A ``Y``/``N`` string column.

    Returns:
        A ``Boolean`` expression.
    """
    upper = expr.cast(pl.String).str.to_uppercase()
    return pl.when(upper == "Y").then(True).when(upper == "N").then(False).otherwise(None)


# --------------------------------------------------------------------------- #
# Name hygiene (audit rule 12)
# --------------------------------------------------------------------------- #
def strip_column_names(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Strip surrounding whitespace from every column name.

    Args:
        lf: The frame to rename.

    Returns:
        The frame with padded name variants collapsed.
    """
    renames = {c: c.strip() for c in lf.collect_schema().names() if c != c.strip()}
    return lf.rename(renames) if renames else lf


# --------------------------------------------------------------------------- #
# Declarative per-dataset spec + applier + validator
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SilverSpec:
    """Declarative close-to-final dtype spec for one silver dataset.

    Declared per dataset in a subpackage ``config.py`` and applied in
    ``import_silver.py`` via `apply_silver_types`. Every field is optional;
    each names the columns a standardization applies to. `apply_silver_types`
    applies the stages in a fixed, safe order (renames -> name strip -> sentinels
    -> dates -> fixed-point -> zip -> fips -> enums -> bools -> numeric casts ->
    identifiers), touching only columns that are present.

    Attributes:
        renames: Old -> new column name aliasing (cross-vintage harmonization).
        strip_names: Strip whitespace from all column names before typing.
        sentinels: Column -> values to null-map (applied before any cast).
        dates: Column -> encoding key (one of `_DATE_PARSERS`).
        fixed_point: Column -> implied-decimal scale (GNMA fixed-width feeds).
        zip5: Columns to standardize as 5-digit zero-padded ZIP ``Utf8``.
        zip3: Columns to standardize as 3-digit zero-padded ZIP-prefix ``Utf8``.
        fips: Column -> zero-pad width for FIPS/geo codes.
        enums: Column -> ``pl.Enum`` of its stable level set.
        bools: Columns to map from ``Y``/``N`` to ``pl.Boolean``.
        casts: Column -> numeric dtype for plain integer/float casts.
        identifiers: Columns to keep/force as ``Utf8`` (codes, not quantities).
        identifiers_padded: Column -> canonical zero-pad width, for identifiers
            that have one. A bare ``Utf8`` cast preserves the damage an upstream
            integer round-trip did to a leading zero; this repairs it, so the
            stored value is the identifier's canonical external form and joins
            to an external roster without every consumer re-padding by hand.
            Use it only where a canonical width genuinely exists — an
            identifier of no fixed width belongs in ``identifiers``.
        target: The final output schema, enforced by `enforce_schema`.
        optional_target: Target columns some vintages legitimately omit, which
            conform-to-target may fill with typed nulls. When this is a tuple (even an empty one),
            filling any target column NOT listed raises `SchemaMismatchError` — the signature of an
            upstream rename or drop, which would otherwise ship a silently 100%-null column.
            ``None`` is permissive: every fill is allowed and logged. Declare a tuple only after
            auditing which columns the dataset's real vintages actually omit.
    """

    renames: Mapping[str, str] = field(default_factory=dict)
    strip_names: bool = False
    sentinels: Mapping[str, Iterable] = field(default_factory=dict)
    dates: Mapping[str, str] = field(default_factory=dict)
    fixed_point: Mapping[str, int] = field(default_factory=dict)
    zip5: tuple[str, ...] = ()
    zip3: tuple[str, ...] = ()
    fips: Mapping[str, int] = field(default_factory=dict)
    enums: Mapping[str, pl.Enum] = field(default_factory=dict)
    bools: tuple[str, ...] = ()
    casts: Mapping[str, PolarsType] = field(default_factory=dict)
    identifiers: tuple[str, ...] = ()
    identifiers_padded: Mapping[str, int] = field(default_factory=dict)
    target: pl.Schema | None = None
    optional_target: tuple[str, ...] | None = None


# --------------------------------------------------------------------------- #
# Column roles + spec builder
# --------------------------------------------------------------------------- #
# A SilverSpec can be written out by hand, but every column then has to be named
# twice — once in the stage map that transforms it, once in ``target`` — and the
# two are kept in agreement by hand. These roles let a dataset instead be declared
# as one ordered ``(name, role)`` list, from which `spec_from_columns`
# derives both. The list doubles as the canonical column order.

#: Role: a clean ``Y``/``N`` flag -> ``pl.Boolean`` (see `yn_to_bool`).
BOOL: Final = "bool"
#: Role: keep/force as ``Utf8`` — a code or identifier, not a quantity.
TEXT: Final = "text"
#: Role: 5-digit ZIP -> zero-padded ``Utf8``.
ZIP5: Final = "zip5"
#: Role: 3-digit ZIP prefix -> zero-padded ``Utf8``.
ZIP3: Final = "zip3"


@dataclass(frozen=True)
class Declared:
    """Role: put the column in the target schema but transform nothing.

    For columns that already arrive correctly typed from bronze (a date the
    bronze builder parsed) or are added downstream (a partition column). They
    must appear in the target so conform-to-target keeps them and places them
    in canonical order.

    Attributes:
        dtype: The dtype the column already has, recorded in the target schema.
    """

    dtype: PolarsType


@dataclass(frozen=True)
class Fips:
    """Role: zero-pad a geographic code to ``width`` as ``Utf8``.

    Attributes:
        width: Canonical zero-padded width (2 state, 3 county, 6 tract, ...).
    """

    width: int


@dataclass(frozen=True)
class PaddedId:
    """Role: an institutional identifier with a canonical zero-padded width.

    The counterpart of `TEXT` for identifiers that have a fixed external
    form (a 5-digit agency mortgagee number, a 9-digit EIN). See
    `SilverSpec.identifiers_padded`.

    Attributes:
        width: Canonical zero-padded width.
    """

    width: int


@dataclass(frozen=True)
class FixedPoint:
    """Role: rescale an implied-decimal integer (GNMA-style fixed-width feeds).

    Attributes:
        scale: Implied-decimal divisor (1000 for rates, 100 for cents/ratios).
        dtype: Output float dtype.
    """

    scale: int
    dtype: PolarsType = pl.Float32


#: What a column can be declared as in a `spec_from_columns` list: one of
#: the marker constants above, a key of `_DATE_PARSERS` (a date encoding),
#: a ``pl.Enum`` instance, a plain polars dtype (-> cast), or one of the
#: parameterized role objects.
type ColumnRole = str | PolarsType | Declared | Fips | FixedPoint | PaddedId


def spec_from_columns(
    columns: Sequence[tuple[str, ColumnRole]],
    *,
    sentinels: Mapping[str, Iterable] | None = None,
    renames: Mapping[str, str] | None = None,
    strip_names: bool = False,
    optional_target: Sequence[str] | None = None,
) -> SilverSpec:
    """Build a `SilverSpec` from an ordered ``(name, role)`` column list.

    Each column is declared once. Its role decides both which transformation
    stage it lands in and what dtype it gets in the target schema, so the two
    cannot disagree. List order becomes target column order, which is what
    conform-to-target uses to give every partition one stable schema.

    Roles:
        - a key of `_DATE_PARSERS` (``"mmyyyy"``, ``"iso"``, ...): parse
          with that encoding -> ``pl.Date``
        - `BOOL`: ``Y``/``N`` -> ``pl.Boolean``
        - `TEXT`: keep/force ``Utf8``
        - `ZIP5` / `ZIP3`: zero-padded ZIP ``Utf8``
        - `Fips`: zero-padded geographic code ``Utf8``
        - `PaddedId`: zero-padded institutional identifier ``Utf8``
        - `FixedPoint`: implied-decimal rescale
        - a ``pl.Enum`` instance: cast to that enum
        - any other polars dtype: plain cast to it
        - `Declared`: no transformation, target dtype only

    Args:
        columns: Ordered ``(column_name, role)`` pairs. Order is authoritative.
        sentinels: Column -> values to null-map before any cast. Keys must name
            columns in ``columns``, so a typo fails loudly instead of silently
            never masking.
        renames: Old -> new column aliasing applied before typing.
        strip_names: Strip whitespace from all column names before typing.
        optional_target: Target columns some vintages legitimately omit (see
            `SilverSpec.optional_target`). Passing a sequence — even an
            empty one — makes conform-to-target fatal on any other absent
            column; ``None`` is permissive.

    Returns:
        The equivalent `SilverSpec`.

    Raises:
        ValueError: If a column is declared twice, a role is unrecognized, or a
            ``sentinels``/``optional_target`` entry names a column absent from
            ``columns``.
    """
    dates: dict[str, str] = {}
    fixed: dict[str, int] = {}
    zip5: list[str] = []
    zip3: list[str] = []
    fips: dict[str, int] = {}
    enums: dict[str, pl.Enum] = {}
    bools: list[str] = []
    casts: dict[str, PolarsType] = {}
    idents: list[str] = []
    padded_idents: dict[str, int] = {}
    target: dict[str, PolarsType] = {}

    for name, role in columns:
        if name in target:
            raise ValueError(f"Column declared twice in spec column list: {name!r}")
        if isinstance(role, Declared):
            target[name] = role.dtype
        elif isinstance(role, Fips):
            fips[name] = role.width
            target[name] = pl.String
        elif isinstance(role, PaddedId):
            padded_idents[name] = role.width
            target[name] = pl.String
        elif isinstance(role, FixedPoint):
            fixed[name] = role.scale
            target[name] = role.dtype
        elif isinstance(role, str):
            if role in _DATE_PARSERS:
                dates[name] = role
                target[name] = pl.Date
            elif role == BOOL:
                bools.append(name)
                target[name] = pl.Boolean
            elif role == TEXT:
                idents.append(name)
                target[name] = pl.String
            elif role == ZIP5:
                zip5.append(name)
                target[name] = pl.String
            elif role == ZIP3:
                zip3.append(name)
                target[name] = pl.String
            else:
                raise ValueError(
                    f"Unknown role {role!r} for column {name!r}; expected a date encoding "
                    f"({', '.join(_DATE_PARSERS)}) or one of BOOL/TEXT/ZIP5/ZIP3."
                )
        elif isinstance(role, pl.Enum):
            enums[name] = role
            target[name] = role
        else:
            casts[name] = role
            target[name] = role

    sentinels = sentinels or {}
    unknown = [c for c in sentinels if c not in target]
    if unknown:
        raise ValueError(f"sentinels name columns absent from the column list: {sorted(unknown)}")
    if optional_target is not None:
        unknown = [c for c in optional_target if c not in target]
        if unknown:
            raise ValueError(
                f"optional_target names columns absent from the column list: {sorted(unknown)}"
            )

    return SilverSpec(
        renames=renames or {},
        strip_names=strip_names,
        sentinels=sentinels,
        dates=dates,
        fixed_point=fixed,
        zip5=tuple(zip5),
        zip3=tuple(zip3),
        fips=fips,
        enums=enums,
        bools=tuple(bools),
        casts=casts,
        identifiers=tuple(idents),
        identifiers_padded=padded_idents,
        target=pl.Schema(target),
        optional_target=tuple(optional_target) if optional_target is not None else None,
    )


def apply_silver_types(lf: pl.LazyFrame, spec: SilverSpec) -> pl.LazyFrame:
    """Apply a `SilverSpec`'s standardizations to a LazyFrame.

    Stages run in a fixed order so that, e.g., sentinels are null-mapped before
    numeric casts and an identifier ``Utf8`` cast sees a sentinel-cleaned column.
    Only columns present in ``lf`` are touched, so the same spec applies safely to
    a full build or a single-partition subset with evolving schema.

    Args:
        lf: The post-business-logic LazyFrame (bronze scan + any derived columns).
        spec: The dataset's declarative dtype spec.

    Returns:
        A LazyFrame with the standardized dtypes applied.
    """
    if spec.renames:
        present = {
            old: new for old, new in spec.renames.items() if old in lf.collect_schema().names()
        }
        if present:
            lf = lf.rename(present)
    if spec.strip_names:
        lf = strip_column_names(lf)

    schema = lf.collect_schema()
    cols = set(schema.names())

    def stage(exprs: list[pl.Expr]) -> pl.LazyFrame:
        return lf.with_columns(exprs) if exprs else lf

    lf = stage(
        [
            null_sentinels(pl.col(c), v, schema[c]).alias(c)
            for c, v in spec.sentinels.items()
            if c in cols
        ]
    )
    lf = stage(
        [_DATE_PARSERS[enc](pl.col(c)).alias(c) for c, enc in spec.dates.items() if c in cols]
    )
    lf = stage(
        [fixed_point(pl.col(c), s).alias(c) for c, s in spec.fixed_point.items() if c in cols]
    )
    lf = stage(
        [to_zip(pl.col(c)).alias(c) for c in spec.zip5 if c in cols]
        + [to_zip3(pl.col(c)).alias(c) for c in spec.zip3 if c in cols]
    )
    lf = stage([pad_geo(pl.col(c), w).alias(c) for c, w in spec.fips.items() if c in cols])
    lf = stage(
        [pl.col(c).cast(e, strict=False).alias(c) for c, e in spec.enums.items() if c in cols]
    )
    lf = stage([yn_to_bool(pl.col(c)).alias(c) for c in spec.bools if c in cols])
    lf = stage(
        [pl.col(c).cast(dt, strict=False).alias(c) for c, dt in spec.casts.items() if c in cols]
    )
    lf = stage(
        [pl.col(c).cast(pl.String).alias(c) for c in spec.identifiers if c in cols]
        + [pad_geo(pl.col(c), w).alias(c) for c, w in spec.identifiers_padded.items() if c in cols]
    )

    # Conform to the declared target: add any absent target column as a typed
    # null (vintages legitimately omit some columns) and order columns
    # canonically, so every partition shares one stable schema regardless of
    # which years were built. Columns not in the target are kept (and surfaced
    # by enforce_schema) rather than dropped. A spec that declares
    # ``optional_target`` bounds which columns may be filled: anything else
    # absent is an upstream rename/drop and fails here instead of shipping a
    # silently all-null column.
    if spec.target is not None:
        target_cols = list(spec.target.names())
        have = set(lf.collect_schema().names())
        absent = [c for c in target_cols if c not in have]
        if absent:
            if spec.optional_target is not None:
                unexpected = [c for c in absent if c not in spec.optional_target]
                if unexpected:
                    raise SchemaMismatchError(
                        f"Source never supplied target column(s) {unexpected}; a typed-null "
                        f"fill would ship them silently empty. Either the source renamed or "
                        f"dropped them (fix the spec's renames / investigate upstream), or "
                        f"they are legitimately vintage-dependent (add them to the spec's "
                        f"optional_target)."
                    )
                logger.debug("Conform-to-target filled optional column(s): %s", absent)
            else:
                logger.info(
                    "Conform-to-target filled absent column(s) with typed nulls: %s", absent
                )
            lf = lf.with_columns([pl.lit(None).cast(spec.target[c]).alias(c) for c in absent])
        extras = [c for c in lf.collect_schema().names() if c not in set(target_cols)]
        lf = lf.select([*target_cols, *extras])
    return lf


class SchemaMismatchError(ValueError):
    """Raised when a built silver schema diverges from its declared target."""


def enforce_schema(
    schema: Mapping[str, PolarsType],
    expected: pl.Schema | None,
    *,
    name: str,
) -> None:
    """Validate a built silver schema against its declared target and log a diff.

    Run after `apply_silver_types` (which conforms columns to the target):
    a dtype mismatch or a missing target column is fatal — that is the regression
    guard for silent corruption (a sentinel-mask that stops firing, a rename that
    is not applied, a typed-null fill that did not run). An *extra* column — a new
    upstream field not yet in the target — is only warned, so genuinely new source
    data surfaces a log line rather than breaking the build.

    Args:
        schema: The actual built schema (e.g. ``lf.collect_schema()``).
        expected: The declared target schema; a no-op when ``None``.
        name: Dataset label for log messages (e.g. ``"fha/single_family"``).

    Raises:
        SchemaMismatchError: On any dtype mismatch or missing target column.
    """
    if expected is None:
        return

    actual = dict(schema)
    exp = dict(expected)
    dtype_diff = {
        c: (str(actual[c]), str(exp[c])) for c in exp if c in actual and actual[c] != exp[c]
    }
    missing = [c for c in exp if c not in actual]
    extra = [c for c in actual if c not in exp]

    if dtype_diff:
        logger.error("[%s] silver schema dtype mismatch: %s", name, dtype_diff)
    if missing:
        logger.error("[%s] silver schema missing target columns: %s", name, missing)
    if extra:
        logger.warning("[%s] new column(s) not in target (update the spec): %s", name, extra)

    if dtype_diff or missing:
        raise SchemaMismatchError(
            f"{name}: silver schema mismatch (dtype={dtype_diff}, missing={missing})"
        )
    if not (dtype_diff or extra):
        logger.info("[%s] silver schema OK (%d columns)", name, len(exp))
