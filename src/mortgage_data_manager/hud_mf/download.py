"""Download HUD multifamily Excel extracts and Data Element Dictionaries.

The HUD multifamily artifacts are monthly point-in-time snapshots published at
fixed URLs (see :data:`SOURCE_FILES`). Each refresh replaces the whole file, so
downloads always fetch the complete extract rather than appending.

Two source quirks are handled here:

- **Section 202 drift** — the 202-direct-loans URL is date-stamped in its
  filename and HUD rolls it forward monthly. :func:`resolve_section202_url`
  HEAD-checks the pinned URL and, on failure, re-scrapes the index page for the
  current ``202directloans*.xlsx`` link.
- **Snapshot provenance** — these files carry no in-band vintage, so each
  download records the source ``Last-Modified`` header into
  ``raw/download_manifest.json``; the bronze layer stamps every row with that
  ``snapshot_date`` to support building a panel from archived vintages.

The self-extracting Access ``.zip`` HUD also offers is intentionally not
downloaded: it bundles the same two Section 8 Excel extracts we already fetch
plus a 65 MB ``.accdb`` — pure redundancy.
"""

from __future__ import annotations

import json
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
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud_mf.config import (
    DEDS,
    SECTION202_INDEX_URL,
    SOURCE_FILES,
    HudMfConfig,
    source_files_for,
)

logger = get_logger(__name__)

_SECTION202_FILE: str = "202_direct_loans.xlsx"


def resolve_section202_url(*, timeout: int | None = None) -> str:
    """Return a working URL for the Section 202 direct-loans workbook.

    The pinned URL in :data:`SOURCE_FILES` carries a date stamp that HUD rolls
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
        logger.warning(f"Section 202 pinned URL failed HEAD ({exc}); scraping index page")

    try:
        response = retry_request(SECTION202_INDEX_URL, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Could not load Section 202 index page: {exc}; using pinned URL")
        return pinned

    soup = BeautifulSoup(response.content, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = str(tag["href"])
        name = Path(urlparse(href).path).name.lower()
        if name.startswith("202directloans") and name.endswith(".xlsx"):
            found = urljoin(SECTION202_INDEX_URL, href)
            logger.info(f"Section 202: resolved current link → {found}")
            return found

    logger.warning("Section 202: no dated link found on index page; using pinned URL")
    return pinned


def _last_modified(url: str, *, timeout: int | None = None) -> str | None:
    """Return the ``Last-Modified`` header of ``url`` as an ISO date, or None."""
    try:
        response = retry_request(url, method="HEAD", timeout=timeout, retries=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"HEAD {url} failed: {exc}")
        return None
    header = response.headers.get("Last-Modified")
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).date().isoformat()
    except (TypeError, ValueError):
        return None


def _write_manifest(entries: dict[str, dict[str, object]]) -> Path:
    """Merge ``entries`` into the on-disk download manifest and return its path."""
    path = HudMfConfig.manifest_path()
    existing: dict[str, dict[str, object]] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing.update(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    return path


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
        datasets: Subset of :data:`VALID_DATASETS`; ``None`` = all. The unique
            set of source workbooks backing those tables is fetched.
        overwrite: If True, redownload existing files.
        pause: Seconds between requests; forwarded to
            ``batch_download(pause_seconds=...)`` (``None`` = config default).
        timeout: Per-request timeout (None = config default).
        show_progress: Display per-file progress bars.

    Returns:
        List of DownloadResult objects in submission order.
    """
    HudMfConfig.ensure_directories()
    sources = source_files_for(datasets)

    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks: list[DownloadTask] = []
    for name, url in sources.items():
        if name == _SECTION202_FILE:
            url = resolve_section202_url(timeout=timeout)
        tasks.append(DownloadTask(url=url, destination=HudMfConfig.raw_path(name), headers=headers))

    logger.info(f"HUD-MF: queued {len(tasks)} source workbook downloads")
    results = batch_download(
        tasks,
        mode="overwrite" if overwrite else "skip",
        pause_seconds=pause,
        timeout=timeout,
        show_progress=show_progress,
    )

    # Record provenance (URL / Last-Modified / bytes) for every present file.
    manifest: dict[str, dict[str, object]] = {}
    for name, url in sources.items():
        path = HudMfConfig.raw_path(name)
        if not path.exists():
            continue
        manifest[name] = {
            "url": resolve_section202_url(timeout=timeout) if name == _SECTION202_FILE else url,
            "last_modified": _last_modified(url, timeout=timeout),
            "bytes": path.stat().st_size,
        }
    _write_manifest(manifest)

    summary = summarize_results(results)
    logger.info(
        f"HUD-MF download complete: success={summary.get('success', 0)} "
        f"skipped={summary.get('skipped_exists', 0)} failed={summary.get('failed', 0)}"
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
    HudMfConfig.ensure_directories()
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks = [
        DownloadTask(
            url=url,
            destination=HudMfConfig.raw_dictionary_path(name),
            headers=headers,
        )
        for name, url in DEDS.items()
    ]
    logger.info(f"HUD-MF: queued {len(tasks)} dictionary PDF downloads")
    return batch_download(
        tasks,
        mode="overwrite" if overwrite else "skip",
        pause_seconds=pause,
        timeout=timeout,
        show_progress=show_progress,
    )


def load_manifest() -> dict[str, dict[str, object]]:
    """Load the raw download manifest (empty dict if absent/unreadable)."""
    path = HudMfConfig.manifest_path()
    if not path.exists():
        return {}
    try:
        data: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data


def missing_sources(datasets: list[str] | None = None) -> list[str]:
    """Return friendly source filenames required for ``datasets`` but not on disk."""
    sources = source_files_for(datasets)
    return [name for name in sources if not HudMfConfig.raw_path(name).exists()]


__all__ = [
    "download_dictionaries",
    "download_files",
    "load_manifest",
    "missing_sources",
    "resolve_section202_url",
]
