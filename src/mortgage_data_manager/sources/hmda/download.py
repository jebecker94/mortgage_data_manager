"""HMDA data download from the CFPB file servers.

CFPB serves every published file from two static hosts at fully predictable
paths, so this module is a URL table rather than a scraper: `_COVERAGE`
records which ``(product, record type)`` series exist and over what years, and
`build_url` turns one row of that table into a URL.

The data-publication pages themselves are a JavaScript application that renders
no links into its HTML, so the previous implementation drove a headless Chrome
through Selenium purely to read hrefs back out. The table it would have
recovered is the same one hardcoded here, and the file hosts serve range
requests, ``Content-Length`` and ``Last-Modified`` — everything
``core.download`` needs.

Coverage moves in two ways, and the table distinguishes them. A series that is
still being published carries ``None`` as its last year: candidates are
generated through the current year and the leading edge is HEAD-probed, so a
newly released year is picked up with no code change. A series CFPB has stopped
publishing carries a real last year, because probing past it would 404 forever.

Main functions:
    - discover_files: expand a selection into candidate files (no network)
    - plan_downloads: HEAD-probe candidates and return the fetchable ones
    - download_hmda_files: discover, plan, and fetch
    - determine_raw_subfolder: route a filename to its ``raw/`` subfolder
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

import requests

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import (
    DownloadResult,
    DownloadTask,
    batch_download,
    summarize_results,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.hmda.config import (
    RAW_DIR,
    HMDAProduct,
    HMDARecordType,
    resolve_download_datasets,
    resolve_products,
)

logger = get_logger(__name__)

#: Host for everything published under the current HMDA platform.
FILE_SERVER: Final[str] = "https://files.ffiec.cfpb.gov"

#: Host for the pre-2018 archive, still on the legacy consumerfinance.gov CDN.
HISTORIC_SERVER: Final[str] = "https://files.consumerfinance.gov"

#: Delimiter of the downloaded file. CFPB ships each file both ways; ``csv`` is
#: the default because the bronze builders read it.
type HMDAFileFormat = Literal["csv", "pipe"]

#: Record type -> the code CFPB uses in ``static-data`` filenames.
_FILENAME_CODE: Final[dict[HMDARecordType, str]] = {
    "loans": "lar",
    "panel": "panel",
    "transmittal_series": "ts",
    "msamd": "msamd",
}

#: Product -> its path segment and filename infix under ``static-data``.
_STATIC_DATA_PARTS: Final[dict[HMDAProduct, tuple[str, str]]] = {
    "snapshot": ("snapshot", ""),
    "one_year": ("one-year", "_one_year"),
    "three_year": ("three-year", "_three_year"),
}

#: Record type -> its path segment and filename suffix in the pre-2018 archive.
_HISTORIC_PARTS: Final[dict[HMDARecordType, tuple[str, str]]] = {
    "loans": ("hmda-historic-loan-data", "nationwide_all-records_codes"),
    "panel": ("hmda-historic-institution-data", "panel"),
    "transmittal_series": ("hmda-historic-institution-data", "transmittal_sheet"),
}

#: ``(product, record type)`` -> ``(first year, last year)``.
#:
#: A ``None`` last year means the series is open: candidates run through the
#: current year and the leading edge is probed, so a new release needs no edit
#: here. A real last year means CFPB stopped publishing that series and probing
#: past it would 404 every run.
#:
#: The three closed ``panel`` bounds are a publication decision, not a lag —
#: CFPB replaced the reporter panel with the transmittal sheet for later
#: releases and says so on the data-publication pages. ``historic`` is closed
#: because 2017 was the last pre-platform filing year. Every other series is
#: open, including ``three_year``, whose 2023 restatement is merely not due yet.
_COVERAGE: Final[dict[tuple[HMDAProduct, HMDARecordType], tuple[int, int | None]]] = {
    ("snapshot", "loans"): (2017, None),
    ("snapshot", "panel"): (2017, 2023),
    ("snapshot", "transmittal_series"): (2017, None),
    ("snapshot", "msamd"): (2017, None),
    ("one_year", "loans"): (2019, None),
    ("one_year", "panel"): (2019, 2022),
    ("one_year", "transmittal_series"): (2019, None),
    ("one_year", "msamd"): (2019, None),
    ("three_year", "loans"): (2017, None),
    ("three_year", "panel"): (2017, 2020),
    ("three_year", "transmittal_series"): (2017, None),
    ("three_year", "msamd"): (2017, None),
    ("historic", "loans"): (2007, 2017),
    ("historic", "panel"): (2007, 2017),
    ("historic", "transmittal_series"): (2007, 2017),
    ("mlar", "loans"): (2022, None),
}

#: The 2017 ARID-to-LEI crosswalk, published as a snapshot extra rather than as
#: one of the four record types. It bridges the pre-2018 respondent ID to the
#: LEI, so it is fetched alongside ``panel`` and lands in ``misc/``.
_ARID_LEI_XREF: Final[str] = "arid2017_to_lei_xref"


@dataclass(frozen=True)
class HMDAFile:
    """One downloadable file, resolved from the coverage table.

    Attributes:
        product: Publication product the file belongs to.
        record_type: Record type the file holds.
        year: Filing year the file covers.
        url: Absolute URL on the CFPB file server.
        filename: CFPB's own filename, kept verbatim.
        subfolder: ``raw/`` subfolder the file belongs in.
    """

    product: HMDAProduct
    record_type: HMDARecordType
    year: int
    url: str
    filename: str
    subfolder: str

    def destination(self, root: Path) -> Path:
        """Return this file's path under a raw-layer ``root``.

        Args:
            root: Root of the raw layer.

        Returns:
            The file's destination path under ``root``.
        """
        return root / self.subfolder / self.filename


def determine_raw_subfolder(file_name: str) -> str:
    """Route a downloaded file into a subfolder under ``raw/`` by filename.

    `discover_files` knows each file's record type outright and does not
    need this; it remains the fallback for callers holding only a name.

    Args:
        file_name: Name of the HMDA file (with or without extension).

    Returns:
        Subfolder name: 'loans', 'msamd', 'panel', 'transmittal_series', or 'misc'.

    Example:
        >>> determine_raw_subfolder("2023_public_lar_csv.zip")
        'loans'
        >>> determine_raw_subfolder("2023_public_panel_csv.zip")
        'panel'
        >>> determine_raw_subfolder("hmda_2016_transmittal_sheet.zip")
        'transmittal_series'
    """
    # Match on the stem: the token patterns below anchor on a separator or the
    # end of the string, and an extension would hide the ones that fall at the
    # end ("hmda_2016_panel.zip" ends in "panel." not "panel").
    name = Path(file_name).stem.lower()

    # MSAMD geography files
    if "msamd" in name:
        return "msamd"

    # Transmittal Series (TS) files
    if (
        ("public_ts" in name)
        or ("transmiss" in name)
        or ("transmittal" in name)
        or re.search(r"(^|[_-])ts([_-]|$)", name)
    ):
        return "transmittal_series"

    # Panel files
    if ("public_panel" in name) or re.search(r"(^|[_-])panel([_-]|$)", name):
        return "panel"

    # Loan files (LAR/MLAR/nationwide loan-level)
    if (
        ("mlar" in name)
        or ("public_lar" in name)
        or re.search(r"(^|[_-])lar([_-]|$)", name)
        or ("nationwide" in name)
    ):
        return "loans"

    # Other miscellaneous assets
    return "misc"


def _format_suffix(product: HMDAProduct, year: int, file_format: HMDAFileFormat) -> str:
    """Return the delimiter token in a ``static-data`` filename.

    The pipe-delimited 2017 snapshot is named ``_txt``; every later year and
    every other product names the same thing ``_pipe``.

    Returns:
        The delimiter token, one of ``csv``, ``txt`` or ``pipe``.
    """
    if file_format == "csv":
        return "csv"
    return "txt" if (product == "snapshot" and year == 2017) else "pipe"


def build_url(
    product: HMDAProduct,
    record_type: HMDARecordType,
    year: int,
    file_format: HMDAFileFormat = "csv",
) -> str:
    """Build the download URL for one ``(product, record type, year)``.

    Args:
        product: Publication product.
        record_type: Record type.
        year: Filing year.
        file_format: Delimiter variant to fetch.

    Returns:
        Absolute URL on the appropriate CFPB file server.

    Raises:
        ValueError: If the combination has no published URL shape.

    Example:
        >>> build_url("snapshot", "loans", 2023)
        'https://files.ffiec.cfpb.gov/static-data/snapshot/2023/2023_public_lar_csv.zip'
    """
    if product == "historic":
        if record_type not in _HISTORIC_PARTS:
            raise ValueError(f"No historic archive for record type {record_type!r}")
        segment, suffix = _HISTORIC_PARTS[record_type]
        return f"{HISTORIC_SERVER}/{segment}/hmda_{year}_{suffix}.zip"

    if product == "mlar":
        return f"{FILE_SERVER}/modified-lar/combined-mlar/{year}/{year}_combined_mlar_header.zip"

    segment, infix = _STATIC_DATA_PARTS[product]
    code = _FILENAME_CODE[record_type]
    suffix = _format_suffix(product, year, file_format)
    name = f"{year}_public_{code}{infix}_{suffix}.zip"
    return f"{FILE_SERVER}/static-data/{segment}/{year}/{name}"


def discover_files(
    products: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    file_format: HMDAFileFormat = "csv",
) -> list[HMDAFile]:
    """Expand a selection into candidate files from the coverage table.

    Pure: it reads `_COVERAGE` and touches no network. Candidates for an
    open-ended series run through ``max_year`` (or the current year), so the
    leading edge is offered up for probing rather than assumed present.

    Args:
        products: Products to include. ``None`` = `DEFAULT_PRODUCTS`.
        datasets: Record types to include. ``None`` = all four.
        min_year: First filing year to consider. ``None`` = each series' own start.
        max_year: Last filing year to consider. ``None`` = the current year.
        file_format: Delimiter variant to fetch.

    Returns:
        Candidate files, sorted by product, record type, then year.

    Raises:
        ValueError: If an unknown product or record type is given.
    """
    selected_products = resolve_products(products)
    selected_types = resolve_download_datasets(datasets)
    ceiling = max_year if max_year is not None else date.today().year

    files: list[HMDAFile] = []
    for product in selected_products:
        for record_type in selected_types:
            bounds = _COVERAGE.get((product, record_type))
            if bounds is None:
                continue
            first_year, last_year = bounds
            start = max(first_year, min_year) if min_year is not None else first_year
            end = min(last_year, ceiling) if last_year is not None else ceiling
            for year in range(start, end + 1):
                url = build_url(product, record_type, year, file_format)
                files.append(
                    HMDAFile(
                        product=product,
                        record_type=record_type,
                        year=year,
                        url=url,
                        filename=Path(url).name,
                        subfolder=record_type,
                    )
                )
            # The ARID->LEI crosswalk rides along with the 2017 snapshot panel.
            if product == "snapshot" and record_type == "panel" and start <= 2017 <= end:
                name = f"{_ARID_LEI_XREF}_{'csv' if file_format == 'csv' else 'psv'}.zip"
                files.append(
                    HMDAFile(
                        product=product,
                        record_type=record_type,
                        year=2017,
                        url=f"{FILE_SERVER}/static-data/snapshot/2017/arid2017tolei/{name}",
                        filename=name,
                        subfolder="misc",
                    )
                )

    return files


def _is_published(url: str, *, timeout: int | None = None) -> bool | None:
    """HEAD ``url`` to see whether CFPB has published it yet.

    Deliberately outside ``core.download``: a 404 here is not a failure but the
    answer "that year isn't out yet", and ``retry_request`` would retry it three
    times and log it as an error. Transient failures return ``None`` so the
    caller can queue the file anyway and let the download report the real error.

    Args:
        url: URL to probe.
        timeout: Per-request timeout (``None`` = config default).

    Returns:
        ``True`` if published, ``False`` on 404, ``None`` if the probe failed.
    """
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    try:
        response = requests.head(
            url,
            headers={"User-Agent": MortgageDataConfig.USER_AGENT},
            allow_redirects=True,
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning("HEAD %s failed (%s); queueing it anyway", url, e)
        return None

    if response.status_code == 404:
        return False
    if response.ok:
        return True
    logger.warning("HEAD %s returned HTTP %s; queueing it anyway", url, response.status_code)
    return None


def plan_downloads(
    files: Sequence[HMDAFile],
    output_dir: Path,
    *,
    timeout: int | None = None,
) -> tuple[list[DownloadTask], list[HMDAFile]]:
    """Split candidates into fetchable tasks and not-yet-published files.

    Only candidates missing from disk are probed: a file already downloaded is
    proof its URL resolves, whatever the mode. A steady-state run therefore
    probes just the handful of files it does not have, and a first run on an
    empty raw layer probes each series' leading edge once. What then happens to
    a file already on disk — skipped, size-checked, or overwritten — is
    ``batch_download``'s mode to decide, so it plays no part here.

    Args:
        files: Candidates from `discover_files`.
        output_dir: Root of the raw layer.
        timeout: Per-probe timeout (``None`` = config default).

    Returns:
        ``(tasks, unpublished)`` — download tasks in candidate order, and the
        candidates CFPB has not published yet.
    """
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    tasks: list[DownloadTask] = []
    unpublished: list[HMDAFile] = []

    for file in files:
        path = file.destination(output_dir)
        if not path.exists() and _is_published(file.url, timeout=timeout) is False:
            logger.debug("Not published yet: %s", file.filename)
            unpublished.append(file)
            continue
        tasks.append(DownloadTask(url=file.url, destination=path, headers=headers))

    if unpublished:
        logger.info(
            "%s candidate(s) not published yet: %s",
            len(unpublished),
            ", ".join(f.filename for f in unpublished),
        )
    return tasks, unpublished


def download_hmda_files(
    products: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    output_dir: Path | str | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    file_format: HMDAFileFormat = "csv",
    overwrite: bool = False,
    refresh: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download HMDA files from the CFPB file servers.

    Args:
        products: Products to fetch. ``None`` = `DEFAULT_PRODUCTS`.
        datasets: Record types to fetch. ``None`` = all four.
        output_dir: Root of the raw layer. ``None`` = ``HMDAConfig.HMDA_RAW_DIR``.
        min_year: First filing year to fetch. ``None`` = each series' own start.
        max_year: Last filing year to fetch. ``None`` = the current year.
        file_format: Delimiter variant to fetch.
        overwrite: Re-download files already on disk.
        refresh: Re-download only files whose size differs from the server's.
            Ignored when ``overwrite`` is set.
        pause: Seconds between downloads (``None`` = config default).
        timeout: Per-request timeout (``None`` = config default).
        retries: Retry attempts per file (``None`` = config default).
        show_progress: Display per-file progress bars.

    Returns:
        List of DownloadResult objects, one per queued file.

    Raises:
        ValueError: If an unknown product or record type is given.
    """
    root = Path(output_dir) if output_dir is not None else RAW_DIR
    root.mkdir(parents=True, exist_ok=True)

    files = discover_files(
        products,
        datasets,
        min_year=min_year,
        max_year=max_year,
        file_format=file_format,
    )
    logger.info("HMDA download: %s candidate file(s) from the coverage table", len(files))

    tasks, _unpublished = plan_downloads(files, root, timeout=timeout)
    if not tasks:
        logger.warning("No HMDA files to download for this selection")
        return []

    mode: Literal["skip", "overwrite", "if_size_diff"] = (
        "overwrite" if overwrite else ("if_size_diff" if refresh else "skip")
    )
    results = batch_download(
        tasks,
        mode=mode,
        pause_seconds=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
        source="hmda",
        expect={"zip"},
        raw_dir=root,
    )

    summary = summarize_results(results)
    logger.info(
        "HMDA download complete: %s downloaded, %s skipped, %s failed",
        summary.get("success", 0),
        summary.get("skipped_exists", 0) + summary.get("skipped_size_match", 0),
        summary.get("failed", 0),
    )
    return results


__all__ = [
    "FILE_SERVER",
    "HISTORIC_SERVER",
    "HMDAFile",
    "build_url",
    "determine_raw_subfolder",
    "discover_files",
    "download_hmda_files",
    "plan_downloads",
]
