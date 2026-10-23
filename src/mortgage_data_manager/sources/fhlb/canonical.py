"""FHLB canonical schema: load, validate, and parse name-addressed annual files.

Pilot port of the GNMA canonical-schema convention (`mortgage_data_manager.sources.gnma.canonical`)
to a delimited, header-bearing source. A canonical schema
(``schemas/fhlb/canonical/{dataset}.json``) is the deterministic source of truth for a
dataset's layout: one entry per canonical field, carrying its portable ``arrow_type`` +
``decode`` rule and a list of date-versioned ``layouts`` naming the **source column** it
arrives under in each vintage.

Three things differ from the GNMA convention, all forced by the source shape:

* **Addressing is by name, not position.** GNMA locates a field by byte window
  (``begin``/``end``) or token ``ordinal``; FHLB's CSVs carry headers, and what drifts
  across vintages is the *header text* (``LoanNumber`` -> ``Loan Number`` -> ``AssignedID``
  -> ``Assigned ID`` -> ``AssignedID`` -> ``LoanCharacteristicsID``). So a layout carries
  ``source``.
* **Periods are years, not months.** The PUDB is published annually, so window bounds are
  ``YYYY`` and compare lexicographically as such.
* **There are no record types.** A GNMA schema multiplexes up to 55 record types in one
  file; a PUDB export is one flat table, so ``fields`` sits at the top level.

What carries over unchanged is the part that matters: dated windows, a portable
``arrow_type`` + ``decode`` pair per field, provenance recorded next to the declaration,
and a validator that checks the schema against real data before it is trusted.

The schema is **authoritative for typing**: the source is read entirely as text
(``infer_schema_length=0``) and every column is cast from its declared ``arrow_type``.
Nothing is inferred at read time, which is what stops a vintage whose values happen to be
integral from silently landing a different dtype than its neighbours.

Every vintage conforms to ONE target schema (`CanonicalSchema.target_schema`);
fields absent from a vintage are emitted as typed nulls rather than dropped.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "CanonicalSchema",
    "read_source_as_text",
    "transform_named_canonical",
    "validate_structural",
    "validate_against_raw",
]

#: Portable arrow type -> polars dtype for the final cast.
_ARROW_TO_POLARS: dict[str, Any] = {
    "string": pl.String,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "float64": pl.Float64,
    "bool": pl.Boolean,
    "date32": pl.Date,
}


def _period(value: str | int | None, grain: str = "year") -> str | None:
    """Normalize a period token or window bound to a lexicographically comparable string.

    ``year`` grain yields ``YYYY``; ``quarter`` grain yields ``YYYYQN``. Both are fixed
    width, so ordinary string comparison orders them correctly and window coverage can be
    tested with ``<=``.

    Returns:
        The normalized token, or None when ``value`` is None.
    """
    if value is None:
        return None
    v = str(value).strip().upper()
    if grain == "quarter":
        if "Q" in v:
            year, _, q = v.partition("Q")
            return f"{year[:4]}Q{q[:1]}"
        return f"{v[:4]}Q0"  # a bare year as a bound means "from the start of that year"
    return v[:4]


@dataclass(frozen=True)
class CanonicalSchema:
    """A loaded canonical schema for one FHLB dataset."""

    dataset: str
    layout_kind: str  # "named"
    period_grain: str  # "year" | "quarter"
    source_kind: str  # "csv" | "excel"
    fields: list[dict[str, Any]]
    vintage_overrides: dict[str, dict[str, Any]]
    raw: dict[str, Any]

    @classmethod
    def load(cls, dataset: str, canonical_dir: str | Path) -> CanonicalSchema:
        """Load ``{canonical_dir}/{dataset}.json``.

        Args:
            dataset: Canonical dataset name.
            canonical_dir: Directory holding the canonical schema JSON files.

        Returns:
            The parsed canonical schema.
        """
        path = Path(canonical_dir) / f"{dataset}.json"
        data = json.loads(path.read_text())
        return cls(
            dataset=data["dataset"],
            layout_kind=data["layout_kind"],
            period_grain=data.get("period_grain", "year"),
            source_kind=data.get("source_kind", "csv"),
            fields=data["fields"],
            vintage_overrides=data.get("vintage_overrides", {}),
            raw=data,
        )

    @classmethod
    def exists(cls, dataset: str, canonical_dir: str | Path) -> bool:
        """Whether a canonical schema file exists for ``dataset``.

        Args:
            dataset: Canonical dataset name.
            canonical_dir: Directory holding the canonical schema JSON files.

        Returns:
            True when the schema file is present.
        """
        return (Path(canonical_dir) / f"{dataset}.json").exists()

    def field_ids(self) -> list[str]:
        """Every canonical field id, in declared (target) order.

        Returns:
            The field ids, in declared order.
        """
        return [f["id"] for f in self.fields]

    def active_layout(self, field: dict[str, Any], as_of: str | int) -> dict[str, Any] | None:
        """The layout covering ``as_of`` for one field.

        Args:
            field: The canonical field declaration.
            as_of: Period token to resolve the layout at.

        Returns:
            The covering layout, or None if the field is absent in that vintage.
        """
        ym = _period(as_of, self.period_grain)
        assert ym is not None  # as_of is always a real period token
        for layout in field["layouts"]:
            lo = _period(layout.get("from"), self.period_grain)
            hi = _period(layout.get("to"), self.period_grain)
            if (lo is None or lo <= ym) and (hi is None or ym <= hi):
                return dict(layout)
        return None

    def active_fields(self, as_of: str | int) -> list[tuple[dict, dict]]:
        """``(field, layout)`` pairs present in the ``as_of`` vintage.

        Args:
            as_of: Period token to resolve the vintage at.

        Returns:
            The present ``(field, layout)`` pairs, in declared order.
        """
        out: list[tuple[dict, dict]] = []
        for field in self.fields:
            layout = self.active_layout(field, as_of)
            if layout is not None:
                out.append((field, layout))
        return out

    def target_schema(self) -> pl.Schema:
        """The single output schema every vintage conforms to.

        Returns:
            The target schema, in declared field order.
        """
        return pl.Schema(
            {f["id"]: _ARROW_TO_POLARS[f["arrow_type"]] for f in self.fields}  # type: ignore[misc]
        )

    def override(self, as_of: str | int, key: str, default: Any = None) -> Any:
        """A per-vintage file-level override (e.g. ``header_row``), or ``default``.

        Some vintages differ in how the FILE is shaped rather than what its columns are
        called -- the 2020Q1 members workbook carries a title banner above the header row.
        Those are declared here rather than special-cased in the builder.

        Args:
            as_of: Period token to resolve the override at.
            key: Override name, e.g. ``header_row``.
            default: Value returned when the vintage declares no such override.

        Returns:
            The declared override, or ``default``.
        """
        p = _period(as_of, self.period_grain)
        return self.vintage_overrides.get(p or "", {}).get(key, default)

    def periods(self) -> list[str]:
        """Every period any field declares a window bound at.

        Returns:
            The version-boundary period tokens, sorted.
        """
        bounds: set[str] = set()
        for field in self.fields:
            for layout in field["layouts"]:
                lo = _period(layout.get("from"), self.period_grain)
                if lo is not None:
                    bounds.add(lo)
        return sorted(bounds)


# =============================================================================
# Decode: raw token expr -> typed expr
# =============================================================================


def _decode_expr(token: pl.Expr, field: dict, layout: dict) -> pl.Expr:
    """Turn a stripped raw-token string expr into the field's ``arrow_type``.

    Mirrors the GNMA decode kinds (``plain``, ``fixed_point``, ``date``, ``boolean``), with
    two additions: a ``date`` field may carry its ``pattern`` on the *layout* rather than the
    field, for sources whose text format changes across vintages while the column name does
    not; and a layout may carry ``fallback_patterns`` for vintages whose cells MIX formats —
    the members workbooks store most dates as ``m/d/yy`` text but a handful as true Excel
    datetime cells, which the as-text read renders ``YYYY-MM-DD HH:MM:SS``. Patterns are
    tried in order and the first parse wins. ``strict=False`` everywhere, so blanks and
    unparseable tokens become null.

    Returns:
        The token expression decoded to the field's ``arrow_type``.
    """
    arrow_type = field["arrow_type"]
    decode = field.get("decode") or {}
    kind = decode.get("kind", "plain")

    null_values = field.get("null_values")
    if null_values:
        token = pl.when(token.is_in(null_values)).then(None).otherwise(token)

    if arrow_type == "string":
        return token
    if arrow_type in ("int32", "int64"):
        # via Float64 so "2015.0"-style tokens survive the trip to an integer column
        return token.cast(pl.Float64, strict=False).cast(_ARROW_TO_POLARS[arrow_type], strict=False)
    if arrow_type == "float64":
        if kind == "fixed_point":
            scale = decode.get("scale") or (
                10 ** decode["implied_decimals"] if decode.get("implied_decimals") else None
            )
            if scale:
                scaled: pl.Expr = token.cast(pl.Float64, strict=False) / scale
                return scaled
        return token.cast(pl.Float64, strict=False)
    if arrow_type == "date32":
        fmt = layout.get("pattern") or decode.get("pattern") or "%Y-%m-%d"
        parsed = token.str.to_date(fmt, strict=False)
        for fallback in layout.get("fallback_patterns") or decode.get("fallback_patterns") or []:
            parsed = parsed.fill_null(token.str.to_date(fallback, strict=False))
        # A two-digit year (%y) pivots 00-69 into the 2000s, so a member that joined in
        # 1966 parses as 2066. This is the same one-line correction the bronze builder has
        # always applied: a date in the future cannot be real, so shift it back a century.
        if decode.get("century_fix"):
            parsed = (
                pl.when(parsed > pl.lit(datetime.date.today()))
                .then(parsed.dt.offset_by("-100y"))
                .otherwise(parsed)
            )
        return parsed
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


# =============================================================================
# Transform: raw delimited source -> typed LazyFrame conforming to the target
# =============================================================================


def read_source_as_text(
    source_file: str | Path,
    schema: CanonicalSchema,
    as_of: str | int,
    *,
    separator: str = ",",
) -> pl.LazyFrame:
    """Read one raw vintage entirely as text, honouring any file-level vintage override.

    Nothing is inferred at read time — the schema is the only authority on dtypes. For
    ``excel`` sources the ``header_row`` override selects which row carries the header
    (the 2020Q1 members workbook puts a title banner above it).

    Args:
        source_file: Path of the raw vintage file.
        schema: The loaded canonical schema.
        as_of: Period token identifying the vintage.
        separator: Field separator, for delimited sources.

    Returns:
        The vintage read entirely as text, with no dtype inference.
    """
    header_row = int(schema.override(as_of, "header_row", 0))
    if schema.source_kind == "excel":
        import pandas as pd

        pdf = pd.read_excel(source_file, header=header_row, dtype=str)
        pdf.columns = [str(c) for c in pdf.columns]
        return pl.from_pandas(pdf.astype(object).where(pdf.notna(), None)).lazy()
    return pl.scan_csv(
        source_file,
        separator=separator,
        infer_schema_length=0,  # everything as text; the schema does the typing
        truncate_ragged_lines=True,
        skip_rows=header_row,
    )


def transform_named_canonical(
    source_file: str | Path,
    schema: CanonicalSchema,
    as_of: str | int,
    *,
    separator: str = ",",
) -> pl.LazyFrame:
    """Parse one raw vintage into the canonical target schema.

    The whole file is read as text so the schema — not the reader's inference — decides
    every dtype. Fields the vintage does not carry are emitted as typed nulls, so all
    vintages share one schema and concatenate without relaxation.

    Args:
        source_file: The raw file for this vintage (CSV or Excel per ``source_kind``).
        schema: The loaded canonical schema.
        as_of: The vintage period (``YYYY`` or ``YYYYQN``).
        separator: Field separator, for delimited sources.

    Returns:
        A LazyFrame whose schema is exactly `CanonicalSchema.target_schema`.
    """
    lf = read_source_as_text(source_file, schema, as_of, separator=separator)
    present = {name.strip().lstrip("﻿"): name for name in lf.collect_schema().names()}

    exprs: list[pl.Expr] = []
    for field in schema.fields:
        dtype = _ARROW_TO_POLARS[field["arrow_type"]]
        layout = schema.active_layout(field, as_of)
        raw_name = present.get(layout["source"]) if layout is not None else None
        if layout is None or raw_name is None:
            exprs.append(pl.lit(None, dtype=dtype).alias(field["id"]))
            continue
        token = pl.col(raw_name).cast(pl.String).str.strip_chars()
        exprs.append(
            _decode_expr(token, field, layout).cast(dtype, strict=False).alias(field["id"])
        )
    return lf.select(exprs)


# =============================================================================
# Validator (deterministic — structural, then against real raw data)
# =============================================================================


def validate_structural(schema: CanonicalSchema) -> list[str]:
    """Run the structural checks that need no data.

    Args:
        schema: The canonical schema to check.

    Returns:
        The problems found; an empty list means the schema passed.
    """
    problems: list[str] = []

    ids = schema.field_ids()
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate field ids {dupes}")

    for field in schema.fields:
        fid = field["id"]
        if field["arrow_type"] not in _ARROW_TO_POLARS:
            problems.append(f"{fid}: unknown arrow_type {field['arrow_type']!r}")
        if not field.get("layouts"):
            problems.append(f"{fid}: no layouts")
            continue

        wins = [
            (
                _period(lay.get("from"), schema.period_grain),
                _period(lay.get("to"), schema.period_grain),
            )
            for lay in field["layouts"]
        ]
        wins_sorted = sorted(wins, key=lambda w: w[0] or "")
        for (_lo1, hi1), (lo2, _hi2) in zip(wins_sorted, wins_sorted[1:]):
            if hi1 is None or (lo2 is not None and lo2 <= hi1):
                problems.append(f"{fid}: overlapping/unbounded windows {wins}")
                break

        for layout in field["layouts"]:
            if not layout.get("source") and not field.get("derived"):
                problems.append(f"{fid}: layout {layout.get('from')} has no source column")
            if (
                field["arrow_type"] == "date32"
                and not field.get("derived")
                and not (layout.get("pattern") or (field.get("decode") or {}).get("pattern"))
            ):
                problems.append(f"{fid}: date layout {layout.get('from')} has no pattern")

    return problems


def validate_against_raw(
    schema: CanonicalSchema,
    raw_files: dict[str, Path],
    *,
    separator: str = ",",
) -> list[str]:
    """Check the schema against real raw files.

    Three checks per vintage: every declared source column for that vintage exists in the
    file; every column in the file is claimed by some field (nothing silently dropped); and
    every field's decode actually parses — a non-string field whose tokens are all non-blank
    but whose decoded column is all-null indicates a wrong ``arrow_type`` or ``pattern``.

    A field declaring ``"all_sentinel": true`` inverts that last check: it is *asserted* to
    decode to all-null in every vintage, and the guard fires when a real value appears. That
    is the signal the column has started carrying information and the declaration is stale.

    Args:
        schema: The loaded canonical schema.
        raw_files: Vintage period -> raw file path.
        separator: Field separator of the raw files.

    Returns:
        The problems found; an empty list means the schema passed.
    """
    problems: list[str] = []

    for as_of, path in sorted(raw_files.items()):
        if not path.exists():
            problems.append(f"{as_of}: raw file missing ({path.name})")
            continue

        lf = read_source_as_text(path, schema, as_of, separator=separator)
        by_stripped = {n.strip().lstrip("﻿"): n for n in lf.collect_schema().names()}
        present = set(by_stripped)

        active = [(f, lay) for f, lay in schema.active_fields(as_of) if not f.get("derived")]
        declared = {lay["source"] for _f, lay in active}
        # Columns explicitly declared as junk for this vintage (stray spreadsheet cells).
        declared |= set(schema.override(as_of, "ignore_columns", []) or [])

        for missing in sorted(declared - present):
            problems.append(f"{as_of}: declared source column {missing!r} not in file")
        for unclaimed in sorted(present - declared):
            problems.append(f"{as_of}: file column {unclaimed!r} claimed by no field")

        typed = transform_named_canonical(path, schema, as_of, separator=separator)
        checks = []
        for field, layout in active:
            if field["arrow_type"] == "string" or layout["source"] not in present:
                continue
            checks.append(
                pl.col(by_stripped[layout["source"]])
                .cast(pl.String)
                .str.strip_chars()
                .replace("", None)
                .is_not_null()
                .sum()
                .alias(f"raw::{field['id']}")
            )
        if not checks:
            continue
        raw_counts = lf.select(checks).collect().to_dicts()[0]
        typed_counts = (
            typed.select(
                [
                    pl.col(f["id"]).is_not_null().sum().alias(f"typed::{f['id']}")
                    for f, lay in active
                    if f["arrow_type"] != "string" and lay["source"] in present
                ]
            )
            .collect()
            .to_dicts()[0]
        )
        for field, _layout in active:
            fid = field["id"]
            r = raw_counts.get(f"raw::{fid}")
            t = typed_counts.get(f"typed::{fid}")
            if r is None or t is None:
                continue
            if field.get("all_sentinel"):
                if t > 0:
                    problems.append(
                        f"{as_of}: {fid} is declared all_sentinel but decodes {t} real values "
                        f"— the declaration is stale"
                    )
            elif r > 0 and t == 0:
                problems.append(f"{as_of}: {fid} decodes to all-null from {r} non-blank tokens")

    return problems
