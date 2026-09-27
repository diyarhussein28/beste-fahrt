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


async def try_claim_job(fp: str, new_status: JobStatus = JobStatus.DISPATCHED) -> bool:
    """Atomically flips an 'open' job to `new_status`. Returns False if it was
    already claimed — used to make "first accept wins" race-safe in broadcast
    mode (القسم 7.2).
    """
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text("UPDATE jobs SET status = :status WHERE fp = :fp AND status = 'open'"),
            {"status": new_status.value, "fp": fp},
        )
        return (result.rowcount or 0) > 0


async def get_job(fp: str) -> Job | None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT fp, platform_id, pickup_addr, dropoff_addr,
                           ST_Y(pickup_geom::geometry) AS pickup_lat, ST_X(pickup_geom::geometry) AS pickup_lon,
                           ST_Y(dropoff_geom::geometry) AS dropoff_lat, ST_X(dropoff_geom::geometry) AS dropoff_lon,
                           price_eur, route_km, pickup_date, url, status, first_seen, gone_at
                    FROM jobs WHERE fp = :fp
                    """
                ),
                {"fp": fp},
            )
        ).mappings().first()
        if row is None:
            return None
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


async def get_driver_by_chat_id(chat_id: int) -> Driver | None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, telegram_chat_id, phone, status, active,
                           location_consent, license_classes, jobs_today, allow_overnight,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           home_city
                    FROM drivers WHERE telegram_chat_id = :chat_id
                    """
                ),
                {"chat_id": chat_id},
            )
        ).mappings().first()
        if row is None:
            return None
        return Driver(
            id=row["id"],
            name=row["name"],
            telegram_chat_id=row["telegram_chat_id"],
            phone=row["phone"],
            status=DriverStatus(row["status"]),
            active=row["active"],
            location_consent=row["location_consent"],
            license_classes=list(row["license_classes"] or []),
            jobs_today=row["jobs_today"],
            home_lat=row["home_lat"],
            home_lon=row["home_lon"],
            home_city=row["home_city"],
            allow_overnight=row["allow_overnight"],
        )


async def get_driver(driver_id: int) -> Driver | None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, telegram_chat_id, phone, status, active,
                           location_consent, license_classes, jobs_today, allow_overnight,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           home_city
                    FROM drivers WHERE id = :id
                    """
                ),
                {"id": driver_id},
            )
        ).mappings().first()
        if row is None:
            return None
        return Driver(
            id=row["id"],
            name=row["name"],
            telegram_chat_id=row["telegram_chat_id"],
            phone=row["phone"],
            status=DriverStatus(row["status"]),
            active=row["active"],
            location_consent=row["location_consent"],
            license_classes=list(row["license_classes"] or []),
            jobs_today=row["jobs_today"],
            home_lat=row["home_lat"],
            home_lon=row["home_lon"],
            home_city=row["home_city"],
            allow_overnight=row["allow_overnight"],
        )


async def find_driver_by_name(name: str) -> Driver | None:
    """Case-insensitive exact match — used by the manager's /remove_driver
    and /activate_driver bot commands.
    """
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, telegram_chat_id, phone, status, active,
                           location_consent, license_classes, jobs_today, allow_overnight,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           home_city
                    FROM drivers WHERE lower(name) = lower(:name)
                    """
                ),
                {"name": name},
            )
        ).mappings().first()
        if row is None:
            return None
        return Driver(
            id=row["id"], name=row["name"], telegram_chat_id=row["telegram_chat_id"], phone=row["phone"],
            status=DriverStatus(row["status"]), active=row["active"], location_consent=row["location_consent"],
            license_classes=list(row["license_classes"] or []), jobs_today=row["jobs_today"],
            home_lat=row["home_lat"], home_lon=row["home_lon"], home_city=row["home_city"],
            allow_overnight=row["allow_overnight"],
        )


async def add_driver(name: str) -> Driver:
    async with get_engine().begin() as conn:
        row = (
            await conn.execute(
                text("INSERT INTO drivers (name, status, active) VALUES (:n, 'off_duty', TRUE) RETURNING id"),
                {"n": name},
            )
        ).first()
    return Driver(id=row.id, name=name, status=DriverStatus.OFF_DUTY, active=True)


async def set_driver_active(driver_id: int, active: bool) -> None:
    """Deactivating stops a driver from ever being ranked/dispatched to
    again, without touching their historical dispatches/KPI data — القسم
    10's "أقل صلاحية" principle applied to offboarding, not a hard delete.
    """
    async with get_engine().begin() as conn:
        if active:
            await conn.execute(text("UPDATE drivers SET active = TRUE WHERE id = :id"), {"id": driver_id})
        else:
            await conn.execute(
                text("UPDATE drivers SET active = FALSE, status = 'off_duty' WHERE id = :id"), {"id": driver_id}
            )


async def set_driver_consent(chat_id: int, consent: bool) -> bool:
    """Driver self-service — القسم 11.3: الموافقة يجب أن تكون من صاحب البيانات."""
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text("UPDATE drivers SET location_consent = :c WHERE telegram_chat_id = :chat_id"),
            {"c": consent, "chat_id": chat_id},
        )
        return (result.rowcount or 0) > 0


async def set_driver_shift_status(chat_id: int, status: DriverStatus) -> bool:
    """Driver self-service shift toggle (/available, /offline). Without this,
    a driver stays at the 'off_duty' default forever and never gets ranked —
    ranked_drivers_for_job only considers status = 'available'.
    """
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text("UPDATE drivers SET status = :status WHERE telegram_chat_id = :chat_id AND active"),
            {"status": status.value, "chat_id": chat_id},
        )
        return (result.rowcount or 0) > 0


async def set_driver_home(driver_id: int, lat: float, lon: float, city: str | None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE drivers SET home_geom = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), home_city = :city
                WHERE id = :id
                """
            ),
            {"lat": lat, "lon": lon, "city": city, "id": driver_id},
        )


async def create_driver_invite(driver_id: int, code: str, ttl_days: int = 7) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO driver_invites (code, driver_id, expires_at)
                VALUES (:code, :driver_id, now() + make_interval(days => :ttl))
                """
            ),
            {"code": code, "driver_id": driver_id, "ttl": ttl_days},
        )


async def consume_driver_invite(code: str, chat_id: int) -> Driver | None:
    """Atomically claims an invite (if unused and unexpired) and links the
    driver record to the Telegram chat_id that opened the deep link.
    """
    async with get_engine().begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    UPDATE driver_invites SET used_at = now()
                    WHERE code = :code AND used_at IS NULL AND expires_at > now()
                    RETURNING driver_id
                    """
                ),
                {"code": code},
            )
        ).first()
        if row is None:
            return None
        await conn.execute(
            text("UPDATE drivers SET telegram_chat_id = :chat_id WHERE id = :id"),
            {"chat_id": chat_id, "id": row.driver_id},
        )
    return await get_driver(row.driver_id)


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
                           d.active, d.location_consent, d.license_classes, d.jobs_today,
                           d.allow_overnight, d.home_city,
                           ST_Y(d.home_geom::geometry) AS home_lat, ST_X(d.home_geom::geometry) AS home_lon,
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
                license_classes=list(row["license_classes"] or []),
                jobs_today=row["jobs_today"],
                home_lat=row["home_lat"],
                home_lon=row["home_lon"],
                home_city=row["home_city"],
                allow_overnight=row["allow_overnight"],
            ),
            approach_km=float(row["approach_km"]),
            loc_age_min=float(row["loc_age_min"]),
            score=0.0,  # filled in by matcher.engine.score()
        )
        for row in rows
    ]


async def log_dispatch(job_fp: str, driver_id: int, rank: int, approach_km: float | None = None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO dispatches (job_fp, driver_id, rank, sent_at, approach_km)
                VALUES (:job_fp, :driver_id, :rank, now(), :approach_km)
                """
            ),
            {"job_fp": job_fp, "driver_id": driver_id, "rank": rank, "approach_km": approach_km},
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


async def beat(component: str) -> None:
    """Heartbeat write — القسم 9.3: كل مكوّن يكتب نبضة كل دقيقة."""
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO heartbeats (component, last_beat) VALUES (:c, now())
                ON CONFLICT (component) DO UPDATE SET last_beat = now()
                """
            ),
            {"c": component},
        )


async def stale_heartbeats(max_age_seconds: int) -> list[tuple[str, "datetime"]]:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT component, last_beat FROM heartbeats
                    WHERE last_beat < now() - make_interval(secs => :max_age)
                    """
                ),
                {"max_age": max_age_seconds},
            )
        ).all()
        return [(r.component, r.last_beat) for r in rows]


async def list_drivers() -> list[Driver]:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, telegram_chat_id, phone, status, active,
                           location_consent, license_classes, jobs_today, allow_overnight,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           home_city
                    FROM drivers ORDER BY name
                    """
                )
            )
        ).mappings().all()
        return [
            Driver(
                id=r["id"],
                name=r["name"],
                telegram_chat_id=r["telegram_chat_id"],
                phone=r["phone"],
                status=DriverStatus(r["status"]),
                active=r["active"],
                location_consent=r["location_consent"],
                license_classes=list(r["license_classes"] or []),
                jobs_today=r["jobs_today"],
                home_lat=r["home_lat"],
                home_lon=r["home_lon"],
                home_city=r["home_city"],
                allow_overnight=r["allow_overnight"],
            )
            for r in rows
        ]


async def list_open_jobs(limit: int = 50) -> list[Job]:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT fp, platform_id, pickup_addr, dropoff_addr,
                           ST_Y(pickup_geom::geometry) AS pickup_lat, ST_X(pickup_geom::geometry) AS pickup_lon,
                           ST_Y(dropoff_geom::geometry) AS dropoff_lat, ST_X(dropoff_geom::geometry) AS dropoff_lon,
                           price_eur, route_km, pickup_date, url, status, first_seen, gone_at
                    FROM jobs WHERE status IN ('open', 'dispatched')
                    ORDER BY first_seen DESC LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).mappings().all()
        return [
            Job(
                fp=r["fp"], platform_id=r["platform_id"], pickup_addr=r["pickup_addr"], dropoff_addr=r["dropoff_addr"],
                pickup_lat=r["pickup_lat"], pickup_lon=r["pickup_lon"],
                dropoff_lat=r["dropoff_lat"], dropoff_lon=r["dropoff_lon"],
                price_eur=float(r["price_eur"]) if r["price_eur"] is not None else None,
                route_km=float(r["route_km"]) if r["route_km"] is not None else None,
                pickup_date=r["pickup_date"], url=r["url"], status=JobStatus(r["status"]),
                first_seen=r["first_seen"], gone_at=r["gone_at"],
            )
            for r in rows
        ]


async def list_recent_dispatches(limit: int = 50) -> list[dict]:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT d.sent_at, d.rank, d.response, d.responded_at, d.approach_km,
                           dr.name AS driver_name, j.fp AS job_fp, j.pickup_addr, j.dropoff_addr, j.price_eur
                    FROM dispatches d
                    JOIN drivers dr ON dr.id = d.driver_id
                    JOIN jobs j ON j.fp = d.job_fp
                    ORDER BY d.sent_at DESC LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]


async def list_dispatch_history() -> list[dict]:
    """Every dispatch ever recorded, oldest first — nothing here is ever
    purged (unlike raw location pings, which القسم 11.3 caps at 30 days),
    so this is the full history from a driver's first day to today. Grouped
    by driver/week for the admin panel's history tab.
    """
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT d.sent_at, d.rank, d.response, d.responded_at, d.approach_km,
                           dr.id AS driver_id, dr.name AS driver_name,
                           j.fp AS job_fp, j.pickup_addr, j.dropoff_addr, j.price_eur, j.route_km
                    FROM dispatches d
                    JOIN drivers dr ON dr.id = d.driver_id
                    JOIN jobs j ON j.fp = d.job_fp
                    ORDER BY dr.name, d.sent_at ASC
                    """
                )
            )
        ).mappings().all()
        return [dict(r) for r in rows]


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
