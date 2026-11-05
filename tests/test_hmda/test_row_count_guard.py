"""Data guard: post-2018 HMDA bronze files and silver partitions keep their pinned row counts.

An interrupted extraction leaves a partial CSV that still parses cleanly, so a
truncated bronze is written and reported as success — and an existence-based skip
then makes it permanent, indistinguishable from a complete build. These tests
re-verify every pinned (dataset, activity year, file_type) in
``schemas/hmda/expected_row_counts.json`` against the CURRENT parquet footers, in both
layers — bronze and silver carry identical counts for these datasets.

The manifest was pinned 2026-08-31 from bronze footers, cross-checked row-exact
against an independent read of the raw archives. A post-2018 bronze file that is
not pinned FAILS the guard — the ritual for a new vintage is: ingest, verify the
count against the raw archive, add the entry.

Everything reads parquet FOOTER metadata only (fast, no data). All tests skip when the
data volume is not mounted.
"""

from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from mortgage_data_manager.sources.hmda.config import HMDAConfig
from mortgage_data_manager.sources.hmda.import_bronze import _get_file_type_code
from mortgage_data_manager.sources.hmda.import_silver import _YEAR_TOKEN
from mortgage_data_manager.sources.hmda.utils import load_expected_row_counts

_LOANS_BRONZE = HMDAConfig.get_dataset_dir("bronze", "loans")


def _bronze_units(dataset: str) -> dict[str, dict[str, tuple[int, str]]]:
    """Footer row counts for post-2018 bronze files, keyed year -> file_type."""
    seen: dict[str, dict[str, tuple[int, str]]] = {}
    for f in sorted(HMDAConfig.get_dataset_dir("bronze", dataset).glob("*.parquet")):
        years = _YEAR_TOKEN.findall(f.stem)
        if not years or int(years[0]) < 2018:
            continue
        try:
            file_type = _get_file_type_code(f)
        except ValueError:
            continue
        seen.setdefault(years[0], {})[file_type] = (pq.ParquetFile(f).metadata.num_rows, f.name)
    return seen


@pytest.mark.skipif(not _LOANS_BRONZE.exists(), reason="HMDA bronze not mounted")
def test_bronze_row_counts_match_manifest():
    for dataset, years in load_expected_row_counts().items():
        seen = _bronze_units(dataset)
        for year, file_types in years.items():
            for file_type, expected in file_types.items():
                assert year in seen and file_type in seen[year], (
                    f"pinned hmda/{dataset} {year}/{file_type} has no bronze file — "
                    f"rebuild it or (if the CFPB withdrew it) remove the manifest entry"
                )
                actual, name = seen[year][file_type]
                assert actual == expected, (
                    f"bronze {name} holds {actual:,} rows; expected_row_counts.json pins "
                    f"{expected:,} — a short count means a truncated write"
                )
        for year, file_types in seen.items():
            for file_type, (actual, name) in file_types.items():
                assert year in years and file_type in years[year], (
                    f"bronze {name} ({actual:,} rows) is not pinned — verify the count "
                    f"against the raw archive and add it to expected_row_counts.json"
                )


@pytest.mark.skipif(not _LOANS_BRONZE.exists(), reason="HMDA bronze not mounted")
def test_silver_partition_row_counts_match_manifest():
    for dataset, years in load_expected_row_counts().items():
        silver_dir = HMDAConfig.get_dataset_dir("silver", dataset, "post2018")
        for year, file_types in years.items():
            for file_type, expected in file_types.items():
                part_dir = silver_dir / f"activity_year={year}" / f"file_type={file_type}"
                files = sorted(part_dir.glob("*.parquet"))
                assert files, f"missing silver partition {part_dir}"
                actual = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
                assert actual == expected, (
                    f"silver {part_dir} holds {actual:,} rows; expected_row_counts.json "
                    f"pins {expected:,} — rebuild the partition from bronze"
                )
