"""Shared dtype standardization for silver layers.

This module is the project-wide home for the close-to-final column-type rules
settled by the silver type audit (2026-06-27): geographic identifiers as
zero-padded ``Utf8`` (never integer), sentinel null-mapping, fixed-point
rescaling, smallest-int category codes, ``pl.Enum`` for stable code sets, and a
declarative per-dataset :class:`SilverSpec` that :func:`apply_silver_types`
turns into expressions and :func:`enforce_schema` validates against a target
schema. It sits beside :mod:`mortgage_data_manager.core.dates`, which owns the
date-encoding parsers this module dispatches to.

Each subpackage declares one :class:`SilverSpec` per silver dataset in its
``config.py`` and applies it in ``import_silver.py``:

    >>> from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
    >>> lf = apply_silver_types(pl.scan_parquet(bronze), SINGLE_FAMILY_SILVER)
    >>> enforce_schema(lf.collect_schema(), SINGLE_FAMILY_SILVER.target, name="fha/single_family")
    >>> lf.sink_parquet(out)  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import polars as pl

from mortgage_data_manager.core.dates import (
    ccyymm_to_date,
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

#: Date-encoding name -> parser, dispatched by :attr:`SilverSpec.dates`.
_DATE_PARSERS = {
    "mmyyyy": month_to_date,
    "mmddyyyy": mmddyyyy_to_date,
    "yyyymmdd": yyyymmdd_to_date,
    "ccyymm": ccyymm_to_date,
    "mmddyy": mmddyy_to_date,
    "from_datetime": to_date_from_datetime,
}


# --------------------------------------------------------------------------- #
# Geographic identifiers — zero-padded Utf8, never integer (audit rule 1)
# --------------------------------------------------------------------------- #
def pad_geo(expr: pl.Expr, width: int) -> pl.Expr:
    """Normalize a numeric-or-string geographic code to zero-padded ``Utf8`` of ``width``.

    Recovers leading zeros lost to integer storage (``602`` -> ``"00602"``) and
    drops float-inferred ``.0`` suffixes (``1.0`` -> ``"01"``), while leaving
    genuinely non-numeric codes untouched (no data loss). Nulls stay null.

    Args:
        expr: The geographic-code column (Int, Float, or String).
        width: The canonical zero-padded width (e.g. 5 for ZIP, 2 for state FIPS).

    Returns:
        A ``Utf8`` expression zero-padded to ``width``.
    """
    numeric = expr.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).cast(pl.String)
    return numeric.fill_null(expr.cast(pl.String)).str.zfill(width)


def to_zip(expr: pl.Expr) -> pl.Expr:
    """Standardize a 5-digit ZIP code to zero-padded ``Utf8``."""
    return pad_geo(expr, 5)


def to_zip3(expr: pl.Expr) -> pl.Expr:
    """Standardize a 3-digit ZIP prefix to zero-padded ``Utf8``."""
    return pad_geo(expr, 3)


def to_state_fips(expr: pl.Expr) -> pl.Expr:
    """Standardize a 2-digit state FIPS code to zero-padded ``Utf8``."""
    return pad_geo(expr, 2)


def to_county_fips(expr: pl.Expr) -> pl.Expr:
    """Standardize a 3-digit (within-state) county FIPS code to zero-padded ``Utf8``."""
    return pad_geo(expr, 3)


def to_tract6(expr: pl.Expr) -> pl.Expr:
    """Standardize a 6-digit census-tract code to zero-padded ``Utf8``."""
    return pad_geo(expr, 6)


def to_geoid11(state: pl.Expr, county: pl.Expr, tract: pl.Expr) -> pl.Expr:
    """Build the 11-digit census ``GEOID`` from state (2) + county (3) + tract (6).

    Args:
        state: State FIPS column.
        county: Within-state county FIPS column.
        tract: 6-digit tract column.

    Returns:
        A ``Utf8`` expression of the concatenated 11-digit GEOID.
    """
    return pl.concat_str([to_state_fips(state), to_county_fips(county), to_tract6(tract)])


# --------------------------------------------------------------------------- #
# Sentinels (audit rule 9)
# --------------------------------------------------------------------------- #
def null_sentinels(expr: pl.Expr, sentinels: Iterable) -> pl.Expr:
    """Map missing-value sentinels to null before downstream numeric/date casts.

    Args:
        expr: The column to clean.
        sentinels: Values to treat as null (e.g. ``(9999, 999)`` or ``("-",)``).

    Returns:
        ``expr`` with any value in ``sentinels`` replaced by null.
    """
    return pl.when(expr.is_in(list(sentinels))).then(None).otherwise(expr)


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
# --------------------------------------------------------------------------- #
_SIGNED_INT_RANGES: tuple[tuple[type[pl.DataType], int, int], ...] = (
    (pl.Int8, -(2**7), 2**7 - 1),
    (pl.Int16, -(2**15), 2**15 - 1),
    (pl.Int32, -(2**31), 2**31 - 1),
    (pl.Int64, -(2**63), 2**63 - 1),
)


def smallest_int(lo: int, hi: int) -> type[pl.DataType]:
    """Return the smallest signed integer dtype that holds the inclusive range ``[lo, hi]``.

    Sizes for any in-band sentinel too: pass the sentinel-inclusive min/max so a
    ``-99999`` sentinel forces ``Int32`` rather than silently overflowing ``Int16``.

    Args:
        lo: Minimum value the column must hold (sentinels included).
        hi: Maximum value the column must hold (sentinels included).

    Returns:
        The narrowest ``pl.Int*`` dtype covering ``[lo, hi]``.
    """
    for dtype, dmin, dmax in _SIGNED_INT_RANGES:
        if lo >= dmin and hi <= dmax:
            return dtype
    return pl.Int64


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
    """Strip surrounding whitespace from every column name (collapses padded variants)."""
    renames = {c: c.strip() for c in lf.collect_schema().names() if c != c.strip()}
    return lf.rename(renames) if renames else lf


# --------------------------------------------------------------------------- #
# Declarative per-dataset spec + applier + validator
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SilverSpec:
    """Declarative close-to-final dtype spec for one silver dataset.

    Declared per dataset in a subpackage ``config.py`` and applied in
    ``import_silver.py`` via :func:`apply_silver_types`. Every field is optional;
    each names the columns a standardization applies to. :func:`apply_silver_types`
    applies the stages in a fixed, safe order (renames -> name strip -> sentinels
    -> dates -> fixed-point -> zip -> fips -> enums -> bools -> numeric casts ->
    identifiers), touching only columns that are present.

    Attributes:
        renames: Old -> new column name aliasing (cross-vintage harmonization).
        strip_names: Strip whitespace from all column names before typing.
        sentinels: Column -> values to null-map (applied before any cast).
        dates: Column -> encoding key (one of :data:`_DATE_PARSERS`).
        fixed_point: Column -> implied-decimal scale (GNMA fixed-width feeds).
        zip5: Columns to standardize as 5-digit zero-padded ZIP ``Utf8``.
        zip3: Columns to standardize as 3-digit zero-padded ZIP-prefix ``Utf8``.
        fips: Column -> zero-pad width for FIPS/geo codes.
        enums: Column -> ``pl.Enum`` of its stable level set.
        bools: Columns to map from ``Y``/``N`` to ``pl.Boolean``.
        casts: Column -> numeric dtype for plain integer/float casts.
        identifiers: Columns to keep/force as ``Utf8`` (codes, not quantities).
        target: The final output schema, enforced by :func:`enforce_schema`.
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
    target: pl.Schema | None = None


def apply_silver_types(lf: pl.LazyFrame, spec: SilverSpec) -> pl.LazyFrame:
    """Apply a :class:`SilverSpec`'s standardizations to a LazyFrame.

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

    cols = set(lf.collect_schema().names())

    def stage(exprs: list[pl.Expr]) -> pl.LazyFrame:
        return lf.with_columns(exprs) if exprs else lf

    lf = stage(
        [null_sentinels(pl.col(c), v).alias(c) for c, v in spec.sentinels.items() if c in cols]
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
    lf = stage([pl.col(c).cast(pl.String).alias(c) for c in spec.identifiers if c in cols])

    # Conform to the declared target: add any absent target column as a typed
    # null (vintages legitimately omit some columns) and order columns
    # canonically, so every partition shares one stable schema regardless of
    # which years were built. Columns not in the target are kept (and surfaced
    # by enforce_schema) rather than dropped.
    if spec.target is not None:
        target_cols = list(spec.target.names())
        have = set(lf.collect_schema().names())
        fills = [pl.lit(None).cast(spec.target[c]).alias(c) for c in target_cols if c not in have]
        if fills:
            lf = lf.with_columns(fills)
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

    Run after :func:`apply_silver_types` (which conforms columns to the target):
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
