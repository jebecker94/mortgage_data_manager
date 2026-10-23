"""Data guard: every raw file has a ledger record, and the ledgers are well-formed.

The raw download ledger (``core.download_ledger``) is only useful if it is
complete. These tests walk every known raw directory on the data volume and
fail when a file has no ``success``/``registered`` record whose size matches the
bytes on disk — the signal that something wrote raw bytes around the ledger
(fix the writer, then ``mortgage-data raw register`` the file). They also check
the ledgers' own invariants: no absolute destinations, no success without a
hash, records filed under the right source.

All tests skip when the data volume is not mounted.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.cli.raw import raw_dirs
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download_ledger import (
    ledger_dir,
    ledger_path,
    read_ledger,
    unrecorded_files,
)

_VOLUME_MOUNTED = MortgageDataConfig.DATA_DIR.exists()
pytestmark = pytest.mark.skipif(not _VOLUME_MOUNTED, reason="data volume not mounted")

_SOURCES = sorted(raw_dirs()) if _VOLUME_MOUNTED else []


@pytest.mark.parametrize("source", _SOURCES)
def test_every_raw_file_is_recorded(source: str) -> None:
    raw_dir = raw_dirs([source])[source]
    if not raw_dir.exists():
        pytest.skip(f"{source} raw dir not present")
    missing = unrecorded_files(source, raw_dir)
    assert not missing, (
        f"{source}: {len(missing)} raw file(s) without a matching ledger record, e.g. "
        f"{missing[:5]} — run `mortgage-data raw register --source {source}`"
    )


def test_ledger_invariants() -> None:
    if not ledger_dir().exists():
        pytest.skip("no ledger yet")
    df = read_ledger(None).collect()
    assert not df.is_empty()

    absolute = df.filter(pl.col("destination").str.starts_with("/"))
    assert absolute.is_empty(), absolute.select("source", "destination").head(5)

    unhashed = df.filter((pl.col("status") == "success") & pl.col("sha256").is_null())
    assert unhashed.is_empty(), unhashed.select("source", "destination", "url").head(5)

    for source in df["source"].unique():
        assert ledger_path(source).exists(), f"{source} records but no {source}.jsonl"
        in_file = read_ledger(source).collect()
        assert (in_file["source"] == source).all(), f"{source}.jsonl holds foreign records"
