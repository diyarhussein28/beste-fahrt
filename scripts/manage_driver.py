"""Small CLI for the operations the Admin Panel intentionally doesn't do
(it's read-only). Run inside the `admin` or `ops` container, or locally
against DATABASE_URL.

Usage:
    python -m scripts.manage_driver add "Ahmed K." --phone +49123456789
    python -m scripts.manage_driver link-telegram "Ahmed K." 123456789
    python -m scripts.manage_driver set-home "Ahmed K." 51.0459 7.0192 --city Leverkusen
    python -m scripts.manage_driver set-consent "Ahmed K." on
    python -m scripts.manage_driver set-license "Ahmed K." BE B
    python -m scripts.manage_driver deactivate "Ahmed K."
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from shared.db import get_engine


async def add(name: str, phone: str | None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("INSERT INTO drivers (name, phone, status, active) VALUES (:n, :p, 'off_duty', TRUE)"),
            {"n": name, "p": phone},
        )
    print(f"added driver '{name}'")


async def link_telegram(name: str, chat_id: int) -> None:
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text("UPDATE drivers SET telegram_chat_id = :c WHERE name = :n"), {"c": chat_id, "n": name}
        )
        if result.rowcount == 0:
            print(f"no driver named '{name}'")
        else:
            print(f"linked '{name}' to Telegram chat_id={chat_id}")


async def set_home(name: str, lat: float, lon: float, city: str | None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE drivers SET home_geom = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), home_city = :city
                WHERE name = :n
                """
            ),
            {"lat": lat, "lon": lon, "city": city, "n": name},
        )
    print(f"set home base for '{name}'")


async def set_consent(name: str, value: bool) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE drivers SET location_consent = :v WHERE name = :n"), {"v": value, "n": name})
    print(f"location_consent={value} for '{name}'")


async def set_license(name: str, classes: list[str]) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE drivers SET license_classes = :c WHERE name = :n"), {"c": classes, "n": name}
        )
    print(f"license_classes={classes} for '{name}'")


async def deactivate(name: str) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE drivers SET active = FALSE, status = 'off_duty' WHERE name = :n"), {"n": name})
    print(f"deactivated '{name}'")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--phone", default=None)

    p = sub.add_parser("link-telegram")
    p.add_argument("name")
    p.add_argument("chat_id", type=int)

    p = sub.add_parser("set-home")
    p.add_argument("name")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("--city", default=None)

    p = sub.add_parser("set-consent")
    p.add_argument("name")
    p.add_argument("value", choices=["on", "off"])

    p = sub.add_parser("set-license")
    p.add_argument("name")
    p.add_argument("classes", nargs="+")

    p = sub.add_parser("deactivate")
    p.add_argument("name")

    args = parser.parse_args()

    if args.command == "add":
        asyncio.run(add(args.name, args.phone))
    elif args.command == "link-telegram":
        asyncio.run(link_telegram(args.name, args.chat_id))
    elif args.command == "set-home":
        asyncio.run(set_home(args.name, args.lat, args.lon, args.city))
    elif args.command == "set-consent":
        asyncio.run(set_consent(args.name, args.value == "on"))
    elif args.command == "set-license":
        asyncio.run(set_license(args.name, args.classes))
    elif args.command == "deactivate":
        asyncio.run(deactivate(args.name))


if __name__ == "__main__":
    main()
