"""Public geocoding API.

The single entrypoint subpackages call is :func:`geocode_addresses`. It:

  1. Hashes every input address.
  2. Looks up the global cache and short-circuits any hits.
  3. Walks the provider chain (Census current → Census 2020) for cache misses.
  4. Writes new resolutions (success *and* terminal failures) back to the cache.
  5. Joins everything back onto the caller's DataFrame, preserving row order.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import polars as pl

from mortgage_data_manager.core.geocoding import cache as cachemod
from mortgage_data_manager.core.geocoding.providers import (
    geocode_batch,
    geocode_oneline,
)
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


INPUT_COLUMNS: tuple[str, ...] = ("address_id", "street", "city", "state", "zip")

OUTPUT_COLUMNS: tuple[str, ...] = (
    "geocode_lat",
    "geocode_lon",
    "geocode_matched",
    "geocode_match_reason",
    "geocode_match_type",
    "geocode_matched_address",
    "geocode_tigerline_id",
    "geocode_provider",
    "geocode_confidence",
    "geocode_cached_at",
)


def _validate_input(df: pl.DataFrame) -> None:
    missing = [c for c in INPUT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"geocode_addresses input is missing required columns: {missing}. "
            f"Expected columns: {INPUT_COLUMNS}"
        )


def _results_to_df(addresses: list[dict], results: list[dict]) -> pl.DataFrame:
    """Pair the provider results back with their input rows for cache-write."""
    return pl.DataFrame(
        {
            "address_hash": [a["address_hash"] for a in addresses],
            "street_norm": [a["_street_norm"] for a in addresses],
            "city_norm": [a["_city_norm"] for a in addresses],
            "state_norm": [a["_state_norm"] for a in addresses],
            "zip5": [a["_zip5"] for a in addresses],
            "geocode_lat": [r.get("lat") for r in results],
            "geocode_lon": [r.get("lon") for r in results],
            "geocode_matched": [bool(r.get("matched")) for r in results],
            "geocode_match_reason": [r.get("match_reason") for r in results],
            "geocode_match_type": [r.get("match_type") for r in results],
            "geocode_matched_address": [r.get("matched_address") for r in results],
            "geocode_tigerline_id": [r.get("tigerline_id") for r in results],
            "geocode_provider": [r.get("provider") for r in results],
            "geocode_confidence": [r.get("confidence") for r in results],
            "geocode_cached_at": [date.today()] * len(results),
        },
        schema=cachemod.CACHE_SCHEMA,
    )


def geocode_addresses(
    df: pl.DataFrame,
    *,
    provider_chain: Sequence[str] = ("census_current", "census_2020"),
    use_cache: bool = True,
    write_cache: bool = True,
    max_workers: int = 10,
    batch_threshold: int = 5_000,
    timeout_seconds: int = 30,
    progress: bool = True,
) -> pl.DataFrame:
    """Geocode a DataFrame of addresses with caching + fallback chain.

    Args:
        df: Input DataFrame. Required columns: address_id, street, city, state, zip.
        provider_chain: Order of providers to try. Each address only consumes
            an API call from a given provider if previous tiers missed.
        use_cache: If True, look up the global cache and skip already-resolved
            addresses (including terminal failures).
        write_cache: If True, write new resolutions back to the cache parquet.
        max_workers: Concurrent HTTP threads for the one-line endpoint.
        batch_threshold: Rows above this go through the batch endpoint.
        timeout_seconds: Per-request HTTP timeout.
        progress: If True, log periodic progress.

    Returns:
        Input DataFrame with the columns from OUTPUT_COLUMNS appended.
    """
    _validate_input(df)
    if df.is_empty():
        empty = {c: [] for c in OUTPUT_COLUMNS}
        return df.with_columns([pl.Series(c, []) for c in empty])

    # Attach normalized fields + hash
    keyed = cachemod.attach_hash(df)

    # Cache lookup
    cache = cachemod.load_cache() if use_cache else pl.DataFrame(schema=cachemod.CACHE_SCHEMA)
    hit_cols = [c for c in cachemod.CACHE_SCHEMA if c != "address_hash"]
    joined = keyed.join(cache.select(["address_hash", *hit_cols]), on="address_hash", how="left")

    n = len(joined)
    n_cached = joined.filter(pl.col("geocode_match_reason").is_not_null()).height
    n_todo = n - n_cached
    if progress:
        logger.info(
            f"geocode_addresses: {n:,} rows, {n_cached:,} cache hits, {n_todo:,} new addresses to geocode"
        )

    # Walk provider chain for cache misses
    if n_todo > 0:
        todo_mask = joined["geocode_match_reason"].is_null()
        # We address unique hashes only — one API call per unique address.
        todo_unique = (
            joined.filter(todo_mask)
            .unique(subset=["address_hash"], keep="first")
            .select(
                "address_hash",
                "_street_norm",
                "_city_norm",
                "_state_norm",
                "_zip5",
                pl.col("address_hash").alias("address_id"),  # batch endpoint needs a per-row id
                pl.col("street").alias("street"),
                pl.col("city").alias("city"),
                pl.col("state").alias("state"),
                pl.col("zip").alias("zip"),
            )
        )

        unresolved_addrs = todo_unique.to_dicts()
        resolved_results: list[dict] = [None] * len(unresolved_addrs)  # type: ignore[list-item]

        for tier in provider_chain:
            remaining_idx = [i for i, r in enumerate(resolved_results) if r is None or not r.get("matched")]
            if not remaining_idx:
                break
            sub = [unresolved_addrs[i] for i in remaining_idx]
            if progress:
                logger.info(f"  trying tier {tier!r} for {len(sub):,} addresses")
            if len(sub) > batch_threshold:
                tier_results = geocode_batch(sub, provider=tier, progress=progress)
            else:
                tier_results = geocode_oneline(
                    sub,
                    provider=tier,
                    max_workers=max_workers,
                    timeout_seconds=timeout_seconds,
                    progress=progress,
                )
            for i, res in zip(remaining_idx, tier_results):
                # Keep the new result only if previous tier hadn't matched.
                if resolved_results[i] is None or not resolved_results[i].get("matched"):
                    if res.get("matched"):
                        resolved_results[i] = res
                    elif resolved_results[i] is None:
                        resolved_results[i] = res

        # Fill in None slots with a terminal failure record so we cache the miss.
        for i, r in enumerate(resolved_results):
            if r is None:
                resolved_results[i] = {
                    "matched": False,
                    "match_reason": "no_match",
                    "match_type": None,
                    "matched_address": None,
                    "lat": None,
                    "lon": None,
                    "tigerline_id": None,
                    "provider": provider_chain[-1] if provider_chain else None,
                    "confidence": None,
                }

        new_cache_rows = _results_to_df(unresolved_addrs, resolved_results)
        if write_cache:
            written = cachemod.append_cache(new_cache_rows)
            if progress:
                logger.info(f"  cache now {written:,} rows ({len(new_cache_rows):,} new this run)")

        # Merge the new resolutions back into the keyed frame, by address_hash.
        joined = joined.drop(hit_cols).join(
            pl.concat([cache, new_cache_rows], how="diagonal_relaxed").select(["address_hash", *hit_cols]),
            on="address_hash",
            how="left",
        )

    out_columns = [
        "geocode_lat",
        "geocode_lon",
        pl.col("geocode_matched").fill_null(False).alias("geocode_matched"),
        "geocode_match_reason",
        "geocode_match_type",
        "geocode_matched_address",
        "geocode_tigerline_id",
        "geocode_provider",
        "geocode_confidence",
        "geocode_cached_at",
    ]
    result = joined.select(
        *[c for c in df.columns],
        *out_columns,
    )
    return result
