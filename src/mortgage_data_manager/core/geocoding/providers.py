"""Census Geocoder provider implementations.

Two tiers, same endpoints, different ``benchmark`` parameter:
  - ``census_current`` uses ``Public_AR_Current``
  - ``census_2020``    uses ``Public_AR_Census2020``

Both expose:
  - a one-line endpoint for ad-hoc per-row requests (used when N <= batch_threshold)
  - a batch endpoint that accepts a CSV upload (used at larger scale)

Each provider returns a list of normalized result dicts with the keys defined
in ``RESULT_FIELDS`` so the rest of the pipeline is provider-agnostic.
"""

from __future__ import annotations

import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

CENSUS_ONELINE = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_BATCH = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"

BENCHMARKS = {
    "census_current": "Public_AR_Current",
    "census_2020": "Public_AR_Census2020",
}

RESULT_FIELDS = (
    "matched",
    "match_reason",
    "match_type",
    "matched_address",
    "lat",
    "lon",
    "tigerline_id",
    "provider",
    "confidence",
)


def _empty_result(reason: str, provider: str) -> dict:
    return {
        "matched": False,
        "match_reason": reason,
        "match_type": None,
        "matched_address": None,
        "lat": None,
        "lon": None,
        "tigerline_id": None,
        "provider": provider,
        "confidence": None,
    }


def _ok_result(match: dict, provider: str) -> dict:
    coords = match.get("coordinates", {}) or {}
    tl = match.get("tigerLine", {}) or {}
    return {
        "matched": True,
        "match_reason": "ok",
        "match_type": tl.get("side") or "match",
        "matched_address": match.get("matchedAddress"),
        "lat": coords.get("y"),
        "lon": coords.get("x"),
        "tigerline_id": tl.get("tigerLineId"),
        "provider": provider,
        # Census interpolates along TIGER segments — call it "medium".
        "confidence": "medium",
    }


def geocode_oneline(
    addresses: list[dict],
    *,
    provider: str,
    session: requests.Session | None = None,
    max_workers: int = 10,
    timeout_seconds: int = 30,
    progress: bool = True,
    progress_every: int = 200,
) -> list[dict]:
    """Geocode a list of address dicts via the Census one-line endpoint.

    Each input dict needs keys: ``address_id``, ``street``, ``city``, ``state``, ``zip``.
    Returns one result dict per input (same order) with the keys in RESULT_FIELDS.
    """
    if provider not in BENCHMARKS:
        raise ValueError(f"Unknown provider: {provider}")
    benchmark = BENCHMARKS[provider]
    sess = session or requests.Session()
    results: list[dict] = [None] * len(addresses)  # type: ignore[list-item]
    n = len(addresses)
    t0 = time.time()
    completed = 0

    def _one(i: int) -> tuple[int, dict]:
        row = addresses[i]
        street = (row.get("street") or "").strip()
        city = (row.get("city") or "").strip()
        state = (row.get("state") or "").strip()
        zip_ = (row.get("zip") or "").strip()
        if not (street and state):
            return i, _empty_result("incomplete_input", provider)
        one = f"{street}, {city}, {state} {zip_}".strip().rstrip(",")
        try:
            r = sess.get(
                CENSUS_ONELINE,
                params={"address": one, "benchmark": benchmark, "format": "json"},
                timeout=timeout_seconds,
            )
            if r.status_code == 429:
                return i, _empty_result("http_429", provider)
            if r.status_code >= 500:
                return i, _empty_result(f"http_{r.status_code}", provider)
            r.raise_for_status()
            matches = r.json().get("result", {}).get("addressMatches", [])
            if not matches:
                return i, _empty_result("no_match", provider)
            return i, _ok_result(matches[0], provider)
        except requests.HTTPError as e:
            sc = e.response.status_code if e.response is not None else "err"
            return i, _empty_result(f"http_{sc}", provider)
        except Exception as e:
            return i, _empty_result(f"err_{type(e).__name__}", provider)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed(ex.submit(_one, i) for i in range(n)):
            i, res = fut.result()
            results[i] = res
            completed += 1
            if progress and completed % progress_every == 0:
                rate = completed / max(time.time() - t0, 1e-9)
                logger.info(f"  geocoded {completed}/{n} ({rate:.1f}/s) via {provider}")

    return results


def _batch_post(rows: list[dict], benchmark: str, timeout_seconds: int) -> list[dict] | None:
    """One POST to the Census batch endpoint. ``rows`` capped to 10k by caller.

    Returns one result per input row (same order) or None on a hard failure
    (in which case the caller should fall through to one-line).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(
            [
                row.get("address_id") or "",
                row.get("street") or "",
                row.get("city") or "",
                row.get("state") or "",
                row.get("zip") or "",
            ]
        )
    data = buf.getvalue().encode("utf-8")
    try:
        r = requests.post(
            CENSUS_BATCH,
            files={"addressFile": ("addresses.csv", data, "text/csv")},
            data={"benchmark": benchmark},
            timeout=timeout_seconds,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Census batch POST failed ({type(e).__name__}): {e}; will fall through to one-line")
        return None

    # Response is CSV: id, input_address, match_indicator, match_type, matched_address, coords, tigerline_id, side
    parsed: list[dict] = [_empty_result("no_match", "census_batch") for _ in rows]
    text = r.text
    reader = csv.reader(io.StringIO(text), quotechar='"', skipinitialspace=True)
    id_to_idx = {(row.get("address_id") or ""): i for i, row in enumerate(rows)}
    for fields in reader:
        if not fields or fields[0] not in id_to_idx:
            continue
        idx = id_to_idx[fields[0]]
        match_indicator = fields[2] if len(fields) > 2 else ""
        if match_indicator == "Match" and len(fields) >= 6:
            coords = fields[5] or ""
            lat = lon = None
            if "," in coords:
                lon_s, lat_s = coords.split(",", 1)
                try:
                    lon = float(lon_s)
                    lat = float(lat_s)
                except ValueError:
                    pass
            parsed[idx] = {
                "matched": True,
                "match_reason": "ok",
                "match_type": fields[7] if len(fields) > 7 else "match",
                "matched_address": fields[4] if len(fields) > 4 else None,
                "lat": lat,
                "lon": lon,
                "tigerline_id": fields[6] if len(fields) > 6 else None,
                "provider": "census_batch",
                "confidence": "medium",
            }
    return parsed


def geocode_batch(
    addresses: list[dict],
    *,
    provider: str,
    chunk_size: int = 10_000,
    timeout_seconds: int = 600,
    progress: bool = True,
) -> list[dict]:
    """Geocode a list of address dicts via the Census batch endpoint.

    Falls through to one-line for any chunk that the batch endpoint refuses.
    """
    if provider not in BENCHMARKS:
        raise ValueError(f"Unknown provider: {provider}")
    benchmark = BENCHMARKS[provider]
    out: list[dict] = []
    t0 = time.time()
    for start in range(0, len(addresses), chunk_size):
        chunk = addresses[start : start + chunk_size]
        chunk_results = _batch_post(chunk, benchmark, timeout_seconds)
        if chunk_results is None:
            chunk_results = geocode_oneline(chunk, provider=provider, progress=False)
        # Stamp the tier identity onto every result so terminal misses also
        # carry the tier they failed at (otherwise the batch path leaves the
        # `census_batch` placeholder behind in the cache).
        for r in chunk_results:
            r["provider"] = provider
        out.extend(chunk_results)
        if progress:
            elapsed = time.time() - t0
            logger.info(f"  batch {len(out)}/{len(addresses)} ({len(out) / max(elapsed, 1e-9):.1f}/s) via {provider}")
    return out
