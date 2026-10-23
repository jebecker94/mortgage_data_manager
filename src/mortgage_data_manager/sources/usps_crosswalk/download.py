"""Download USPS ZIP Code Crosswalk data from the HUD API.

Saves each quarter's untouched API response as raw JSON, one file per
(type, year, quarter). Schema enforcement and parquet conversion happen in
bronze.

Not routed through ``core.download.batch_download``, deliberately: that helper
calls ``raise_for_status()``, retries three times and then logs ``ERROR``. Here a
404 is not a failure — it is how HUD says "that quarter isn't published yet", and
every run probes the leading edge. Through ``batch_download`` a normal run would
triple its requests and print a wall of red. The loop below treats 404 as a
routine negative answer, and skips the rest of a quarter as soon as its first
type reports one, so an unpublished quarter costs one request rather than twelve.
Every saved body and every "not published" answer is still written to the raw
download ledger (``method="api_json"``) so the acquisition history is complete.
"""

from __future__ import annotations

import json
import time
from typing import cast

import requests

from mortgage_data_manager.core.download import SKIPPED_STATUSES, DownloadResult, DownloadStatus
from mortgage_data_manager.core.download_ledger import (
    DownloadRecord,
    append_record,
    inspect_file,
    relative_destination,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.usps_crosswalk.config import (
    CROSSWALK_TYPES_BY_NAME,
    VALID_CROSSWALK_NAMES,
    USPSCrosswalkConfig,
    current_quarter,
    quarters_for,
)

logger = get_logger(__name__)


def validate_datasets(datasets: list[str] | None) -> list[str]:
    """Return the requested crosswalk types, defaulting to all 12.

    Args:
        datasets: Crosswalk type names, or None for all 12.

    Returns:
        The validated crosswalk type names.

    Raises:
        ValueError: If any name is not a known crosswalk type.
    """
    if not datasets:
        return list(VALID_CROSSWALK_NAMES)
    unknown = [d for d in datasets if d not in VALID_CROSSWALK_NAMES]
    if unknown:
        raise ValueError(f"Unknown crosswalk type(s): {unknown}. Valid: {VALID_CROSSWALK_NAMES}")
    return datasets


def download_crosswalk(
    type_id: int,
    year: int,
    quarter: int,
    token: str,
    timeout: int | None = None,
) -> tuple[dict | None, int | None]:
    """Fetch one type-quarter from the API.

    Args:
        type_id: HUD crosswalk type ID (1-12).
        year: Data year.
        quarter: Data quarter (1-4).
        token: HUD API bearer token.
        timeout: Per-request timeout in seconds.

    Returns:
        ``(body, http_status)``: the response JSON, or ``None`` if the quarter is
        unavailable (404, or the pre-2021 400 that ``query=All`` returns) or the
        request failed (status ``None``).
    """
    params: dict[str, str | int] = {
        "type": type_id,
        "year": year,
        "quarter": quarter,
        "query": "All",
    }
    try:
        response = requests.get(
            USPSCrosswalkConfig.USPS_CROSSWALK_API_BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.error("Type %d %d Q%d: request failed: %s", type_id, year, quarter, e)
        return None, None

    if response.status_code == 200:
        return cast(dict, response.json()), 200
    if response.status_code in (400, 404):
        # 404 = not published; 400 = `query=All` unsupported, which HUD only
        # officially guarantees for 2021+.
        logger.debug(
            "Type %d %d Q%d unavailable (%d)", type_id, year, quarter, response.status_code
        )
        return None, response.status_code
    logger.error(
        "Type %d %d Q%d: HTTP %d %s",
        type_id,
        year,
        quarter,
        response.status_code,
        response.text[:200],
    )
    return None, response.status_code


def _request_url(type_id: int, year: int, quarter: int) -> str:
    return (
        f"{USPSCrosswalkConfig.USPS_CROSSWALK_API_BASE_URL}"
        f"?type={type_id}&year={year}&quarter={quarter}&query=All"
    )


def _record(
    name: str,
    year: int,
    quarter: int,
    *,
    status: DownloadStatus,
    http_status: int | None = None,
    error: str | None = None,
) -> DownloadResult:
    """Ledger one type-quarter (skips are not recorded) and return its DownloadResult."""
    path = USPSCrosswalkConfig.raw_path(name, year, quarter)
    raw_dir = USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR
    url = _request_url(CROSSWALK_TYPES_BY_NAME[name].type_id, year, quarter)
    info = inspect_file(path) if status is DownloadStatus.SUCCESS else None
    if status not in SKIPPED_STATUSES:
        append_record(
            DownloadRecord(
                source="usps_crosswalk",
                destination=relative_destination(path, raw_dir),
                method="api_json",
                status=status.value,
                dataset=name,
                url=url,
                http_status=http_status,
                bytes=info.bytes if info else None,
                sha256=info.sha256 if info else None,
                sniffed_type=info.sniffed_type if info else None,
                error=error,
            )
        )
    return DownloadResult(
        path=path,
        status=status,
        bytes_downloaded=info.bytes if info else 0,
        error_message=error,
        url=url,
        sha256=info.sha256 if info else None,
        sniffed_type=info.sniffed_type if info else None,
        http_status=http_status,
    )


def download_all(
    datasets: list[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
) -> list[DownloadResult]:
    """Download every missing type-quarter and save the raw JSON.

    Args:
        datasets: Crosswalk types to fetch. ``None`` = all 12.
        min_year: First year to consider. ``None`` = each type's own first
            published quarter (types 4/9 start 2017 Q4, 11/12 start 2018 Q2).
        max_year: Last year to consider. ``None`` = through the current quarter,
            which is what lets a new HUD release be picked up automatically.
        overwrite: Re-download type-quarters already present.
        pause: Seconds between requests (``None`` = ``USPS_CROSSWALK_REQUEST_DELAY``).
        timeout: Per-request timeout in seconds.

    Returns:
        One DownloadResult per type-quarter considered: skipped_exists for files
        already present, success, skipped_not_published (400/404) or failed. A
        quarter whose first type is unpublished is not probed further.

    Raises:
        ValueError: If ``USPS_CROSSWALK_API_KEY`` is unset or a dataset is invalid.
    """
    token = USPSCrosswalkConfig.USPS_CROSSWALK_API_KEY
    if not token:
        raise ValueError("USPS_CROSSWALK_API_KEY not set. Add it to your .env or the environment.")
    types = validate_datasets(datasets)
    through = (max_year, 4) if max_year is not None else current_quarter()
    pause = USPSCrosswalkConfig.USPS_CROSSWALK_REQUEST_DELAY if pause is None else pause

    # Group by quarter so an unpublished quarter can be abandoned after one probe.
    pending: dict[tuple[int, int], list[str]] = {}
    out: list[DownloadResult] = []
    for name in types:
        for year, quarter in quarters_for(name, through, since=(min_year, 1) if min_year else None):
            if overwrite or not USPSCrosswalkConfig.raw_path(name, year, quarter).exists():
                pending.setdefault((year, quarter), []).append(name)
            else:
                out.append(_record(name, year, quarter, status=DownloadStatus.SKIPPED_EXISTS))

    for (year, quarter), names in sorted(pending.items()):
        for position, name in enumerate(names):
            crosswalk = CROSSWALK_TYPES_BY_NAME[name]
            logger.info("Downloading %s %d Q%d...", name, year, quarter)
            data, http_status = download_crosswalk(crosswalk.type_id, year, quarter, token, timeout)
            time.sleep(pause)

            if data is None:
                unpublished = http_status in (400, 404)
                out.append(
                    _record(
                        name,
                        year,
                        quarter,
                        status=(
                            DownloadStatus.SKIPPED_NOT_PUBLISHED
                            if unpublished
                            else DownloadStatus.FAILED
                        ),
                        http_status=http_status,
                        error=None if unpublished else f"request failed (HTTP {http_status})",
                    )
                )
                if unpublished and position == 0:
                    logger.info(
                        "%d Q%d is not published yet — skipping its other %d type(s)",
                        year,
                        quarter,
                        len(names) - 1,
                    )
                    break
                continue

            results = data.get("data", {}).get("results")
            if not results:
                logger.warning("%s %d Q%d returned no records", name, year, quarter)
                out.append(
                    _record(
                        name,
                        year,
                        quarter,
                        status=DownloadStatus.FAILED,
                        http_status=http_status,
                        error="published quarter returned no records",
                    )
                )
                continue

            path = USPSCrosswalkConfig.raw_path(name, year, quarter)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
            out.append(
                _record(name, year, quarter, status=DownloadStatus.SUCCESS, http_status=http_status)
            )
            logger.info("Saved %s (%d records)", path.name, len(results))

    saved = sum(1 for r in out if r.status == DownloadStatus.SUCCESS)
    logger.info("USPS crosswalk download: saved %d raw file(s)", saved)
    return out
