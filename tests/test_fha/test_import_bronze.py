"""Unit tests for the FHA bronze cast-loss detector.

A vintage delivering a numeric field as text (e.g. ``%``-suffixed interest
rates) loses every unparseable value to the ``strict=False`` cast with no
trace. The detector does not change what is stored — it names what the cast
destroyed.
"""

from __future__ import annotations

import logging

import polars as pl

from mortgage_data_manager.sources.fha.import_bronze import _cast_numeric_reporting_loss

_LOGGER_NAME = "mortgage_data_manager.sources.fha.import_bronze"


def test_cast_loss_logged_as_error_for_unexpected_column(caplog) -> None:
    df = pl.DataFrame({"Interest Rate": ["3.25", "4.75%", "5.00", "n/a"]})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        out = _cast_numeric_reporting_loss(df, "Interest Rate")

    # cast semantics unchanged: parseable values survive, text becomes null
    assert out["Interest Rate"].to_list() == [3.25, None, 5.00, None]
    records = [r for r in caplog.records if "Numeric cast" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    assert "2 of 4" in records[0].getMessage()
    assert "4.75%" in records[0].getMessage()  # offending values are named


def test_cast_loss_downgraded_for_expected_columns(caplog) -> None:
    # "Not Available" is long-standing legitimate content in the identifier
    # columns; the allowlist keeps it out of the error log.
    df = pl.DataFrame({"Sponsor Number": ["12345", "Not Available"]})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        out = _cast_numeric_reporting_loss(df, "Sponsor Number")

    assert out["Sponsor Number"].to_list() == [12345.0, None]
    records = [r for r in caplog.records if "Numeric cast" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.DEBUG


def test_clean_cast_logs_nothing(caplog) -> None:
    df = pl.DataFrame({"Mortgage Amount": ["100000", "250000", None]})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        out = _cast_numeric_reporting_loss(df, "Mortgage Amount")

    assert out["Mortgage Amount"].to_list() == [100000.0, 250000.0, None]
    assert not [r for r in caplog.records if "Numeric cast" in r.getMessage()]
