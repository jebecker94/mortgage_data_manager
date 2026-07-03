"""Tests for GNMA configuration module.

Tests for Phase 1 of the GNMA refactoring - configuration inheritance.
"""

from __future__ import annotations

import warnings
from pathlib import Path


class TestGNMAConfig:
    """Tests for GNMAConfig class."""

    def test_gnma_config_inherits_from_base(self):
        """Test that GNMAConfig inherits from MortgageDataConfig."""
        from mortgage_data_manager.core.config import MortgageDataConfig
        from mortgage_data_manager.gnma.config import GNMAConfig

        assert issubclass(GNMAConfig, MortgageDataConfig)
        assert GNMAConfig.__bases__[0] is MortgageDataConfig

    def test_gnma_config_get_prefix_dir(self):
        """Test get_prefix_dir resolves per-prefix sub-directories."""
        from mortgage_data_manager.gnma.config import GNMAConfig

        # Bare stage directories come from the per-dir attributes
        # (get_medallion_dir is now the inherited base API).
        assert GNMAConfig.GNMA_RAW_DIR.name == "raw"
        assert GNMAConfig.GNMA_BRONZE_DIR.name == "bronze"
        assert GNMAConfig.GNMA_SILVER_DIR.name == "silver"

        # Prefix axis routes through get_prefix_dir
        raw_monthly = GNMAConfig.get_prefix_dir("raw", "monthly")
        assert raw_monthly == GNMAConfig.GNMA_RAW_DIR / "monthly"

        silver_llmon1 = GNMAConfig.get_prefix_dir("silver", "llmon1")
        assert silver_llmon1 == GNMAConfig.GNMA_SILVER_DIR / "llmon1"

    def test_gnma_config_ensure_directories(self, gnma_temp_dir, monkeypatch):
        """Test ensure_directories creates required directories."""
        monkeypatch.setenv("GNMA_DATA_DIR", str(gnma_temp_dir))
        monkeypatch.setenv("GNMA_RAW_DIR", str(gnma_temp_dir / "raw"))
        monkeypatch.setenv("GNMA_BRONZE_DIR", str(gnma_temp_dir / "bronze"))
        monkeypatch.setenv("GNMA_SILVER_DIR", str(gnma_temp_dir / "silver"))
        monkeypatch.setenv("GNMA_SCHEMAS_DIR", str(gnma_temp_dir / "schemas"))

        # Reimport after setting env vars
        import importlib

        import mortgage_data_manager.gnma.config as config_module

        importlib.reload(config_module)

        config_module.GNMAConfig.ensure_directories()

        # Verify directories exist (at least base ones)
        assert (gnma_temp_dir / "raw").exists()
        assert (gnma_temp_dir / "bronze").exists()
        assert (gnma_temp_dir / "silver").exists()

    def test_backward_compatibility_exports(self):
        """Test backward compatibility module-level exports."""
        from mortgage_data_manager.gnma.config import DATA_ROOT, GNMA_DATA, PROJECT_ROOT, GNMAConfig

        # These should be equal to GNMAConfig class attributes
        assert PROJECT_ROOT == GNMAConfig.PROJECT_DIR
        assert DATA_ROOT == GNMAConfig.DATA_DIR
        assert GNMA_DATA == GNMAConfig.GNMA_DATA_DIR


class TestProcessorConfig:
    """Tests for ProcessorConfig dataclass."""

    def test_processor_config_defaults(self):
        """Test ProcessorConfig default values."""
        from mortgage_data_manager.gnma.config import GNMAConfig, ProcessorConfig

        config = ProcessorConfig()

        assert config.raw_folder == GNMAConfig.GNMA_RAW_DIR
        assert config.bronze_folder == GNMAConfig.GNMA_BRONZE_DIR
        assert config.silver_folder == GNMAConfig.GNMA_SILVER_DIR
        assert config.schema_folder == GNMAConfig.GNMA_SCHEMA_COMBINED_DIR
        assert config.prefix_file == GNMAConfig.GNMA_PREFIX_DICTIONARY
        assert config.schemas_folder == GNMAConfig.GNMA_SCHEMAS_DIR

    def test_processor_config_clean_folder_deprecated(self):
        """Test that clean_folder property emits deprecation warning."""
        from mortgage_data_manager.gnma.config import ProcessorConfig

        config = ProcessorConfig()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = config.clean_folder
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "clean_folder" in str(w[0].message)

    def test_processor_config_custom_values(self, gnma_temp_dir):
        """Test ProcessorConfig with custom values."""
        from mortgage_data_manager.gnma.config import ProcessorConfig

        config = ProcessorConfig(
            raw_folder=gnma_temp_dir / "custom_raw",
            bronze_folder=gnma_temp_dir / "custom_bronze",
            silver_folder=gnma_temp_dir / "custom_silver",
            skip_existing=False,
        )

        assert config.raw_folder == gnma_temp_dir / "custom_raw"
        assert config.bronze_folder == gnma_temp_dir / "custom_bronze"
        assert config.silver_folder == gnma_temp_dir / "custom_silver"
        assert config.skip_existing is False


class TestDeprecatedFunctions:
    """Tests for deprecated configuration functions."""

    def test_get_project_root_deprecated(self):
        """Test that get_project_root emits deprecation warning."""
        from mortgage_data_manager.gnma.config import get_project_root

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = get_project_root()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "get_project_root" in str(w[0].message)
            assert isinstance(result, Path)

    def test_get_data_root_deprecated(self):
        """Test that get_data_root emits deprecation warning."""
        from mortgage_data_manager.gnma.config import get_data_root

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = get_data_root()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "get_data_root" in str(w[0].message)
            assert isinstance(result, Path)
