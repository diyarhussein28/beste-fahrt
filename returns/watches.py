"""Return-watch lifecycle — القسم 8.2, 8.3, 8.7. Opening a watch also opens
a temporary circular service area around the driver's drop-off point so the
Collector's geographic filter (القسم 4.5) picks up offers there too; closing
or expiring the watch removes that area again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from shared.config import ReturnTripConfig
from shared.db import get_engine
from shared.models import Driver, Job, ReturnWatch, WatchStatus

from returns.queries import estimate_eta

log = logging.getLogger("returns.watches")


def _area_name(watch_id: int) -> str:
    return f"return-watch-{watch_id}"


async def open_watch(driver: Driver, outbound: Job, config: ReturnTripConfig) -> ReturnWatch | None:
    if driver.home_lat is None or driver.home_lon is None:
        log.warning("driver has no home base set, cannot open a return watch", extra={"extra_fields": {"driver_id": driver.id}})
        return None
    if outbound.dropoff_lat is None or outbound.dropoff_lon is None:
        return None

    eta_b = estimate_eta(outbound.pickup_date, outbound.route_km)
    expires_at = eta_b + timedelta(hours=config.watch_expires_after_hours)

    async with get_engine().begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO return_watches
                        (driver_id, outbound_fp, from_geom, home_geom, available_at, expires_at, status)
                    VALUES (:driver_id, :outbound_fp,
                            ST_SetSRID(ST_MakePoint(:from_lon, :from_lat), 4326),
                            ST_SetSRID(ST_MakePoint(:home_lon, :home_lat), 4326),
                            :available_at, :expires_at, 'open')
                    RETURNING id
                    """
                ),
                {
                    "driver_id": driver.id,
                    "outbound_fp": outbound.fp,
                    "from_lat": outbound.dropoff_lat,
                    "from_lon": outbound.dropoff_lon,
                    "home_lat": driver.home_lat,
                    "home_lon": driver.home_lon,
                    "available_at": eta_b,
                    "expires_at": expires_at,
                },
            )
        ).first()
        watch_id = row.id

        await conn.execute(
            text(
                """
                INSERT INTO service_areas (name, geom, radius_km, active, expires_at)
                VALUES (:name, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :radius_km, TRUE, :expires_at)
                """
            ),
            {
                "name": _area_name(watch_id),
                "lat": outbound.dropoff_lat,
                "lon": outbound.dropoff_lon,
                "radius_km": config.pickup_radius_km,
                "expires_at": expires_at,
            },
        )

    log.info("return watch opened", extra={"extra_fields": {"watch_id": watch_id, "driver_id": driver.id}})
    return ReturnWatch(
        id=watch_id,
        driver_id=driver.id,
        outbound_fp=outbound.fp,
        from_lat=outbound.dropoff_lat,
        from_lon=outbound.dropoff_lon,
        home_lat=driver.home_lat,
        home_lon=driver.home_lon,
        available_at=eta_b,
        expires_at=expires_at,
        status=WatchStatus.OPEN,
    )


async def close_watch(watch_id: int, status: WatchStatus, matched_fp: str | None = None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE return_watches SET status = :status, matched_fp = :fp WHERE id = :id"),
            {"status": status.value, "fp": matched_fp, "id": watch_id},
        )
        await conn.execute(
            text("UPDATE service_areas SET active = FALSE WHERE name = :name"),
            {"name": _area_name(watch_id)},
        )
    log.info("return watch closed", extra={"extra_fields": {"watch_id": watch_id, "status": status}})


async def reopen_watch(watch_id: int) -> None:
    """Driver tapped 'ابحث عن غيرها' on a matched return alert — put the
    watch back to open so returns/service.py keeps searching.
    """
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE return_watches SET status = 'open', matched_fp = NULL WHERE id = :id AND status = 'matched'"),
            {"id": watch_id},
        )


async def expire_watches() -> int:
    """Sweep for watches past their `expires_at` — القسم 8.2: 'أو تنتهي
    المهلة المحددة'. Meant to be called periodically by ops/heartbeat.py or
    returns/service.py's own loop.
    """
    async with get_engine().begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    UPDATE return_watches SET status = 'expired'
                    WHERE status = 'open' AND expires_at < now()
                    RETURNING id
                    """
                )
            )
        ).all()
        for row in rows:
            await conn.execute(
                text("UPDATE service_areas SET active = FALSE WHERE name = :name"),
                {"name": _area_name(row.id)},
            )
    if rows:
        log.info("expired return watches", extra={"extra_fields": {"count": len(rows)}})
    return len(rows)


async def get_watch(watch_id: int) -> ReturnWatch | None:
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, driver_id, outbound_fp,
                           ST_Y(from_geom::geometry) AS from_lat, ST_X(from_geom::geometry) AS from_lon,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           available_at, expires_at, status, matched_fp, created_at
                    FROM return_watches WHERE id = :id
                    """
                ),
                {"id": watch_id},
            )
        ).mappings().first()
        if row is None:
            return None
        return ReturnWatch(
            id=row["id"],
            driver_id=row["driver_id"],
            outbound_fp=row["outbound_fp"],
            from_lat=row["from_lat"],
            from_lon=row["from_lon"],
            home_lat=row["home_lat"],
            home_lon=row["home_lon"],
            available_at=row["available_at"],
            expires_at=row["expires_at"],
            status=WatchStatus(row["status"]),
            matched_fp=row["matched_fp"],
            created_at=row["created_at"],
        )


async def list_open_watches() -> list[ReturnWatch]:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, driver_id, outbound_fp,
                           ST_Y(from_geom::geometry) AS from_lat, ST_X(from_geom::geometry) AS from_lon,
                           ST_Y(home_geom::geometry) AS home_lat, ST_X(home_geom::geometry) AS home_lon,
                           available_at, expires_at, status, matched_fp, created_at
                    FROM return_watches WHERE status = 'open'
                    """
                )
            )
        ).mappings().all()
        return [
            ReturnWatch(
                id=r["id"],
                driver_id=r["driver_id"],
                outbound_fp=r["outbound_fp"],
                from_lat=r["from_lat"],
                from_lon=r["from_lon"],
                home_lat=r["home_lat"],
                home_lon=r["home_lon"],
                available_at=r["available_at"],
                expires_at=r["expires_at"],
                status=WatchStatus(r["status"]),
                matched_fp=r["matched_fp"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
