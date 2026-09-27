"""Fake `OfferParser` that invents a handful of offers around Leverkusen
without touching a browser or any real platform. Lets the whole pipeline
(Collector -> Matching -> Dispatcher -> Return Finder) be exercised locally
before a real platform integration exists. Never use this in production —
set COLLECTOR_MODE=demo only for local runs/demos.

Offers stay listed for several poll cycles (like a real platform, where an
offer sits there until someone actually books it) instead of being replaced
every cycle — otherwise a job the collector just alerted a driver about
would already show as "gone" by the time the driver taps a button.
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

MAX_ACTIVE_OFFERS = 5
MIN_LIFESPAN_CYCLES = 6  # ~3-6 min at the default 30s poll interval
MAX_LIFESPAN_CYCLES = 12
SPAWN_CHANCE = 0.5


class DemoOfferParser(OfferParser):
    def __init__(self) -> None:
        self._active: dict[str, tuple[RawOffer, int]] = {}

    async def is_logged_in(self, page: "Page") -> bool:
        return True

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        return None

    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        for platform_id in list(self._active):
            offer, remaining = self._active[platform_id]
            remaining -= 1
            if remaining <= 0:
                del self._active[platform_id]
            else:
                self._active[platform_id] = (offer, remaining)

        if len(self._active) < MAX_ACTIVE_OFFERS and random.random() < SPAWN_CHANCE:
            pickup, dropoff = random.sample(_CITIES, 2)
            offer = RawOffer(
                platform_id=f"demo-{random.randint(100000, 999999)}",
                pickup_address=pickup[0],
                dropoff_address=dropoff[0],
                pickup_date=datetime.now(timezone.utc) + timedelta(hours=random.randint(1, 48)),
                price_eur=round(random.uniform(40, 350), 2),
                url=f"https://platform.example/jobs/{random.randint(10000, 99999)}",
            )
            self._active[offer.platform_id] = (offer, random.randint(MIN_LIFESPAN_CYCLES, MAX_LIFESPAN_CYCLES))

        return [offer for offer, _ in self._active.values()]
