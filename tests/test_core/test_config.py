"""Tests for core configuration module."""

from pathlib import Path

from mortgage_data_manager.core.config import MortgageDataConfig


def test_get_subpackage_data_dir():
    """Test getting subpackage data directory."""
    hmda_dir = MortgageDataConfig.get_subpackage_data_dir("hmda")
    assert hmda_dir.name == "hmda"
    assert "data" in str(hmda_dir)


def test_get_medallion_dir():
    """Test getting medallion stage directory."""
    silver_dir = MortgageDataConfig.get_medallion_dir("hmda", "silver")
    assert "hmda" in str(silver_dir)
    assert "silver" in str(silver_dir)


def test_get_medallion_dir_all_stages():
    """Test getting medallion directories for all stages."""
    stages = ["raw", "bronze", "silver", "gold"]

    for stage in stages:
        stage_dir = MortgageDataConfig.get_medallion_dir("fha", stage)
        assert stage in str(stage_dir)
        assert "fha" in str(stage_dir)


def test_ensure_directories_creates_root(temp_project_dir, monkeypatch):
    """Test that ensure_directories creates root directories."""
    # Config paths resolve at import time, so the env var won't take effect on the
    # already-imported module — point DATA_DIR at the temp tree directly instead.
    data_dir = temp_project_dir / "fresh_data"
    monkeypatch.setattr(MortgageDataConfig, "DATA_DIR", data_dir)

    MortgageDataConfig.ensure_directories()

    assert data_dir.exists()


def test_ensure_directories_creates_data_root(temp_project_dir, monkeypatch):
    """Test that ensure_directories creates the data root."""
    data_dir = temp_project_dir / "data"
    monkeypatch.setattr(MortgageDataConfig, "DATA_DIR", data_dir)

    MortgageDataConfig.ensure_directories()

    assert data_dir.is_dir()


def test_get_project_root():
    """Test getting project root."""
    root = MortgageDataConfig.get_project_root()
    assert isinstance(root, Path)
    assert root == MortgageDataConfig.PROJECT_DIR


def test_validate_paths(temp_project_dir, monkeypatch):
    """Test path validation."""
    monkeypatch.setattr(MortgageDataConfig, "DATA_DIR", temp_project_dir / "data")

    assert MortgageDataConfig.validate_paths() is True
