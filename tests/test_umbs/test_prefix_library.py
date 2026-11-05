"""Tests for the Freddie Prefix Library pool class (``umbs.prefix_library``)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.umbs.prefix_library import (
    acquisition_type_expr,
    load_prefix_ranges,
)


def test_committed_table_loads_and_has_no_overlaps() -> None:
    ranges = load_prefix_ranges()
    assert set(ranges["prefix"]) == {"CL", "CT", "CI", "CN"}
    assert set(ranges["acquisition_type"]) == {"Cash", "MultiLender", "Guar"}


def test_cl_pool_classes() -> None:
    pools = pl.DataFrame(
        {
            "Prefix": ["CL", "CL", "CL", "CL", "CL", "CL", "CL", "CI", "CL", "3P"],
            "Security Identifier": [
                "RA0999",  # MultiLender (RA0000-RA0999)
                "RA1000",  # Cash (RA1000-RA9999)
                "RJ0042",  # Cash
                "RQ0187",  # MultiLender
                "QJ9999",  # Guar (QA0000-QJ9999)
                "TH0001",  # Guar (TC0000-TH9999)
                "SD8001",  # Super: no class
                "RA1000",  # CI has no RA range
                "RG5000",  # MultiLender (RG5000-RG9999)
                "QU3000",  # prefix outside the table
            ],
        }
    )
    got = pools.select(acquisition_type_expr())["acquisition_type"].to_list()
    assert got == [
        "MultiLender",
        "Cash",
        "Cash",
        "MultiLender",
        "Guar",
        "Guar",
        None,
        None,
        "MultiLender",
        None,
    ]


def test_overlapping_ranges_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "ranges.csv"
    bad.write_text(
        "prefix,pool_number_first,pool_number_last,pool_term,description,acquisition_type\n"
        "CL,RA0000,RA1000,30 Year,a,MultiLender\n"
        "CL,RA1000,RA9999,30 Year,b,Cash\n"
    )
    with pytest.raises(ValueError, match="Overlapping"):
        load_prefix_ranges.__wrapped__(bad)
