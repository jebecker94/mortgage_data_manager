"""Shared utilities for HMDA data processing.

Helpers for I/O, cleaning, identity keys, and schema handling.
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from csv import Sniffer
from functools import cache
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.hmda.config import HMDA_INDEX_COLUMN

logger = get_logger(__name__)

#: Pinned bronze/silver row counts per (dataset, activity year, file_type).
EXPECTED_ROW_COUNTS_FILE = Path(__file__).parent / "expected_row_counts.json"


@cache
def load_expected_row_counts() -> dict[str, dict[str, dict[str, int]]]:
    """Load the pinned row-count manifest, dropping its ``_comment`` keys.

    Returns:
        The manifest, nested as ``{dataset: {year: {file_type: count}}}``.
    """
    with open(EXPECTED_ROW_COUNTS_FILE) as f:
        manifest = json.load(f)
    return {k: v for k, v in manifest.items() if not k.startswith("_")}


def check_expected_row_count(
    dataset: str, year: int | str, file_type: str, actual: int, source: Path | str
) -> None:
    """Verify a written bronze file or silver partition against the pinned manifest.

    Silent no-op when no count is pinned for the unit (new vintages get pinned by
    the guard-test ritual, not here). Raises on mismatch so a truncated write can
    never be reported as success.

    Args:
        dataset: Dataset family the unit belongs to.
        year: Activity year of the unit.
        file_type: Release code of the unit.
        actual: Row count actually written.
        source: Path of the written file or partition, for the error message.

    Raises:
        ValueError: If the written row count differs from the pinned expectation.
    """
    expected = load_expected_row_counts().get(dataset, {}).get(str(year), {}).get(file_type)
    if expected is None:
        logger.debug("No pinned row count for hmda/%s %s/%s", dataset, year, file_type)
        return
    if actual != expected:
        raise ValueError(
            f"{source} holds {actual:,} rows for hmda/{dataset} {year}/{file_type} but "
            f"{EXPECTED_ROW_COUNTS_FILE.name} pins {expected:,}. A short count means a "
            f"truncated write — delete the output and re-run. If the CFPB republished "
            f"the file, verify the new count against the raw archive and update the "
            f"manifest deliberately."
        )
    logger.info(
        "Row count verified: hmda/%s %s/%s = %s rows", dataset, year, file_type, f"{actual:,}"
    )


def append_hmda_index(lf: pl.LazyFrame, year: int, file_type_code: str) -> pl.LazyFrame:
    """Format a row-index column into the canonical HMDAIndex string.

    The HMDAIndex is a stable per-record locator shared across every HMDA era:
    activity year (4 digits) + file-type code (1 char) + zero-padded 9-digit row
    number, e.g. ``2018a_000000001``. The caller must already have added an
    integer row index named `HMDA_INDEX_COLUMN` (e.g. via
    ``scan_csv(row_index_name=...)`` at bronze, or ``with_row_index`` at silver).

    Args:
        lf: Lazy frame carrying an integer row index in ``HMDA_INDEX_COLUMN``.
        year: Activity year, used as the 4-digit prefix.
        file_type_code: Single-character file-type code (a, b, c, d, e).

    Returns:
        Lazy frame with ``HMDA_INDEX_COLUMN`` rewritten as the formatted string.
    """
    prefix = f"{year}{file_type_code}_"
    return (
        lf.cast({HMDA_INDEX_COLUMN: pl.String}, strict=False)
        .with_columns(pl.col(HMDA_INDEX_COLUMN).str.zfill(9).alias(HMDA_INDEX_COLUMN))
        .with_columns((pl.lit(prefix) + pl.col(HMDA_INDEX_COLUMN)).alias(HMDA_INDEX_COLUMN))
    )


def hmda_index_year(col: str = HMDA_INDEX_COLUMN) -> pl.Expr:
    """Activity year parsed from the HMDAIndex prefix (``2020a_...`` -> 2020).

    Args:
        col: Column holding the HMDAIndex.

    Returns:
        An Int32 expression yielding the activity year.
    """
    return pl.col(col).str.slice(0, 4).cast(pl.Int32)


def hmda_index_file_type(col: str = HMDA_INDEX_COLUMN) -> pl.Expr:
    """Release code parsed from the HMDAIndex prefix (``2020a_...`` -> ``"a"``).

    Args:
        col: Column holding the HMDAIndex.

    Returns:
        A Utf8 expression yielding the single-character release code.
    """
    return pl.col(col).str.slice(4, 1)


# =============================================================================
# I/O Utilities
# =============================================================================


def normalized_file_stem(stem: str) -> str:
    """Remove common suffixes from extracted archive names.

    Args:
        stem: File stem (name without extension)

    Returns:
        Normalized file stem with common suffixes removed
    """
    if stem.endswith("_csv"):
        stem = stem[:-4]
    if stem.endswith("_pipe"):
        stem = stem[:-5]
    return stem


def get_delimiter(file_path: Path | str, bytes: int = 4096) -> str:
    """Determine the delimiter used in a delimited text file.

    Args:
        file_path: Path of the delimited file.
        bytes: How many bytes to sniff from the head of the file.

    Returns:
        The detected delimiter.
    """
    sniffer = Sniffer()
    data = open(file_path, encoding="latin-1").read(bytes)
    return sniffer.sniff(data).delimiter


def replace_csv_column_names(
    csv_file: Path | str, column_name_mapper: dict[str, str] | None = None
) -> None:
    """Replace column headers in a CSV file based on a mapping.

    Args:
        csv_file: Path of the CSV file to rewrite in place.
        column_name_mapper: Old-to-new header names; empty leaves the file as is.
    """
    if column_name_mapper is None:
        column_name_mapper = {}

    delimiter = get_delimiter(csv_file, bytes=16000)
    with open(csv_file) as f:
        first_line = f.readline().strip()

    first_line_items = first_line.split(delimiter)
    new_first_line_items = []
    for first_line_item in first_line_items:
        for key, item in column_name_mapper.items():
            if first_line_item == key:
                first_line_item = item
        new_first_line_items.append(first_line_item)
    new_first_line = delimiter.join(new_first_line_items)

    with open(csv_file) as f:
        lines = f.readlines()
    lines[0] = new_first_line + "\n"
    with open(csv_file, "w") as f:
        f.writelines(lines)


def unzip_hmda_file(zip_file: Path | str, raw_folder: Path | str, overwrite: bool = False) -> Path:
    """Extract a compressed HMDA archive and return extracted file path.

    Args:
        zip_file: Path of the archive; a missing ``.zip`` suffix is added.
        raw_folder: Directory to extract into.
        overwrite: Re-extract even when the target file already exists.

    Returns:
        Path of the extracted delimited file.

    Raises:
        ValueError: If no comparably-named zip file can be found.
    """
    zip_file = Path(zip_file)
    raw_folder = Path(raw_folder)
    if zip_file.suffix.lower() != ".zip":
        zip_file = zip_file.with_suffix(".zip")
        if not zip_file.exists():
            raise ValueError(
                "The file name was not given as a zip file. Failed to find a comparably-named zip file."
            )

    with zipfile.ZipFile(zip_file) as z:
        delimited_files = [
            x for x in z.namelist() if (x.endswith(".txt") or x.endswith(".csv")) and "/" not in x
        ]
        for file in delimited_files:
            raw_file_name = raw_folder / file
            expected_size = z.getinfo(file).file_size

            # A leftover file from an interrupted extraction must not be reused —
            # a partial CSV parses cleanly and produces a silently truncated
            # bronze. Panel files are exempt from the size comparison because
            # their header is rewritten in place below.
            needs_extract = overwrite or not raw_file_name.exists()
            if (
                not needs_extract
                and "panel" not in file
                and raw_file_name.stat().st_size != expected_size
            ):
                logger.warning(
                    "Existing extracted file %s is %d bytes but the archive member "
                    "holds %d — treating it as a partial extract and re-extracting.",
                    raw_file_name,
                    raw_file_name.stat().st_size,
                    expected_size,
                )
                needs_extract = True

            if needs_extract:
                logger.info("Extracting file: %s", file)
                try:
                    z.extract(file, path=raw_folder)
                except Exception as exc:
                    import shutil

                    sevenz = shutil.which("7z") or shutil.which("7zz")
                    if sevenz is None:
                        raise OSError(
                            f"Failed to extract {file} from {zip_file} "
                            f"({type(exc).__name__}: {exc}). "
                            "Install 7z (`brew install p7zip`) for fallback extraction."
                        ) from exc
                    logger.warning(
                        "zipfile extraction failed for %s (%s). Falling back to 7z.",
                        file,
                        exc,
                    )
                    p = subprocess.Popen(
                        [sevenz, "e", str(zip_file), f"-o{raw_folder}", file, "-y"]
                    )
                    p.wait()

                actual_size = raw_file_name.stat().st_size if raw_file_name.exists() else -1
                if actual_size != expected_size:
                    raw_file_name.unlink(missing_ok=True)
                    raise OSError(
                        f"Extraction of {file} from {zip_file} is incomplete: "
                        f"{actual_size} bytes on disk vs {expected_size} in the archive. "
                        "Partial file removed; re-run after checking disk space."
                    )

            if "panel" in file:
                column_name_mapper = {
                    "topholder_rssd": "top_holder_rssd",
                    "topholder_name": "top_holder_name",
                    "upper": "lei",
                }
                replace_csv_column_names(raw_file_name, column_name_mapper=column_name_mapper)

    return raw_file_name


__all__ = [
    # I/O
    "normalized_file_stem",
    "get_delimiter",
    "replace_csv_column_names",
    "unzip_hmda_file",
    # Row-count guards
    "load_expected_row_counts",
    "check_expected_row_count",
    # HMDAIndex
    "append_hmda_index",
    "hmda_index_year",
    "hmda_index_file_type",
]
