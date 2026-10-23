"""The Philadelphia Fed's HMDA Lender File (the "Avery file"), kept as reference data.

The Lender File links each HMDA filer to its NIC, Call Report, FHLB-membership, CRA and Ginnie
Mae identifiers. It is published as two workbooks, one per era, each replaced in place at every
release. Several matching workflows read it (FHLB membership for HMDA<->FHLB, lender names for
the direct HMDA<->issuance matcher), so it lives under ``MortgageDataConfig.REFERENCE_DIR`` rather than in
any one workflow's folder. Readers open the workbook themselves: the data sheet's name is a
release label that changes between vintages, so they read it by position.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import DownloadStatus, DownloadTask, batch_download
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

type AveryEra = Literal["post2018", "pre2018"]

AVERY_BASE_URL = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/hmda"

#: Published workbook name per era (2018-present and 1990-2017).
AVERY_FILES: dict[AveryEra, str] = {
    "post2018": "hmda-2018-present.xlsx",
    "pre2018": "hmda-1990-2017.xlsx",
}


def avery_dir() -> Path:
    """The Lender File's folder under the reference root."""
    return MortgageDataConfig.REFERENCE_DIR / "avery_lender_file"


def avery_file_path(era: AveryEra = "post2018") -> Path:
    """Local path of one era's Lender File workbook.

    Args:
        era: ``"post2018"`` (filing years 2018-present) or ``"pre2018"`` (1990-2017).

    Returns:
        The workbook's path under `avery_dir`; it exists only once downloaded.
    """
    return avery_dir() / AVERY_FILES[era]


def download_avery_files(
    output_dir: Path | None = None,
    overwrite: bool = False,
    pause: float | None = None,
) -> int:
    """Download both Lender File workbooks from the Philadelphia Fed.

    Each release replaces the workbooks in place under the same names, so a new release is
    picked up with ``overwrite=True``. Every fetch is ledgered under ``source="reference"``
    and must sniff as a zip container (xlsx), which rejects an HTML error page.

    Args:
        output_dir: Folder to write into; defaults to `avery_dir`.
        overwrite: If True, re-download workbooks already on disk.
        pause: Seconds between requests; ``None`` uses the configured default.

    Returns:
        The number of workbooks downloaded (skipped files are not counted).

    Raises:
        RuntimeError: If any download fails.
    """
    out = output_dir or avery_dir()
    out.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks = [
        DownloadTask(url=f"{AVERY_BASE_URL}/{name}", destination=out / name, headers=headers)
        for name in AVERY_FILES.values()
    ]
    results = batch_download(
        tasks,
        mode="overwrite" if overwrite else "skip",
        pause_seconds=pause,
        show_progress=False,
        source="reference",
        dataset="avery_lender_file",
        expect={"zip"},
        raw_dir=MortgageDataConfig.REFERENCE_DIR,
    )
    failed = [r for r in results if r.status == DownloadStatus.FAILED]
    if failed:
        raise RuntimeError(
            f"Avery Lender File download failed: {[r.error_message for r in failed]}"
        )
    downloaded = sum(r.status == DownloadStatus.SUCCESS for r in results)
    logger.info(
        "Avery Lender File: %s downloaded, %s already present",
        downloaded,
        len(results) - downloaded,
    )
    return downloaded
