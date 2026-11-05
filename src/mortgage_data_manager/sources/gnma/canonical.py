"""GNMA canonical schema: load, validate, and parse fixed-width / delimited files.

A canonical
schema (``schemas/gnma/canonical/{prefix}.json``) is the deterministic, portable source
of truth for a prefix's layout: record types, date-versioned field windows, portable
``arrow_type`` + ``decode`` rules. This module is pure schema/parse/validate logic — the
silver *writer* lives in `.import_silver` per the package convention.

The loader is 100% deterministic over the JSON; all interpretation of the source PDFs
was resolved at authoring time into explicit structure (see the spec's governing
principle). A separate validator checks a schema against real bronze before it is trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "CanonicalSchema",
    "record_types_in_file",
    "transform_fixed_width_canonical",
    "transform_delimited_canonical",
    "validate_structural",
]

# arrow_type -> polars dtype for the final cast
_ARROW_TO_POLARS: dict[str, Any] = {
    "string": pl.String,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "float64": pl.Float64,
    "bool": pl.Boolean,
    "date32": pl.Date,
}

# GNMA packed-date pattern -> polars strptime format
_DATE_PATTERNS: dict[str, str] = {
    "CCYYMM": "%Y%m",
    "CCYYMMDD": "%Y%m%d",
    "YYYYMMDD": "%Y%m%d",
    "MMDDYY": "%m%d%y",  # GNMA factor files: 6-digit MM/DD/YY (two-digit year, strptime pivot)
    "YYYYMM": "%Y%m",
}


def _ym(value: str | None) -> str | None:
    """Normalize a date bound / as-of token to a comparable ``YYYY-MM`` string.

    Accepts ``YYYYMM`` or ``YYYYMMDD`` (filename tokens — some prefixes such as
    ``issrcutoff`` carry a full daily date), ``YYYY-MM`` or ``YYYY-MM-DD`` (schema bounds).
    ``YYYY-MM`` strings compare correctly lexicographically, which is how window
    coverage is tested.

    Returns:
        The normalized ``YYYY-MM`` token, or None when ``value`` is None.
    """
    if value is None:
        return None
    v = value.strip()
    if len(v) in (6, 8) and v.isdigit():  # YYYYMM / YYYYMMDD
        return f"{v[:4]}-{v[4:6]}"
    return v[:7]  # YYYY-MM(-DD) -> YYYY-MM


def _ym_next(ym: str) -> str:
    """Month after a ``YYYY-MM`` — the first period a window's ``to`` no longer covers."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _version_boundaries(fields: list[dict[str, Any]]) -> list[str]:
    """Every period where the active-field set can change.

    Each layout ``from`` **and the month after each bounded ``to``** — so a field that drops
    out (or a window that narrows) with nothing new starting that month still gets its
    post-drop tiling checked, not just periods where some field begins.

    Returns:
        The boundary period tokens, sorted.
    """
    bs: set[str] = set()
    for f in fields:
        for lay in f["layouts"]:
            fr = _ym(lay.get("from"))
            if fr is not None:
                bs.add(fr)
            to = _ym(lay.get("to"))
            if to is not None:
                bs.add(_ym_next(to))
    return sorted(bs)


@dataclass(frozen=True)
class CanonicalSchema:
    """A loaded canonical schema for one prefix."""

    prefix: str
    layout_kind: str  # "fixed_width" | "delimited"
    delimiter: str | None
    record_types: dict[str, dict[str, Any]]
    control_record_types: tuple[str, ...]  # non-data control/trailer rows (e.g. "CT", "END", "")
    raw: dict[str, Any]

    @classmethod
    def load(cls, prefix: str, canonical_dir: str | Path) -> CanonicalSchema:
        """Load ``{canonical_dir}/{prefix}.json``.

        Args:
            prefix: GNMA file prefix naming the schema file.
            canonical_dir: Directory holding the canonical JSON files.

        Returns:
            The loaded schema.
        """
        path = Path(canonical_dir) / f"{prefix}.json"
        data = json.loads(path.read_text())
        return cls(
            prefix=data["prefix"],
            layout_kind=data["layout_kind"],
            delimiter=data.get("delimiter"),
            record_types=data["record_types"],
            control_record_types=tuple(data.get("control_record_types", [])),
            raw=data,
        )

    @classmethod
    def exists(cls, prefix: str, canonical_dir: str | Path) -> bool:
        """Whether ``prefix`` has a canonical schema file.

        Args:
            prefix: GNMA file prefix naming the schema file.
            canonical_dir: Directory holding the canonical JSON files.

        Returns:
            True when ``{canonical_dir}/{prefix}.json`` exists.
        """
        return (Path(canonical_dir) / f"{prefix}.json").exists()

    def record_type_codes(self) -> list[str]:
        """The declared record-type codes, in schema order."""
        return list(self.record_types.keys())

    def rt_present(self, record_type: str, as_of: str) -> bool:
        """Whether ``record_type`` exists for the ``as_of`` period.

        Args:
            record_type: Record-type code.
            as_of: Period token, ``YYYYMM`` or ``YYYY-MM``.

        Returns:
            True when a presence window covers the period.
        """
        ym = _ym(as_of)
        assert ym is not None  # as_of is always a real period token
        for window in self.record_types[record_type].get("present", [{"from": None, "to": None}]):
            lo, hi = _ym(window.get("from")), _ym(window.get("to"))
            if (lo is None or lo <= ym) and (hi is None or ym <= hi):
                return True
        return False

    def active_fields(self, record_type: str, as_of: str) -> list[tuple[dict, dict]]:
        """Return ``(field, layout)`` pairs active for ``as_of``, sorted by position.

        A field is active when exactly one of its ``layouts`` windows covers the period;
        fixed-width results are sorted by ``begin``, delimited by ``ordinal``.

        Args:
            record_type: Record-type code.
            as_of: Period token to resolve the vintage at.

        Returns:
            The active ``(field, layout)`` pairs, sorted by position.
        """
        ym = _ym(as_of)
        assert ym is not None  # as_of is always a real period token
        out: list[tuple[dict, dict]] = []
        for field in self.record_types[record_type]["fields"]:
            for layout in field["layouts"]:
                lo, hi = _ym(layout.get("from")), _ym(layout.get("to"))
                if (lo is None or lo <= ym) and (hi is None or ym <= hi):
                    out.append((field, layout))
                    break
        if self.layout_kind == "fixed_width":
            out.sort(key=lambda fl: fl[1]["begin"])
        else:
            out.sort(key=lambda fl: fl[1]["ordinal"])
        return out


# =============================================================================
# Decode: raw token expr -> typed expr (portable arrow_type + decode rule)
# =============================================================================


def _fixed_point_expr(token: pl.Expr, scale: int) -> pl.Expr:
    """Implied-decimal integer token -> the exact nearest Float64.

    Dividing a float by the scale is not exact in polars (``5999 / 1000`` gives
    ``5.9990000000000006``), so a power-of-ten scale writes the decimal string and
    parses it instead, matching what a feed with explicit decimals yields. A token
    that is not an integer keeps the plain divide.

    Returns:
        The decoded value; null where the token does not parse.
    """
    digits = len(str(scale)) - 1
    divided = token.cast(pl.Float64, strict=False) / scale
    if scale != 10**digits:
        return divided
    value = token.cast(pl.Int64, strict=False)
    magnitude = value.abs()
    exact = pl.concat_str(
        pl.when(value < 0).then(pl.lit("-")).otherwise(pl.lit("")),
        (magnitude // scale).cast(pl.String),
        pl.lit("."),
        (magnitude % scale).cast(pl.String).str.zfill(digits),
    ).cast(pl.Float64, strict=False)
    return pl.coalesce(exact, divided)


def _decode_expr(token: pl.Expr, field: dict) -> pl.Expr:
    """Turn a stripped raw-token string expr into the field's ``arrow_type``.

    Mirrors the spec's ``decode`` kinds: ``plain`` (direct parse), ``fixed_point``
    (integer / scale), ``date`` (packed-date parse), ``boolean`` (true/false tokens).
    ``strict=False`` everywhere so blanks and sentinels become null rather than raise.

    Returns:
        The token expression decoded to the field's ``arrow_type``.
    """
    arrow_type = field["arrow_type"]
    decode = field.get("decode") or {}
    kind = decode.get("kind", "plain")

    # null_values: literal tokens that map to null before any cast.
    null_values = field.get("null_values")
    if null_values:
        token = pl.when(token.is_in(null_values)).then(None).otherwise(token)

    if arrow_type == "string":
        # A blank field is missing, not an empty value.
        return pl.when(token == "").then(None).otherwise(token)
    if arrow_type in ("int32", "int64"):
        return token.cast(_ARROW_TO_POLARS[arrow_type], strict=False)
    if arrow_type == "float64":
        if kind == "fixed_point":
            scale = decode.get("scale") or (
                10 ** decode["implied_decimals"] if decode.get("implied_decimals") else None
            )
            if scale:
                return _fixed_point_expr(token, int(scale))
        return token.cast(pl.Float64, strict=False)
    if arrow_type == "date32":
        fmt = _DATE_PATTERNS[decode.get("pattern", "CCYYMMDD")]
        return token.str.to_date(fmt, strict=False)
    if arrow_type == "bool":
        trues = decode.get("true_values", ["Y", "1", "T"])
        falses = decode.get("false_values", ["N", "0", "F"])
        return (
            pl.when(token.is_in(trues))
            .then(True)
            .when(token.is_in(falses))
            .then(False)
            .otherwise(None)
        )
    raise ValueError(f"Unknown arrow_type {arrow_type!r} for field {field['id']!r}")


def _slice_token(begin: int, length: int, text_column: str) -> pl.Expr:
    """1-indexed fixed-width slice -> stripped string expr."""
    return pl.col(text_column).str.slice(begin - 1, length).str.strip_chars()


def _discriminator(
    schema: CanonicalSchema, active: list[tuple[dict, dict]], text_column: str
) -> pl.Expr | None:
    """The stripped record-type token of each row, or None when the RT has no such field."""
    disc = next((lo for f, lo in active if f["id"] == "record_type"), None)
    if disc is None:
        return None
    if schema.layout_kind == "fixed_width":
        return _slice_token(disc["begin"], disc["length"], text_column)
    first = pl.col(text_column).str.split(schema.delimiter or "|").list.get(0, null_on_oob=True)
    return first.str.strip_chars()


def record_types_in_file(
    bronze_file: str | Path,
    schema: CanonicalSchema,
    record_types: list[str],
    as_of: str,
    text_column: str = "text_content",
) -> set[str]:
    """Which of ``record_types`` have at least one row in a bronze file, in one scan.

    A layout can declare record types a file never carries (a pool-statistics file
    holds only its PS records, though the layout it shares with the supplemental
    file lists all of them), so this is what decides whether a silver file is written.

    Args:
        bronze_file: Path of the bronze parquet holding the raw text column.
        schema: The loaded canonical schema.
        record_types: Record-type codes to look for, each present for ``as_of``.
        as_of: Period token identifying the vintage.
        text_column: Column holding the raw record text.

    Returns:
        The subset of ``record_types`` with rows in the file.

    Raises:
        ValueError: If a record type has no active fields for that period.
    """
    checks = []
    for rt in record_types:
        active = schema.active_fields(rt, as_of)
        if not active:
            raise ValueError(f"No active fields for {schema.prefix} {rt} @ {as_of}")
        token = _discriminator(schema, active, text_column)
        # A record type without a discriminator is the whole file.
        checks.append((pl.len() > 0 if token is None else (token == rt).any()).alias(rt))
    if not checks:
        return set()
    row = pl.scan_parquet(bronze_file).select(checks).collect().row(0, named=True)
    return {rt for rt, found in row.items() if found}


# =============================================================================
# Transform: bronze parquet (one text_content column) -> typed per-RT LazyFrame
# =============================================================================


def transform_fixed_width_canonical(
    bronze_file: str | Path,
    schema: CanonicalSchema,
    record_type: str,
    as_of: str,
    text_column: str = "text_content",
) -> pl.LazyFrame:
    """Parse one fixed-width bronze file for a single record type at ``as_of``.

    Args:
        bronze_file: Path of the bronze parquet holding the raw text column.
        schema: The loaded canonical schema.
        record_type: Record-type code to parse.
        as_of: Period token identifying the vintage.
        text_column: Column holding the raw record text.

    Returns:
        The parsed records, conformed to the record type's target schema.

    Raises:
        ValueError: If no fields are active for that record type and period.
    """
    active = schema.active_fields(record_type, as_of)
    if not active:
        raise ValueError(f"No active fields for {schema.prefix} {record_type} @ {as_of}")

    # Discriminator: the record_type field's window filters to this RT's rows.
    disc = _discriminator(schema, active, text_column)
    lf = pl.scan_parquet(bronze_file)
    if disc is not None:
        lf = lf.filter(disc == record_type)

    exprs = [
        _decode_expr(_slice_token(lo["begin"], lo["length"], text_column), f).alias(f["id"])
        for f, lo in active
    ]
    return lf.select(exprs)


def transform_delimited_canonical(
    bronze_file: str | Path,
    schema: CanonicalSchema,
    record_type: str,
    as_of: str,
    text_column: str = "text_content",
) -> pl.LazyFrame:
    """Parse one delimited bronze file for a single record type at ``as_of``.

    Tokens are mapped by ``ordinal`` (1-indexed); a short trailing tail is null-padded
    (``split_exact`` yields nulls past the last present token). Field ordinals must be
    contiguous ``1..N`` (enforced by `validate_structural`).

    Args:
        bronze_file: Path of the bronze parquet holding the raw text column.
        schema: The loaded canonical schema.
        record_type: Record-type code to parse.
        as_of: Period token identifying the vintage.
        text_column: Column holding the raw record text.

    Returns:
        The parsed records, conformed to the record type's target schema.
    """
    active = schema.active_fields(record_type, as_of)
    if not active:
        raise ValueError(f"No active fields for {schema.prefix} {record_type} @ {as_of}")
    delimiter = schema.delimiter or "|"

    lf = pl.scan_parquet(bronze_file)
    # Discriminator is ordinal 1 (the record_type field).
    disc = _discriminator(schema, active, text_column)
    if disc is not None:
        lf = lf.filter(disc == record_type)

    parts = pl.col(text_column).str.split(delimiter)
    exprs = []
    for f, lo in active:
        idx = lo["ordinal"] - 1
        token = parts.list.get(idx, null_on_oob=True).str.strip_chars()
        exprs.append(_decode_expr(token, f).alias(f["id"]))
    return lf.select(exprs)


# =============================================================================
# Validator (committed, deterministic — structural)
# =============================================================================


def validate_structural(schema: CanonicalSchema) -> list[str]:
    """Run the structural checks that need no data.

    Args:
        schema: The canonical schema to check.

    Returns:
        The problems found; an empty list means the schema passed.
    """
    problems: list[str] = []
    for rt, body in schema.record_types.items():
        # id uniqueness within the record type
        ids = [f["id"] for f in body["fields"]]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"{rt}: duplicate ids {sorted(dupes)}")

        # per-field: windows well-ordered and non-overlapping in time
        for f in body["fields"]:
            wins = [(_ym(lay.get("from")), _ym(lay.get("to"))) for lay in f["layouts"]]
            wins_sorted = sorted(wins, key=lambda w: w[0] or "")
            for (lo1, hi1), (lo2, _) in zip(wins_sorted, wins_sorted[1:]):
                if hi1 is None or (lo2 is not None and lo2 <= hi1):
                    problems.append(f"{rt}.{f['id']}: overlapping/unbounded windows {wins}")
                    break

        # tiling per distinct version boundary (incl. post-drop-out periods)
        boundaries = _version_boundaries(body["fields"])
        if schema.layout_kind == "fixed_width":
            for ym in boundaries:
                active = schema.active_fields(rt, ym.replace("-", ""))
                pos = sorted((lo["begin"], lo["end"], f["id"]) for f, lo in active)
                expected = 1
                for begin, end, fid in pos:
                    if begin != expected:
                        problems.append(
                            f"{rt} @ {ym}: gap/overlap before {fid} "
                            f"(begin={begin}, expected {expected})"
                        )
                        break
                    expected = end + 1
        else:  # delimited: ordinals contiguous 1..N per boundary
            for ym in boundaries:
                active = schema.active_fields(rt, ym.replace("-", ""))
                ords = sorted(lo["ordinal"] for _, lo in active)
                if ords != list(range(1, len(ords) + 1)):
                    problems.append(f"{rt} @ {ym}: ordinals not contiguous 1..N: {ords}")

    # cross-version append-only: an id's arrow_type/decode never changes
    for rt, body in schema.record_types.items():
        for f in body["fields"]:
            pass  # single arrow_type/decode per field object -> invariant by construction
    return problems
