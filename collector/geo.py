"""Geocoding with a persistent cache, and geographic filtering — القسم 4.5.

The base service area (config.yaml) is checked first since it never needs a
DB round-trip; dynamic return-watch areas (القسم 8.3) are checked against
`service_areas`, which the Return Finder inserts/expires as watches open
and close.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import httpx
from sqlalchemy import text

from shared.config import get_config, get_secrets
from shared.db import active_service_areas, get_engine
from matcher.routing import haversine_km

log = logging.getLogger("collector.geo")

# Nominatim's usage policy caps public-instance requests at 1/second; a
# platform with rides spread across the whole country can easily produce a
# few dozen distinct addresses in one poll cycle, which without this
# throttle blew straight through that limit and got 429'd on most of them —
# self-hosting Nominatim (القسم 4 mentions this) removes the need for this
# once it matters enough to bother.
_MIN_REQUEST_INTERVAL_S = 1.1
_rate_limit_lock = asyncio.Lock()
_last_request_at = 0.0


async def geocode(address: str) -> tuple[float, float] | None:
    address = address.strip()
    if not address:
        return None
    address_hash = hashlib.sha256(address.lower().encode("utf-8")).hexdigest()

    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text("SELECT lat, lon FROM geocode_cache WHERE address_hash = :h"),
                {"h": address_hash},
            )
        ).first()
        if row is not None:
            return float(row.lat), float(row.lon)

    result = await _geocode_via_nominatim(address)
    if result is None:
        return None

    lat, lon = result
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO geocode_cache (address_hash, address, lat, lon, provider)
                VALUES (:h, :address, :lat, :lon, 'nominatim')
                ON CONFLICT (address_hash) DO NOTHING
                """
            ),
            {"h": address_hash, "address": address, "lat": lat, "lon": lon},
        )
    return lat, lon


async def _throttle_nominatim() -> None:
    global _last_request_at
    async with _rate_limit_lock:
        wait = _MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def _geocode_via_nominatim(address: str) -> tuple[float, float] | None:
    base_url = get_secrets().nominatim_url
    await _throttle_nominatim()
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "fleet-dispatch-monitor/1.0"}) as client:
            resp = await client.get(
                f"{base_url}/search", params={"q": address, "format": "json", "limit": 1}
            )
            resp.raise_for_status()
            results = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("geocoding failed", extra={"extra_fields": {"address": address, "error": str(e)}})
        return None

    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


async def in_service_area(lat: float, lon: float) -> bool:
    cfg = get_config().service_area
    center_lat, center_lon = cfg.center
    if haversine_km(lat, lon, center_lat, center_lon) <= cfg.radius_km:
        return True

    for area_lat, area_lon, radius_km in await active_service_areas():
        if haversine_km(lat, lon, area_lat, area_lon) <= radius_km:
            return True
    return False
