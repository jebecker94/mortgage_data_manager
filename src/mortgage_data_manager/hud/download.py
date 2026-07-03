"""Download HUD USPS ZIP Code Crosswalk data from the HUD API.

Downloads all 12 crosswalk types from 2010 to present, saving each quarter's
untouched API response as raw JSON. Schema enforcement (zero-padded geographic
codes, numeric ratios) and parquet conversion happen in the bronze layer.

Note:
    The API ``query=All`` parameter only officially works for 2021+ data.
    Pre-2021 requests may return 400 errors.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import requests

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud.config import (
    CROSSWALK_TYPES,
    HUDConfig,
    is_available,
)

logger = get_logger(__name__)


def download_crosswalk(
    type_id: int,
    year: int,
    quarter: int,
    token: str,
) -> dict | None:
    """Download crosswalk data for a specific type, year, and quarter.

    Args:
        type_id: HUD crosswalk type ID (1-12).
        year: Data year.
        quarter: Data quarter (1-4).
        token: HUD API bearer token.

    Returns:
        API response JSON dict, or None if request failed.
    """
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "type": type_id,
        "year": year,
        "quarter": quarter,
        "query": "All",
    }

    try:
        response = requests.get(HUDConfig.HUD_API_BASE_URL, headers=headers, params=params)

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logger.warning("No data found (404) for Type %d - %d Q%d", type_id, year, quarter)
            return None
        elif response.status_code == 400:
            logger.warning(
                "Bad Request (400) for Type %d - %d Q%d. "
                "(API limitation: 'query=All' may only work for 2021+)",
                type_id,
                year,
                quarter,
            )
            return None
        else:
            logger.error(
                "Error %d for Type %d - %d Q%d: %s",
                response.status_code,
                type_id,
                year,
                quarter,
                response.text[:200],
            )
            return None

    except Exception as e:
        logger.error("Exception downloading Type %d - %d Q%d: %s", type_id, year, quarter, e)
        return None


def download_all(
    overwrite: bool = False,
    min_year: int = 2010,
) -> None:
    """Download all crosswalk types from the HUD API.

    Downloads quarterly crosswalk data from ``min_year`` to the current
    quarter, saving each untouched API response as a JSON file in the raw
    directory. Conversion to parquet happens in the bronze layer.

    Args:
        overwrite: If True, re-download existing files.
        min_year: First year to download (default: 2010).

    Raises:
        ValueError: If HUD_API_KEY is not configured.
    """
    api_token = HUDConfig.HUD_API_KEY
    if not api_token:
        raise ValueError(
            "HUD_API_KEY not set. Set it in your .env file or as an environment variable."
        )

    output_dir = HUDConfig.HUD_RAW_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    current_year = datetime.now().year
    current_quarter = (datetime.now().month - 1) // 3 + 1

    for year in range(min_year, current_year + 1):
        for quarter in range(1, 5):
            if year == current_year and quarter > current_quarter:
                continue

            for type_id, crosswalk in CROSSWALK_TYPES.items():
                if not is_available(crosswalk, year, quarter):
                    continue

                type_dir = output_dir / crosswalk.name
                filename = f"{crosswalk.name}_{year}_Q{quarter}.json"
                filepath = type_dir / filename

                if filepath.exists() and not overwrite:
                    logger.debug(
                        "Skipping Type %d (%s) %d Q%d (already exists)",
                        type_id,
                        crosswalk.name,
                        year,
                        quarter,
                    )
                    continue

                logger.info(
                    "Downloading Type %d (%s) %d Q%d...",
                    type_id,
                    crosswalk.name,
                    year,
                    quarter,
                )

                data = download_crosswalk(type_id, year, quarter, api_token)

                if data and "data" in data and "results" in data["data"]:
                    results = data["data"]["results"]

                    if results:
                        # Save the untouched API response as raw JSON; schema
                        # enforcement + parquet conversion happen in bronze.
                        type_dir.mkdir(parents=True, exist_ok=True)
                        filepath.write_text(json.dumps(data), encoding="utf-8")
                        logger.info(
                            "Saved %d records to %s/%s", len(results), crosswalk.name, filename
                        )
                    else:
                        logger.warning("Empty results for Type %d %d Q%d", type_id, year, quarter)
                else:
                    if year < 2021:
                        logger.debug(
                            "Failed for Type %d %d Q%d. "
                            "Note: 'query=All' is officially supported only for 2021+.",
                            type_id,
                            year,
                            quarter,
                        )

                time.sleep(HUDConfig.HUD_REQUEST_DELAY)
