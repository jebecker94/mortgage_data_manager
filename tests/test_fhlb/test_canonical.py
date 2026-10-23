"""Unit tests + data guards for the FHLB canonical schemas (AMA + members).

Split by the two kinds the suite already distinguishes:

* **Unit tests** build their schema inline and run anywhere — they pin the window-selection
  and decode logic, which is pure and ours.
* **Data guards** read the real raw vintages and the committed ``ama.json`` /
  ``members.json``; they assert claims about the *world* (every source column is accounted
  for, every vintage conforms to one schema) and skip when the data is not mounted.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.fhlb.canonical import (
    CanonicalSchema,
    transform_named_canonical,
    validate_against_raw,
    validate_structural,
)
from mortgage_data_manager.sources.fhlb.config import FHLBConfig
from mortgage_data_manager.sources.fhlb.import_bronze import _members_year_quarter

_CANON_DIR = FHLBConfig.FHLB_SCHEMAS_DIR / "canonical"
_RAW_AMA = FHLBConfig.FHLB_RAW_AMA_DIR
_AMA_JSON = _CANON_DIR / "ama.json"
_RAW_MEMBERS = FHLBConfig.FHLB_RAW_MEMBERS_DIR
_MEMBERS_JSON = _CANON_DIR / "members.json"


def _schema(
    fields: list[dict],
    *,
    period_grain: str = "year",
    source_kind: str = "csv",
    vintage_overrides: dict | None = None,
) -> CanonicalSchema:
    """Build an in-memory schema for the unit tests."""
    raw = {
        "dataset": "t",
        "layout_kind": "named",
        "period_grain": period_grain,
        "source_kind": source_kind,
        "fields": fields,
    }
    return CanonicalSchema(
        dataset="t",
        layout_kind="named",
        period_grain=period_grain,
        source_kind=source_kind,
        fields=fields,
        vintage_overrides=vintage_overrides or {},
        raw=raw,
    )


# --------------------------------------------------------------------------- #
# Unit tests — pure window selection + decode
# --------------------------------------------------------------------------- #
def test_active_layout_picks_the_window_covering_the_period() -> None:
    field = {
        "id": "loan_id",
        "arrow_type": "int64",
        "layouts": [
            {"from": "2009", "to": "2014", "source": "LoanNumber"},
            {"from": "2015", "to": None, "source": "LoanCharacteristicsID"},
        ],
    }
    s = _schema([field])
    assert s.active_layout(field, "2009")["source"] == "LoanNumber"
    assert s.active_layout(field, "2014")["source"] == "LoanNumber"
    assert s.active_layout(field, "2015")["source"] == "LoanCharacteristicsID"
    assert s.active_layout(field, "2024")["source"] == "LoanCharacteristicsID"


def test_field_absent_before_its_first_window_is_not_active() -> None:
    field = {
        "id": "late_field",
        "arrow_type": "int64",
        "layouts": [{"from": "2019", "to": None, "source": "LateField"}],
    }
    s = _schema([field])
    assert s.active_layout(field, "2018") is None
    assert s.active_fields("2018") == []
    assert len(s.active_fields("2019")) == 1


def test_target_schema_is_the_union_and_absent_fields_come_back_typed_null(
    tmp_path: Path,
) -> None:
    """A vintage missing a field still yields the full target schema, not a short frame."""
    s = _schema(
        [
            {
                "id": "a",
                "arrow_type": "int64",
                "layouts": [{"from": "2009", "to": None, "source": "A"}],
            },
            {
                "id": "b",
                "arrow_type": "float64",
                "layouts": [{"from": "2019", "to": None, "source": "B"}],
            },
        ]
    )
    csv_path = tmp_path / "v2009.csv"
    csv_path.write_text("A\n1\n2\n")

    out = transform_named_canonical(csv_path, s, "2009").collect()
    assert out.columns == ["a", "b"]
    assert out.schema == s.target_schema()
    assert out["a"].to_list() == [1, 2]
    assert out["b"].to_list() == [None, None]


def test_date_pattern_is_read_from_the_layout_not_just_the_field(tmp_path: Path) -> None:
    """The same source column can change text format across vintages."""
    field = {
        "id": "expires",
        "arrow_type": "date32",
        "layouts": [
            {"from": "2009", "to": "2023", "source": "Exp", "pattern": "%m/%d/%Y"},
            {"from": "2024", "to": None, "source": "Exp", "pattern": "%Y-%m-%d"},
        ],
    }
    s = _schema([field])

    old = tmp_path / "old.csv"
    old.write_text("Exp\n03/31/2020\n")
    new = tmp_path / "new.csv"
    new.write_text("Exp\n2020-03-31\n")

    got_old = transform_named_canonical(old, s, "2023").collect()["expires"].to_list()
    got_new = transform_named_canonical(new, s, "2024").collect()["expires"].to_list()
    assert got_old == got_new


def test_date_fallback_patterns_cover_mixed_format_cells(tmp_path: Path) -> None:
    """A vintage can mix m/d/yy text with Excel-datetime cells rendered as ISO text."""
    field = {
        "id": "joined",
        "arrow_type": "date32",
        "layouts": [
            {
                "from": "2009",
                "to": None,
                "source": "D",
                "pattern": "%m/%d/%y",
                "fallback_patterns": ["%Y-%m-%d %H:%M:%S"],
            }
        ],
        "decode": {"kind": "date", "century_fix": True},
    }
    s = _schema([field])
    p = tmp_path / "v.csv"
    p.write_text("D\n09/13/94\n1994-09-13 00:00:00\nnot a date\n")
    got = transform_named_canonical(p, s, "2016").collect()["joined"].to_list()
    assert got[0] == got[1]  # both parse, to the same (century-fixed) date
    assert str(got[0]) == "1994-09-13"
    assert got[2] is None


def test_null_values_are_mapped_before_the_cast(tmp_path: Path) -> None:
    field = {
        "id": "expires",
        "arrow_type": "date32",
        "layouts": [{"from": "2009", "to": None, "source": "Exp", "pattern": "%m/%d/%Y"}],
        "null_values": ["12/31/9999"],
    }
    s = _schema([field])
    p = tmp_path / "v.csv"
    p.write_text("Exp\n12/31/9999\n03/31/2020\n")
    got = transform_named_canonical(p, s, "2009").collect()["expires"].to_list()
    assert got[0] is None
    assert got[1] is not None


def test_string_values_are_stripped(tmp_path: Path) -> None:
    """Trailing whitespace must not create a second category (FHLB 2022 'Dallas ')."""
    s = _schema(
        [
            {
                "id": "bank",
                "arrow_type": "string",
                "layouts": [{"from": "2009", "to": None, "source": "Bank"}],
            }
        ]
    )
    p = tmp_path / "v.csv"
    p.write_text("Bank\nDallas \nDallas\n")
    got = transform_named_canonical(p, s, "2022").collect()["bank"].to_list()
    assert got == ["Dallas", "Dallas"]


def test_validate_structural_flags_overlapping_windows() -> None:
    s = _schema(
        [
            {
                "id": "x",
                "arrow_type": "int64",
                "layouts": [
                    {"from": "2009", "to": "2015", "source": "X"},
                    {"from": "2014", "to": None, "source": "X2"},
                ],
            }
        ]
    )
    assert any("overlapping" in p for p in validate_structural(s))


def test_validate_structural_flags_a_date_without_a_pattern() -> None:
    s = _schema(
        [
            {
                "id": "d",
                "arrow_type": "date32",
                "layouts": [{"from": "2009", "to": None, "source": "D"}],
            }
        ]
    )
    assert any("no pattern" in p for p in validate_structural(s))


# --------------------------------------------------------------------------- #
# Data guards — the committed ama.json against the real raw vintages
# --------------------------------------------------------------------------- #
def _raw_ama_files() -> dict[str, Path]:
    """Vintage year -> raw AMA CSV."""

    def year_of(name: str) -> int:
        m = re.match(r"(\d{4})[-_]", name)
        if m:
            return int(m.group(1))
        m = re.search(r"_(\d{2})(\d{2})(\d{2})\.csv$", name)
        if m:
            return 2000 + int(m.group(3))
        raise ValueError(name)

    return {str(year_of(f.name)): f for f in sorted(_RAW_AMA.glob("*.csv"))}


@pytest.mark.skipif(not _AMA_JSON.exists(), reason="AMA canonical schema not present")
def test_committed_ama_schema_is_structurally_valid() -> None:
    assert validate_structural(CanonicalSchema.load("ama", _CANON_DIR)) == []


@pytest.mark.skipif(
    not _AMA_JSON.exists() or not _RAW_AMA.exists(), reason="AMA raw data not mounted"
)
def test_every_raw_column_in_every_vintage_is_claimed_by_a_field() -> None:
    """No source column is silently dropped, and none is declared but absent.

    This is the guard that fires when FHFA publishes a vintage with a new or renamed
    column: the schema stops covering the file, and the failure names the column.
    """
    schema = CanonicalSchema.load("ama", _CANON_DIR)
    assert validate_against_raw(schema, _raw_ama_files()) == []


@pytest.mark.skipif(
    not _AMA_JSON.exists() or not _RAW_AMA.exists(), reason="AMA raw data not mounted"
)
def test_every_vintage_conforms_to_one_target_schema() -> None:
    """The point of the exercise: 16 vintages, one schema, concatenable without relaxation."""
    schema = CanonicalSchema.load("ama", _CANON_DIR)
    target = schema.target_schema()
    for as_of, path in sorted(_raw_ama_files().items()):
        got = transform_named_canonical(path, schema, as_of).collect_schema()
        assert dict(got) == dict(target), f"{as_of} does not conform to the target schema"


@pytest.mark.skipif(
    not _AMA_JSON.exists() or not _RAW_AMA.exists(), reason="AMA raw data not mounted"
)
def test_canonical_output_has_no_whitespace_padded_string_values() -> None:
    """Regression guard for the 2022 'Dallas ' / 'Topeka ' split found by the pilot."""
    schema = CanonicalSchema.load("ama", _CANON_DIR)
    for as_of, path in sorted(_raw_ama_files().items()):
        df = transform_named_canonical(path, schema, as_of).collect()
        for col, dtype in zip(df.columns, df.dtypes):
            if dtype != pl.String:
                continue
            padded = df.filter(pl.col(col) != pl.col(col).str.strip_chars()).height
            assert padded == 0, f"{as_of}.{col}: {padded} whitespace-padded values"


@pytest.mark.skipif(
    not _AMA_JSON.exists() or not _RAW_AMA.exists(), reason="AMA raw data not mounted"
)
def test_schema_field_count_matches_the_union_of_raw_headers() -> None:
    """Every distinct raw header across all vintages maps to exactly one canonical field."""
    schema = CanonicalSchema.load("ama", _CANON_DIR)
    declared_sources = {
        lay["source"] for f in schema.fields if not f.get("derived") for lay in f["layouts"]
    }

    seen: set[str] = set()
    for path in _raw_ama_files().values():
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            seen |= {h.strip().lstrip("﻿") for h in next(csv.reader(fh))}

    assert seen - declared_sources == set(), "raw headers with no canonical field"
    assert declared_sources - seen == set(), "declared sources that no vintage carries"


# --------------------------------------------------------------------------- #
# Data guards — the committed members.json against the real raw workbooks
# --------------------------------------------------------------------------- #
def _raw_members_files() -> dict[str, Path]:
    """Vintage period (``YYYYQN``) -> raw members workbook."""
    out: dict[str, Path] = {}
    for f in sorted(_RAW_MEMBERS.glob("*.xls*")):
        yq = _members_year_quarter(f.name)
        assert yq is not None, f"unparseable members filename {f.name}"
        out[f"{yq[0]}Q{yq[1]}"] = f
    return out


def test_members_filename_vintage_parsing() -> None:
    """The three published filename patterns, plus a non-match."""
    assert _members_year_quarter("FHLB_Members_Q12020.xlsx") == (2020, 1)
    assert _members_year_quarter("FHLB_Members_Q42023_Release.xlsx") == (2023, 4)
    assert _members_year_quarter("fhlb_members_q42025.xlsx") == (2025, 4)
    assert _members_year_quarter("FHLB-Members-09302018.xlsx") == (2018, 3)
    assert _members_year_quarter("FHLB_Members_12312018.xlsx") == (2018, 4)
    assert _members_year_quarter("FHLB_Members_033114.xls") == (2014, 1)
    assert _members_year_quarter("random.xlsx") is None


@pytest.mark.skipif(not _MEMBERS_JSON.exists(), reason="members canonical schema not present")
def test_committed_members_schema_is_structurally_valid() -> None:
    assert validate_structural(CanonicalSchema.load("members", _CANON_DIR)) == []


@pytest.mark.skipif(
    not _MEMBERS_JSON.exists() or not _RAW_MEMBERS.exists(),
    reason="members raw data not mounted",
)
def test_every_raw_members_column_in_every_vintage_is_claimed_by_a_field() -> None:
    """No workbook column is silently dropped, and none is declared but absent.

    This is the guard that fires when FHFA publishes a quarter with a new or renamed
    column: the schema stops covering the file, and the failure names the column.
    """
    schema = CanonicalSchema.load("members", _CANON_DIR)
    assert validate_against_raw(schema, _raw_members_files()) == []


@pytest.mark.skipif(
    not _MEMBERS_JSON.exists() or not _RAW_MEMBERS.exists(),
    reason="members raw data not mounted",
)
def test_every_members_vintage_conforms_and_strings_are_stripped() -> None:
    """66 quarters, one typed schema, no whitespace-padded string values."""
    schema = CanonicalSchema.load("members", _CANON_DIR)
    target = schema.target_schema()
    for as_of, path in sorted(_raw_members_files().items()):
        df = transform_named_canonical(path, schema, as_of).collect()
        assert dict(df.schema) == dict(target), f"{as_of} does not conform to the target schema"
        for col, dtype in zip(df.columns, df.dtypes):
            if dtype != pl.String:
                continue
            padded = df.filter(pl.col(col) != pl.col(col).str.strip_chars()).height
            assert padded == 0, f"{as_of}.{col}: {padded} whitespace-padded values"
