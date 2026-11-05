"""Regression tests for the FNMA SF silver writer."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from mortgage_data_manager.sources.fnma.config import FNMA_SF_SILVER
from mortgage_data_manager.sources.fnma.import_silver import process_file


def test_failed_terminations_leave_no_issuances(tmp_path: Path) -> None:
    # build_silver's skip guard keys on the issuances file, so a quarter whose
    # terminations step fails must not publish issuances: the next run would skip it.
    bronze = tmp_path / "2020Q1.parquet"
    pl.DataFrame({"Loan Identifier": ["1"], "Monthly Reporting Period": ["012020"]}).write_parquet(
        bronze
    )  # no "Zero Balance Code": the terminations filter raises
    iss, term = tmp_path / "iss", tmp_path / "term"
    iss.mkdir()
    term.mkdir()

    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        process_file(bronze, iss, term)

    assert list(iss.iterdir()) == []
    assert list(term.iterdir()) == []


def test_loan_purpose_has_no_trailing_space() -> None:
    names = FNMA_SF_SILVER.target.names()
    assert "Loan Purpose" in names
    assert "Loan Purpose " not in names
    assert FNMA_SF_SILVER.renames == {"Loan Purpose ": "Loan Purpose"}
