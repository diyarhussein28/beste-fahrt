"""Fake `OfferParser` that invents a handful of offers around Leverkusen
without touching a browser or any real platform. Lets the whole pipeline
(Collector -> Matching -> Dispatcher -> Return Finder) be exercised locally
before a real platform integration exists. Never use this in production —
set COLLECTOR_MODE=demo only for local runs/demos.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from shared.models import RawOffer
from collector.parser import OfferParser

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

_CITIES = [
    ("Leverkusen-Opladen", 51.0679, 7.0132),
    ("Köln-Mülheim", 50.9686, 6.9918),
    ("Düsseldorf-Flingern", 51.2223, 6.8103),
    ("München-Pasing", 48.1489, 11.4599),
    ("Frankfurt-Bockenheim", 50.1225, 8.6435),
    ("Solingen-Ohligs", 51.1550, 7.0836),
]


class DemoOfferParser(OfferParser):
    async def is_logged_in(self, page: "Page") -> bool:
        return True

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        return None

    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        n = random.randint(0, 2)
        offers = []
        for _ in range(n):
            pickup, dropoff = random.sample(_CITIES, 2)
            offers.append(
                RawOffer(
                    platform_id=f"demo-{random.randint(100000, 999999)}",
                    pickup_address=pickup[0],
                    dropoff_address=dropoff[0],
                    pickup_date=datetime.now(timezone.utc) + timedelta(hours=random.randint(1, 48)),
                    price_eur=round(random.uniform(40, 350), 2),
                    url=f"https://platform.example/jobs/{random.randint(10000, 99999)}",
                )
            )
        return offers
