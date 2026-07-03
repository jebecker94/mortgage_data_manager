"""Shared pytest fixtures for mortgage data manager tests.

This module provides fixtures that can be used across all test modules.
"""

import shutil
import tempfile
from pathlib import Path

import polars as pl
import pytest


@pytest.fixture
def temp_project_dir():
    """Create temporary project directory structure.

    Yields:
        Path to temporary project directory

    Example:
        >>> def test_something(temp_project_dir):
        ...     raw_dir = temp_project_dir / "data" / "hmda" / "raw"
        ...     assert raw_dir.exists()
    """
    temp_dir = Path(tempfile.mkdtemp())

    # Create subdirectories
    (temp_dir / "data" / "hmda" / "raw").mkdir(parents=True)
    (temp_dir / "data" / "hmda" / "bronze").mkdir(parents=True)
    (temp_dir / "data" / "hmda" / "silver").mkdir(parents=True)
    (temp_dir / "data" / "fha" / "raw").mkdir(parents=True)
    (temp_dir / "data" / "fha" / "bronze").mkdir(parents=True)
    (temp_dir / "data" / "fha" / "silver").mkdir(parents=True)

    yield temp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_hmda_data():
    """Generate small sample HMDA dataset for testing.

    Returns:
        Polars DataFrame with sample HMDA data

    Example:
        >>> def test_hmda_import(sample_hmda_data):
        ...     assert len(sample_hmda_data) > 0
        ...     assert "activity_year" in sample_hmda_data.columns
    """
    return pl.DataFrame({
        "activity_year": [2020, 2020, 2021],
        "lei": ["ABC123", "DEF456", "ABC123"],
        "loan_amount": [250000, 180000, 300000],
        "interest_rate": [3.5, 3.25, 3.75],
        "state_code": ["CA", "NY", "CA"],
        "county_code": ["037", "061", "037"],
        "census_tract": ["980100", "010100", "980200"],
        "action_taken": [1, 1, 1],
        "loan_type": [2, 2, 2],
        "loan_purpose": [1, 1, 3],
    })


@pytest.fixture
def sample_fha_data():
    """Generate small sample FHA dataset for testing.

    Returns:
        Polars DataFrame with sample FHA data

    Example:
        >>> def test_fha_import(sample_fha_data):
        ...     assert len(sample_fha_data) > 0
        ...     assert "Year" in sample_fha_data.columns
    """
    return pl.DataFrame({
        "Year": [2020, 2020, 2021],
        "Month": [6, 7, 6],
        "Mortgage Amount": [250000, 180000, 300000],
        "Interest Rate": [3.5, 3.25, 3.75],
        "Property State": ["CA", "NY", "CA"],
        "Property Zip": [90001, 10001, 90002],
        "Loan Type": ["Purchase", "Purchase", "Refinance"],
    })


@pytest.fixture
def temp_parquet_file(temp_project_dir, sample_hmda_data):
    """Create a temporary parquet file with sample data.

    Args:
        temp_project_dir: Temporary project directory fixture
        sample_hmda_data: Sample HMDA data fixture

    Returns:
        Path to the created parquet file

    Example:
        >>> def test_read_parquet(temp_parquet_file):
        ...     df = pl.read_parquet(temp_parquet_file)
        ...     assert len(df) > 0
    """
    parquet_path = temp_project_dir / "test_data.parquet"
    sample_hmda_data.write_parquet(parquet_path)
    return parquet_path


@pytest.fixture
def mock_config(temp_project_dir, monkeypatch):
    """Mock configuration to use temporary directories.

    Args:
        temp_project_dir: Temporary project directory fixture
        monkeypatch: Pytest monkeypatch fixture

    Example:
        >>> def test_config(mock_config):
        ...     from mortgage_data_manager.core.config import MortgageDataConfig
        ...     assert MortgageDataConfig.PROJECT_DIR.exists()
    """
    # Set environment variables to use temp directory
    monkeypatch.setenv("MORTGAGE_DATA_PROJECT_DIR", str(temp_project_dir))
    monkeypatch.setenv("MORTGAGE_DATA_DIR", str(temp_project_dir / "data"))
    monkeypatch.setenv("MORTGAGE_OUTPUT_DIR", str(temp_project_dir / "output"))

    return temp_project_dir


@pytest.fixture
def sample_csv_file(temp_project_dir):
    """Create a temporary CSV file with sample data.

    Args:
        temp_project_dir: Temporary project directory fixture

    Returns:
        Path to the created CSV file

    Example:
        >>> def test_csv_reading(sample_csv_file):
        ...     df = pl.read_csv(sample_csv_file)
        ...     assert len(df) == 3
    """
    csv_path = temp_project_dir / "test_data.csv"

    df = pl.DataFrame({
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Charlie"],
        "value": [100, 200, 300],
    })

    df.write_csv(csv_path)
    return csv_path
