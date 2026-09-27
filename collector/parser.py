"""Pluggable platform interface — القسم 4.1 و4.6.

The Collector doesn't know anything about a specific fleet platform. To
support a real one, implement `OfferParser` for it (selectors, login flow,
CAPTCHA/2FA detection) and point `collector/monitor.py` at your subclass.
`collector/example_parser.py` has a config-driven reference implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from shared.models import RawOffer

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


class OfferParser(ABC):
    """One implementation per fleet platform."""

    @abstractmethod
    async def is_logged_in(self, page: "Page") -> bool:
        """Cheap check (no navigation) for whether the saved session is still valid."""

    @abstractmethod
    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        """Perform the login flow.

        Must raise `collector.exceptions.LoginBlocked` the moment a CAPTCHA
        or 2FA challenge appears — never attempt to solve or bypass it
        (القسم 4.2 و11.2).
        """

    @abstractmethod
    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        """Navigate to the offers list and return every currently visible offer."""
