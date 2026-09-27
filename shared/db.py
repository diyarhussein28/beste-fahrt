"""Async DB access layer (PostgreSQL + PostGIS). Uses raw SQL via SQLAlchemy's
async engine rather than the ORM, since most of the value here is in PostGIS
geography operators (ST_Distance, ST_DWithin) that don't map cleanly onto the
ORM — see migrations/0001_init.sql for the schema these queries assume.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from shared.config import get_secrets
from shared.models import Driver, DriverStatus, Job, JobStatus, RankedDriver


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_secrets().database_url, pool_pre_ping=True)


def get_sessionmaker():
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def is_new_job(fp: str) -> bool:
    async with get_engine().connect() as conn:
        row = (await conn.execute(text("SELECT 1 FROM jobs WHERE fp = :fp"), {"fp": fp})).first()
        return row is None


async def upsert_job(job: Job) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO jobs (fp, platform_id, pickup_addr, dropoff_addr,
                                   pickup_geom, dropoff_geom, price_eur, route_km,
                                   pickup_date, url, status, first_seen)
                VALUES (:fp, :platform_id, :pickup_addr, :dropoff_addr,
                        ST_SetSRID(ST_MakePoint(:pickup_lon, :pickup_lat), 4326),
                        ST_SetSRID(ST_MakePoint(:dropoff_lon, :dropoff_lat), 4326),
                        :price_eur, :route_km, :pickup_date, :url, :status, :first_seen)
                ON CONFLICT (fp) DO UPDATE SET
                    status = EXCLUDED.status,
                    price_eur = EXCLUDED.price_eur,
                    route_km = EXCLUDED.route_km
                """
            ),
            {
                "fp": job.fp,
                "platform_id": job.platform_id,
                "pickup_addr": job.pickup_addr,
                "dropoff_addr": job.dropoff_addr,
                "pickup_lon": job.pickup_lon,
                "pickup_lat": job.pickup_lat,
                "dropoff_lon": job.dropoff_lon,
                "dropoff_lat": job.dropoff_lat,
                "price_eur": job.price_eur,
                "route_km": job.route_km,
                "pickup_date": job.pickup_date,
                "url": job.url,
                "status": job.status.value,
                "first_seen": job.first_seen,
            },
        )


async def mark_jobs_gone(seen_fps: set[str], since: datetime) -> int:
    """Flip any open job not seen in the latest poll to 'gone' — القسم 4.4."""
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE jobs SET status = 'gone', gone_at = now()
                WHERE status = 'open' AND first_seen < :since
                  AND NOT (fp = ANY(:seen_fps))
                """
            ),
            {"since": since, "seen_fps": list(seen_fps)},
        )
        return result.rowcount or 0


async def set_job_status(fp: str, status: JobStatus) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET status = :status WHERE fp = :fp"),
            {"status": status.value, "fp": fp},
        )


async def record_driver_location(driver_id: int, lat: float, lon: float, source: str) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO driver_locations (driver_id, geom, source, recorded_at)
                VALUES (:driver_id, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :source, now())
                """
            ),
            {"driver_id": driver_id, "lat": lat, "lon": lon, "source": source},
        )


async def ranked_drivers_for_job(job_fp: str, limit: int = 5) -> list[RankedDriver]:
    """Nearest available drivers for a job — القسم 6.4."""
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT d.id, d.name, d.telegram_chat_id, d.phone, d.status,
                           d.active, d.location_consent,
                           ST_Distance(l.geom, j.pickup_geom) / 1000 AS approach_km,
                           EXTRACT(EPOCH FROM now() - l.recorded_at) / 60 AS loc_age_min
                    FROM drivers d
                    JOIN LATERAL (
                        SELECT geom, recorded_at FROM driver_locations
                        WHERE driver_id = d.id ORDER BY recorded_at DESC LIMIT 1
                    ) l ON TRUE
                    CROSS JOIN (SELECT pickup_geom FROM jobs WHERE fp = :fp) j
                    WHERE d.active AND d.status = 'available'
                    ORDER BY approach_km
                    LIMIT :limit
                    """
                ),
                {"fp": job_fp, "limit": limit},
            )
        ).mappings().all()

    return [
        RankedDriver(
            driver=Driver(
                id=row["id"],
                name=row["name"],
                telegram_chat_id=row["telegram_chat_id"],
                phone=row["phone"],
                status=DriverStatus(row["status"]),
                active=row["active"],
                location_consent=row["location_consent"],
            ),
            approach_km=float(row["approach_km"]),
            loc_age_min=float(row["loc_age_min"]),
            score=0.0,  # filled in by matcher.engine.score()
        )
        for row in rows
    ]


async def log_dispatch(job_fp: str, driver_id: int, rank: int) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO dispatches (job_fp, driver_id, rank, sent_at)
                VALUES (:job_fp, :driver_id, :rank, now())
                """
            ),
            {"job_fp": job_fp, "driver_id": driver_id, "rank": rank},
        )


async def record_dispatch_response(job_fp: str, driver_id: int, response: str) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE dispatches SET response = :response, responded_at = now()
                WHERE job_fp = :job_fp AND driver_id = :driver_id
                  AND responded_at IS NULL
                """
            ),
            {"job_fp": job_fp, "driver_id": driver_id, "response": response},
        )


async def active_service_areas() -> list[tuple[float, float, float]]:
    """(center_lat, center_lon, radius_km) for the base area + open return watches."""
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon, radius_km
                    FROM service_areas WHERE active
                      AND (expires_at IS NULL OR expires_at > now())
                    """
                )
            )
        ).all()
        return [(r.lat, r.lon, r.radius_km) for r in rows]
