"""GNMA-specific pytest fixtures.

This module provides fixtures for testing the GNMA module.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

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
