"""PostGIS queries shared by the return finder and the chain search —
القسم 8.8. Both need the same primitive: open jobs near a point that move a
driver closer to their home base within an acceptable pickup window.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from shared.db import get_engine
from shared.models import Job, JobStatus


async def candidates_near_point(
    from_lat: float,
    from_lon: float,
    home_lat: float,
    home_lon: float,
    after: datetime,
    pickup_radius_km: float,
    min_buffer_minutes: int,
    max_wait_hours: float,
) -> list[dict]:
    """Open jobs within `pickup_radius_km` of (from_lat, from_lon) whose
    drop-off is strictly closer to (home_lat, home_lon) than the starting
    point is, with a pickup time inside [after + buffer, after + max_wait].
    Returns dicts with the job fields plus deadhead_km/remaining_km/dist_from_home.
    """
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT j.fp, j.platform_id, j.pickup_addr, j.dropoff_addr,
                           ST_Y(j.pickup_geom::geometry) AS pickup_lat, ST_X(j.pickup_geom::geometry) AS pickup_lon,
                           ST_Y(j.dropoff_geom::geometry) AS dropoff_lat, ST_X(j.dropoff_geom::geometry) AS dropoff_lon,
                           j.price_eur, j.route_km, j.pickup_date, j.url, j.status, j.first_seen, j.gone_at,
                           ST_Distance(:from_point, j.pickup_geom) / 1000 AS deadhead_km,
                           ST_Distance(j.dropoff_geom, :home_point) / 1000 AS remaining_km,
                           ST_Distance(:from_point, :home_point) / 1000 AS dist_from_home
                    FROM jobs j
                    WHERE j.status = 'open'
                      AND ST_DWithin(j.pickup_geom, :from_point, :radius_m)
                      AND ST_Distance(j.dropoff_geom, :home_point) < ST_Distance(:from_point, :home_point)
                      AND j.pickup_date >= :after + make_interval(mins => :buffer_min)
                      AND j.pickup_date <= :after + make_interval(hours => :max_wait)
                    ORDER BY remaining_km ASC, j.price_eur DESC
                    LIMIT 10
                    """
                ),
                {
                    "from_point": f"SRID=4326;POINT({from_lon} {from_lat})",
                    "home_point": f"SRID=4326;POINT({home_lon} {home_lat})",
                    "radius_m": pickup_radius_km * 1000,
                    "after": after,
                    "buffer_min": min_buffer_minutes,
                    "max_wait": max_wait_hours,
                },
            )
        ).mappings().all()
        return [dict(r) for r in rows]


def row_to_job(row: dict) -> Job:
    return Job(
        fp=row["fp"],
        platform_id=row["platform_id"],
        pickup_addr=row["pickup_addr"],
        dropoff_addr=row["dropoff_addr"],
        pickup_lat=row["pickup_lat"],
        pickup_lon=row["pickup_lon"],
        dropoff_lat=row["dropoff_lat"],
        dropoff_lon=row["dropoff_lon"],
        price_eur=float(row["price_eur"]) if row["price_eur"] is not None else None,
        route_km=float(row["route_km"]) if row["route_km"] is not None else None,
        pickup_date=row["pickup_date"],
        url=row["url"],
        status=JobStatus(row["status"]),
        first_seen=row["first_seen"],
        gone_at=row["gone_at"],
    )


def estimate_eta(pickup_date: datetime | None, route_km: float | None, avg_speed_kmh: float = 70.0, buffer_hours: float = 0.5) -> datetime:
    """ETA_B تقريبية بدون OSRM — القسم 8: 'موعد الاستلام + مدة القيادة + هامش أمان'."""
    base = pickup_date or datetime.now()
    drive_hours = (route_km or 0.0) / avg_speed_kmh
    return base + timedelta(hours=drive_hours + buffer_hours)
