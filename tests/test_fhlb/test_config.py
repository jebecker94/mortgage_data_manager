"""Tests for FHLB configuration."""

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.fhlb.config import FHLBConfig


def test_fhlb_config_inherits_from_base():
    """Test that FHLB config inherits from MortgageDataConfig."""
    assert issubclass(FHLBConfig, MortgageDataConfig)


def test_fhlb_config_has_defaults():
    """Test that FHLB config exposes a sane default year range.

    Asserts the attributes exist and form a valid range rather than pinning
    exact literals: the defaults track data availability and move over time
    (FHLB_DEFAULT_MAX_YEAR has already advanced past the original 2023).
    """
    assert hasattr(FHLBConfig, 'FHLB_DEFAULT_MIN_YEAR')
    assert hasattr(FHLBConfig, 'FHLB_DEFAULT_MAX_YEAR')

    assert isinstance(FHLBConfig.FHLB_DEFAULT_MIN_YEAR, int)
    assert isinstance(FHLBConfig.FHLB_DEFAULT_MAX_YEAR, int)
    assert FHLBConfig.FHLB_DEFAULT_MIN_YEAR <= FHLBConfig.FHLB_DEFAULT_MAX_YEAR


def test_fhlb_ensure_directories_creates_all_dirs(temp_project_dir, monkeypatch):
    """Test that FHLB ensure_directories creates all medallion directories and subdirs.

    Gold is deliberately excluded: per the project convention, gold directories
    are never pre-created (writers mkdir on demand), so ensure_directories does
    not make one.
    """
    # Config paths resolve at import time; patch the FHLB dirs to the temp tree
    # directly (env vars won't take effect on the already-imported module).
    fhlb_dir = temp_project_dir / "data" / "fhlb"
    monkeypatch.setattr(FHLBConfig, "FHLB_RAW_DIR", fhlb_dir / "raw")
    monkeypatch.setattr(FHLBConfig, "FHLB_BRONZE_DIR", fhlb_dir / "bronze")
    monkeypatch.setattr(FHLBConfig, "FHLB_SILVER_DIR", fhlb_dir / "silver")
    monkeypatch.setattr(FHLBConfig, "FHLB_RAW_MEMBERS_DIR", fhlb_dir / "raw" / "members")
    monkeypatch.setattr(FHLBConfig, "FHLB_RAW_AMA_DIR", fhlb_dir / "raw" / "ama")

    FHLBConfig.ensure_directories()

    assert FHLBConfig.FHLB_RAW_DIR.exists()
    assert FHLBConfig.FHLB_BRONZE_DIR.exists()
    assert FHLBConfig.FHLB_SILVER_DIR.exists()
    assert FHLBConfig.FHLB_RAW_MEMBERS_DIR.exists()
    assert FHLBConfig.FHLB_RAW_AMA_DIR.exists()

    assert callable(FHLBConfig.ensure_directories)
