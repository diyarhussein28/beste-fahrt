"""Seeds 10 demo drivers around Leverkusen so the pipeline has someone to
dispatch to before real driver onboarding happens. Safe to re-run — skips
any name that already exists. NOT for production data: telegram_chat_id is
left NULL here, so alerts won't actually be delivered until you attach a
real chat id (e.g. via an UPDATE once the driver has messaged the bot once).

Usage: python -m scripts.seed_demo_drivers
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from shared.db import get_engine

DEMO_DRIVERS = [
    # name, home_city, (home_lat, home_lon)
    ("Ahmed K.", "Leverkusen", (51.0459, 7.0192)),
    ("Bilal T.", "Köln", (50.9375, 6.9603)),
    ("Cem Y.", "Solingen", (51.1652, 7.0671)),
    ("Deniz A.", "Leverkusen-Opladen", (51.0679, 7.0132)),
    ("Emre S.", "Düsseldorf", (51.2277, 6.7735)),
    ("Farid N.", "Bergisch Gladbach", (50.9925, 7.1281)),
    ("Giuseppe M.", "Leverkusen-Schlebusch", (51.0500, 7.0700)),
    ("Hakan D.", "Remscheid", (51.1789, 7.1894)),
    ("Ismail B.", "Leichlingen", (51.1050, 7.0200)),
    ("Jonas W.", "Monheim am Rhein", (51.0925, 6.8975)),
]


async def run() -> None:
    async with get_engine().begin() as conn:
        for name, home_city, (lat, lon) in DEMO_DRIVERS:
            exists = (await conn.execute(text("SELECT 1 FROM drivers WHERE name = :n"), {"n": name})).first()
            if exists:
                continue
            await conn.execute(
                text(
                    """
                    INSERT INTO drivers (name, status, active, location_consent, home_city, home_geom)
                    VALUES (:name, 'available', TRUE, TRUE, :home_city,
                            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                    """
                ),
                {"name": name, "home_city": home_city, "lat": lat, "lon": lon},
            )
            # Seed an initial location at the driver's home base so the
            # matcher has something to rank against immediately.
            row = (await conn.execute(text("SELECT id FROM drivers WHERE name = :n"), {"n": name})).first()
            await conn.execute(
                text(
                    """
                    INSERT INTO driver_locations (driver_id, geom, source)
                    VALUES (:driver_id, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 'manual')
                    """
                ),
                {"driver_id": row.id, "lat": lat, "lon": lon},
            )
    print(f"seeded {len(DEMO_DRIVERS)} demo drivers (skipping any that already existed)")


if __name__ == "__main__":
    asyncio.run(run())
