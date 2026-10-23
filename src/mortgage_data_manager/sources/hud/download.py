"""Download HUD multifamily Excel extracts and Data Element Dictionaries.

The HUD multifamily artifacts are monthly point-in-time snapshots published at
fixed URLs (see `SOURCE_FILES`). Each refresh replaces the whole file, so
downloads always fetch the complete extract rather than appending.

Two source quirks are handled here:

- **Section 202 drift** — the 202-direct-loans URL is date-stamped in its
  filename and HUD rolls it forward monthly. `resolve_section202_url`
  HEAD-checks the pinned URL and, on failure, re-scrapes the index page for the
  current ``202directloans*.xlsx`` link.
- **Snapshot provenance** — these files carry no in-band vintage, so the
  source ``Last-Modified`` header captured by the raw download ledger
  (``core.download_ledger``) is the vintage; `snapshot_dates` reads it
  and the bronze layer stamps every row with that ``snapshot_date`` to support
  building a panel from archived vintages.

The self-extracting Access ``.zip`` HUD also offers is intentionally not
downloaded: it bundles the same two Section 8 Excel extracts we already fetch
plus a 65 MB ``.accdb`` — pure redundancy.
"""

from __future__ import annotations

from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import (
    DownloadResult,
    DownloadTask,
    batch_download,
    retry_request,
    summarize_results,
)
from mortgage_data_manager.core.download_ledger import latest
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.hud.config import (
    DEDS,
    SECTION202_INDEX_URL,
    SOURCE_FILES,
    HUDConfig,
    source_files_for,
)

logger = get_logger(__name__)

_SECTION202_FILE: str = "202_direct_loans.xlsx"


def resolve_section202_url(*, timeout: int | None = None) -> str:
    """Return a working URL for the Section 202 direct-loans workbook.

    The pinned URL in `SOURCE_FILES` carries a date stamp that HUD rolls
    forward monthly. This HEAD-checks the pinned URL and, if it no longer
    resolves, scrapes the index page for the current ``202directloans*.xlsx``
    link.

    Args:
        timeout: Per-request timeout (None = config default).

    Returns:
        A URL that currently responds, or the pinned URL as a last resort.
    """
    pinned = SOURCE_FILES[_SECTION202_FILE]
    try:
        retry_request(pinned, method="HEAD", timeout=timeout, retries=0)
        return pinned
    except Exception as exc:  # noqa: BLE001 - fall back to scraping the index
        logger.warning("Section 202 pinned URL failed HEAD (%s); scraping index page", exc)

    try:
        response = retry_request(SECTION202_INDEX_URL, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not load Section 202 index page: %s; using pinned URL", exc)
        return pinned

    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = str(tag["href"])
        name = Path(urlparse(href).path).name.lower()
        if name.startswith("202directloans") and name.endswith(".xlsx"):
            found = urljoin(SECTION202_INDEX_URL, href)
            logger.info("Section 202: resolved current link → %s", found)
            return found

    logger.warning("Section 202: no dated link found on index page; using pinned URL")
    return pinned


def download_files(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download the source workbooks needed for ``datasets`` to the raw layer.

    Args:
        datasets: Subset of `VALID_DATASETS`; ``None`` = all. The unique
            set of source workbooks backing those tables is fetched.
        overwrite: If True, redownload existing files.
        pause: Seconds between requests; forwarded to
            ``batch_download(pause_seconds=...)`` (``None`` = config default).
        timeout: Per-request timeout (None = config default).
        show_progress: Display per-file progress bars.

    Returns:
        List of DownloadResult objects in submission order.
    """
    HUDConfig.ensure_directories()
    sources = source_files_for(datasets)

    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks: list[DownloadTask] = []
    for name, url in sources.items():
        if name == _SECTION202_FILE:
            url = resolve_section202_url(timeout=timeout)
        tasks.append(DownloadTask(url=url, destination=HUDConfig.raw_path(name), headers=headers))

    logger.info("HUD-MF: queued %s source workbook downloads", len(tasks))
    results = batch_download(
        tasks,
        mode="overwrite" if overwrite else "skip",
        pause_seconds=pause,
        timeout=timeout,
        show_progress=show_progress,
        source="hud",
        expect={"zip", "xls"},
        raw_dir=HUDConfig.HUD_RAW_DIR,
    )

    summary = summarize_results(results)
    logger.info(
        "HUD-MF download complete: success=%s skipped=%s failed=%s",
        summary.get("success", 0),
        summary.get("skipped_exists", 0),
        summary.get("failed", 0),
    )
    return results


def download_dictionaries(
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download the Data Element Dictionary PDFs to ``raw/dictionaries/``.

    Args:
        overwrite: If True, redownload existing PDFs.
        pause: Seconds between requests (None = config default).
        timeout: Per-request timeout (None = config default).
        show_progress: Display per-file progress bars.

    Returns:
        List of DownloadResult objects, one per dictionary.
    """
    HUDConfig.ensure_directories()
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks = [
        DownloadTask(
            url=url,
            destination=HUDConfig.raw_dictionary_path(name),
            headers=headers,
        )
        for name, url in DEDS.items()
    ]
    logger.info("HUD-MF: queued %s dictionary PDF downloads", len(tasks))
    return batch_download(
        tasks,
        mode="overwrite" if overwrite else "skip",
        pause_seconds=pause,
        timeout=timeout,
        show_progress=show_progress,
        source="hud",
        dataset="dictionaries",
        expect={"pdf"},
        raw_dir=HUDConfig.HUD_RAW_DIR,
    )


def _parse_snapshot_date(value: str | None) -> date | None:
    """Parse a ``Last-Modified`` header (RFC 2822) or an ISO date into a date."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def snapshot_dates() -> dict[str, date]:
    """Snapshot date of every source workbook on disk, keyed by friendly filename.

    The date is the source's ``Last-Modified`` header as captured in the raw
    download ledger when the file was last fetched (or registered).

    Returns:
        Snapshot dates keyed by friendly filename, for every source with a
        parseable header on record.
    """
    rows = latest("hud").select("destination", "last_modified")
    out: dict[str, date] = {}
    for destination, header in rows.iter_rows():
        parsed = _parse_snapshot_date(header)
        if parsed is not None and "/" not in destination:
            out[destination] = parsed
    return out


def missing_sources(datasets: list[str] | None = None) -> list[str]:
    """Return friendly source filenames required for ``datasets`` but not on disk.

    Args:
        datasets: Logical tables to check, or None for all.

    Returns:
        The required source filenames that are absent from raw.
    """
    sources = source_files_for(datasets)
    return [name for name in sources if not HUDConfig.raw_path(name).exists()]


__all__ = [
    "download_dictionaries",
    "download_files",
    "snapshot_dates",
    "missing_sources",
    "resolve_section202_url",
]
