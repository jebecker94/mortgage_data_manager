"""GNMA-specific pytest fixtures.

This module provides fixtures for testing the GNMA module.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import polars as pl
import pytest


@pytest.fixture
def gnma_temp_dir():
    """Create temporary GNMA directory structure.

    Yields:
        Path to temporary GNMA data directory with medallion structure
    """
    import shutil

    temp_dir = Path(tempfile.mkdtemp())

    # Create medallion directories
    (temp_dir / "raw" / "monthly").mkdir(parents=True)
    (temp_dir / "bronze" / "monthly").mkdir(parents=True)
    (temp_dir / "silver" / "monthly" / "L").mkdir(parents=True)
    (temp_dir / "dictionary_files" / "raw").mkdir(parents=True)
    (temp_dir / "dictionary_files" / "clean").mkdir(parents=True)
    (temp_dir / "dictionary_files" / "combined").mkdir(parents=True)

    yield temp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_gnma_text_content():
    """Generate sample GNMA fixed-width text content.

    Returns:
        Sample text content for testing staging
    """
    # Example fixed-width content (simplified)
    lines = [
        "L202401ABCD123456789012345000025000000350000001",
        "L202401EFGH123456789012345000030000000325000002",
        "L202401IJKL123456789012345000035000000375000003",
    ]
    return "\n".join(lines)


@pytest.fixture
def sample_schema_df():
    """Generate sample schema DataFrame.

    Returns:
        DataFrame with sample schema for testing
    """
    return pd.DataFrame({
        "Data Item": ["Record Type", "Pool Number", "UPB", "Interest Rate", "Filler"],
        "Length": [1, 9, 11, 5, 10],
        "Format": ["X(1)", "X(9)", "9(11)", "9(3)V9(2)", "X(10)"],
        "Description": [
            "Record type indicator",
            "Pool identifier",
            "Unpaid principal balance",
            "Interest rate",
            "Reserved space",
        ],
        "Record_Type": ["L", "L", "L", "L", "L"],
        "Min_Date": ["2024-01-01"] * 5,
        "Max_Date": ["2024-12-31"] * 5,
    })


@pytest.fixture
def sample_cobol_formats():
    """Generate sample COBOL format test cases.

    Returns:
        List of tuples (cobol_notation, expected_result)
    """
    return [
        # String formats
        ("X(9)", {"python_type": str, "length": 9, "cobol_type": "string"}),
        ("X(1)", {"python_type": str, "length": 1, "cobol_type": "string"}),
        # Integer formats
        ("9(13)", {"python_type": int, "length": 13, "cobol_type": "integer"}),
        ("9(5)", {"python_type": int, "length": 5, "cobol_type": "integer"}),
        # Decimal formats
        ("9(13)V9(2)", {"python_type": float, "length": 15, "cobol_type": "decimal", "decimal_places": 2}),
        ("9(9)V9(4)", {"python_type": float, "length": 13, "cobol_type": "decimal", "decimal_places": 4}),
    ]


@pytest.fixture
def sample_gnma_parquet(gnma_temp_dir, sample_gnma_text_content):
    """Create sample GNMA staged parquet file.

    Args:
        gnma_temp_dir: Temporary directory fixture
        sample_gnma_text_content: Sample text content fixture

    Returns:
        Path to the created parquet file
    """
    bronze_dir = gnma_temp_dir / "bronze" / "monthly"
    parquet_path = bronze_dir / "monthly_202401.parquet"

    # Create DataFrame with text_content column (as staging produces)
    df = pl.DataFrame({
        "text_content": sample_gnma_text_content.split("\n"),
    })
    df.write_parquet(parquet_path)

    return parquet_path


@pytest.fixture
def mock_gnma_config(gnma_temp_dir, monkeypatch):
    """Mock GNMAConfig to use temporary directories.

    Args:
        gnma_temp_dir: Temporary directory fixture
        monkeypatch: Pytest monkeypatch fixture

    Returns:
        Temporary directory path
    """
    monkeypatch.setenv("GNMA_DATA_DIR", str(gnma_temp_dir))
    monkeypatch.setenv("GNMA_RAW_DIR", str(gnma_temp_dir / "raw"))
    monkeypatch.setenv("GNMA_BRONZE_DIR", str(gnma_temp_dir / "bronze"))
    monkeypatch.setenv("GNMA_SILVER_DIR", str(gnma_temp_dir / "silver"))
    monkeypatch.setenv("GNMA_DICTIONARY_DIR", str(gnma_temp_dir / "dictionary_files"))

    return gnma_temp_dir
