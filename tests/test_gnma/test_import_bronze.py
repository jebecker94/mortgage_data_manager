"""Tests for GNMA import_bronze module.

Tests for converting raw data files to parquet (raw → bronze layer).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl


class TestGetOutputPath:
    """Tests for _get_output_path function."""

    def test_default_output_to_bronze(self, gnma_temp_dir, monkeypatch):
        """Test that default output path goes to bronze layer."""
        monkeypatch.setenv("GNMA_BRONZE_DIR", str(gnma_temp_dir / "bronze"))

        # Reimport to pick up env var
        import importlib

        import mortgage_data_manager.gnma.config as config_module

        importlib.reload(config_module)

        from mortgage_data_manager.gnma.import_bronze import _get_output_path

        input_path = Path("/data/gnma/raw/monthly/monthly_202401.zip")
        raw_folder = Path("/data/gnma/raw")

        output = _get_output_path(input_path, None, raw_folder)

        # Should contain bronze in path
        assert "bronze" in str(output)
        assert output.suffix == ".parquet"

    def test_custom_output_folder(self, gnma_temp_dir):
        """Test with custom output folder."""
        from mortgage_data_manager.gnma.import_bronze import _get_output_path

        input_path = Path("/data/gnma/raw/monthly/monthly_202401.zip")
        custom_output = gnma_temp_dir / "custom_output"
        raw_folder = Path("/data/gnma/raw")

        output = _get_output_path(input_path, custom_output, raw_folder)

        assert str(output).startswith(str(custom_output))
        assert output.suffix == ".parquet"

    def test_preserves_prefix_structure(self, gnma_temp_dir):
        """Test that prefix subfolder structure is preserved."""
        from mortgage_data_manager.gnma.import_bronze import _get_output_path

        input_path = gnma_temp_dir / "raw" / "monthly" / "monthly_202401.zip"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.touch()

        raw_folder = gnma_temp_dir / "raw"
        output_folder = gnma_temp_dir / "bronze"

        output = _get_output_path(input_path, output_folder, raw_folder)

        # Should preserve 'monthly' prefix folder
        assert "monthly" in str(output)
        assert output.name == "monthly_202401.parquet"


class TestImportZipToParquet:
    """Tests for import_zip_to_parquet function."""

    def test_stage_zip_creates_parquet(self, gnma_temp_dir, sample_gnma_text_content):
        """Test that staging a ZIP file creates a parquet file."""
        import zipfile

        from mortgage_data_manager.gnma.config import ProcessorConfig
        from mortgage_data_manager.gnma.import_bronze import import_zip_to_parquet

        # Create a test ZIP file
        zip_path = gnma_temp_dir / "raw" / "monthly" / "monthly_202401.zip"
        txt_path = gnma_temp_dir / "monthly_202401.txt"
        txt_path.write_text(sample_gnma_text_content)

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(txt_path, "monthly_202401.txt")

        # Stage it
        output_path = gnma_temp_dir / "bronze" / "monthly" / "monthly_202401.parquet"
        config = ProcessorConfig(
            raw_folder=gnma_temp_dir / "raw",
            bronze_folder=gnma_temp_dir / "bronze",
        )

        result = import_zip_to_parquet(zip_path, output_path, config)

        assert result is True
        assert output_path.exists()

        # Verify parquet content
        df = pl.read_parquet(output_path)
        assert "text_content" in df.columns
        assert len(df) > 0

    def test_skip_existing_file(self, gnma_temp_dir):
        """Test that existing files are skipped when skip_existing=True."""
        import zipfile

        from mortgage_data_manager.gnma.config import ProcessorConfig
        from mortgage_data_manager.gnma.import_bronze import import_zip_to_parquet

        # Create a test ZIP file
        zip_path = gnma_temp_dir / "raw" / "monthly" / "monthly_202401.zip"
        txt_content = "test content"
        txt_path = gnma_temp_dir / "test.txt"
        txt_path.write_text(txt_content)

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(txt_path, "monthly_202401.txt")

        # Create existing output file
        output_path = gnma_temp_dir / "bronze" / "monthly" / "monthly_202401.parquet"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write dummy parquet
        pl.DataFrame({"text_content": ["existing"]}).write_parquet(output_path)

        config = ProcessorConfig(
            raw_folder=gnma_temp_dir / "raw",
            bronze_folder=gnma_temp_dir / "bronze",
            skip_existing=True,
        )

        result = import_zip_to_parquet(zip_path, output_path, config)

        # Should return True (skipped successfully)
        assert result is True

        # Content should be unchanged
        df = pl.read_parquet(output_path)
        assert df["text_content"][0] == "existing"


class TestImportTxtToParquet:
    """Tests for import_txt_to_parquet function."""

    def test_stage_txt_creates_parquet(self, gnma_temp_dir, sample_gnma_text_content):
        """Test that staging a TXT file creates a parquet file."""
        from mortgage_data_manager.gnma.config import ProcessorConfig
        from mortgage_data_manager.gnma.import_bronze import import_txt_to_parquet

        # Create a test TXT file
        txt_path = gnma_temp_dir / "raw" / "monthly" / "monthly_202401.txt"
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(sample_gnma_text_content)

        # Stage it
        output_path = gnma_temp_dir / "bronze" / "monthly" / "monthly_202401.parquet"
        config = ProcessorConfig(
            raw_folder=gnma_temp_dir / "raw",
            bronze_folder=gnma_temp_dir / "bronze",
        )

        result = import_txt_to_parquet(txt_path, output_path, config)

        assert result is True
        assert output_path.exists()

        # Verify parquet content
        df = pl.read_parquet(output_path)
        assert "text_content" in df.columns
        assert len(df) == len(sample_gnma_text_content.split("\n"))
