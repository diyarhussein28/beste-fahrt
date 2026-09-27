"""Reference `OfferParser` driven entirely by CSS selectors (from this
platform's entry in config.yaml) + credentials (from .env, per-platform —
see shared.config.get_platform_credentials). A starting point for a real
platform, not a finished integration — every platform's login flow and
markup differs. Adapt the selectors in config.yaml first; only touch this
file if the login flow or offer-card layout doesn't fit the generic shape
assumed here. `collector/movacarpro_parser.py` is a fully custom example
for a platform whose structure didn't fit this generic shape at all.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from shared.config import get_platform_credentials
from shared.models import RawOffer
from collector import session
from collector.exceptions import LoginBlocked
from collector.parser import OfferParser

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

log = logging.getLogger("collector.parser")

# Substrings commonly present in CAPTCHA/2FA challenge pages. Extend this
# list for the target platform rather than trying to solve the challenge.
CAPTCHA_MARKERS = ["captcha", "recaptcha", "hcaptcha", "cf-challenge", "are you human"]
TWO_FA_MARKERS = ["two-factor", "2fa", "verification code", "one-time code"]


class ConfigDrivenParser(OfferParser):
    async def is_logged_in(self, page: "Page") -> bool:
        await page.goto(self.entry.offers_url, wait_until="domcontentloaded")
        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found while checking session validity")
        selectors = self.entry.selectors
        try:
            await page.wait_for_selector(selectors.offer_card, timeout=3000)
            return True
        except Exception:
            return False

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        username, password = get_platform_credentials(self.name)
        if not username or not password:
            raise RuntimeError(f"PLATFORM_{self.name.upper()}_USERNAME/_PASSWORD not set")

        await page.goto(self.entry.login_url or self.entry.offers_url, wait_until="domcontentloaded")
        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found on login page")

        # Generic form fill — adjust field selectors for the real platform.
        await page.fill('input[type="email"], input[name="username"]', username)
        await page.fill('input[type="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found after submitting credentials")

        await session.save_state(ctx, self.name)
        log.info("login successful, session state saved", extra={"extra_fields": {"platform": self.name}})

    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        selectors = self.entry.selectors
        await page.goto(self.entry.offers_url, wait_until="networkidle")
        cards = await page.query_selector_all(selectors.offer_card)

        offers: list[RawOffer] = []
        for card in cards:
            offers.append(await self._parse_card(card, selectors))
        return offers

    async def _parse_card(self, card, selectors) -> RawOffer:
        async def text_of(sel: str) -> str | None:
            if not sel:
                return None
            el = await card.query_selector(sel)
            return (await el.inner_text()).strip() if el else None

        async def attr_of(sel: str, attr: str) -> str | None:
            if not sel:
                return None
            el = await card.query_selector(sel)
            return await el.get_attribute(attr) if el else None

        price_text = await text_of(selectors.price)
        price_eur = _parse_price(price_text) if price_text else None
        date_text = await text_of(selectors.pickup_date)

        return RawOffer(
            platform=self.name,
            platform_id=await text_of(selectors.offer_id),
            pickup_address=await text_of(selectors.pickup_address) or "",
            dropoff_address=await text_of(selectors.dropoff_address) or "",
            pickup_date=_parse_date(date_text) if date_text else None,
            price_eur=price_eur,
            url=await attr_of(selectors.offer_url, "href"),
        )

    @staticmethod
    def _looks_blocked(body: str) -> bool:
        return any(m in body for m in CAPTCHA_MARKERS + TWO_FA_MARKERS)


def _parse_price(text: str) -> float | None:
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ",.")
    cleaned = cleaned.replace(".", "").replace(",", ".") if "," in cleaned else cleaned
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(text: str) -> datetime | None:
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None
