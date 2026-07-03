"""Unit test for ``core.extract.is_archive`` suffix classification (incl. compound extensions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mortgage_data_manager.core.extract import is_archive


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("data.zip", True),
        ("data.tar.gz", True),
        ("data.tgz", True),
        ("data.tar.bz2", True),   # bz2 counts only as part of a .tar.bz2 compound
        ("data.csv", False),
        ("data.bz2", False),      # bare bz2 has no extractable path
    ],
)
def test_is_archive(name, expected):
    assert is_archive(Path(name)) is expected
