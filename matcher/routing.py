"""Distance calculation — القسم 6.2.

Stage 1 (haversine) is used to rank every candidate cheaply. Stage 2 (real
road distance via OSRM) is only computed for the closest few candidates,
since it needs a network call and the ranking rarely changes for drivers
that were far apart to begin with.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

import httpx

from shared.config import get_secrets

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


async def road_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
    """Real road distance via a self-hosted OSRM instance. Returns None (and
    callers should fall back to haversine) when OSRM isn't configured or the
    call fails — road distance is a refinement, not a hard dependency.
    """
    osrm_url = get_secrets().osrm_url
    if not osrm_url:
        return None
    url = f"{osrm_url}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data["routes"][0]["distance"] / 1000
    except (httpx.HTTPError, KeyError, IndexError):
        return None


async def refine_top_candidates(
    candidates: list[tuple[float, float, float]],  # (lat, lon, haversine_km) per candidate
    target_lat: float,
    target_lon: float,
    top_n: int,
) -> list[float]:
    """Replaces the haversine distance of the closest `top_n` candidates with
    real road distance where OSRM is available, leaving the rest untouched.
    """
    ranked_km = [c[2] for c in candidates]
    order = sorted(range(len(candidates)), key=lambda i: ranked_km[i])[:top_n]
    for i in order:
        lat, lon, hav_km = candidates[i]
        road_km = await road_distance_km(lat, lon, target_lat, target_lon)
        if road_km is not None:
            ranked_km[i] = road_km
    return ranked_km
