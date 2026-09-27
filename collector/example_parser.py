"""Reference `OfferParser` driven entirely by CSS selectors + credentials
from config.yaml/.env — a starting point for a real platform, not a finished
integration (every platform's login flow and markup differs). Adapt the
selectors in config.yaml first; only touch this file if the login flow or
offer-card layout doesn't fit the generic shape assumed here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from shared.config import get_secrets, get_config
from shared.models import RawOffer
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
        cfg = get_config()
        await page.goto(cfg.platform.offers_url, wait_until="domcontentloaded")
        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found while checking session validity")
        selectors = cfg.platform.selectors
        try:
            await page.wait_for_selector(selectors.offer_card, timeout=3000)
            return True
        except Exception:
            return False

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        secrets = get_secrets()
        cfg = get_config()
        if not secrets.platform_username or not secrets.platform_password:
            raise RuntimeError("PLATFORM_USERNAME / PLATFORM_PASSWORD not set")

        await page.goto(cfg.platform.login_url or cfg.platform.offers_url, wait_until="domcontentloaded")
        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found on login page")

        # Generic form fill — adjust field selectors for the real platform.
        await page.fill('input[type="email"], input[name="username"]', secrets.platform_username)
        await page.fill('input[type="password"]', secrets.platform_password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found after submitting credentials")

        await ctx.storage_state(path=secrets.state_file)
        log.info("login successful, session state saved")

    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        cfg = get_config()
        selectors = cfg.platform.selectors
        await page.goto(cfg.platform.offers_url, wait_until="networkidle")
        cards = await page.query_selector_all(selectors.offer_card)

        offers: list[RawOffer] = []
        for card in cards:
            offers.append(await self._parse_card(card, selectors))
        return offers

    @staticmethod
    async def _parse_card(card, selectors) -> RawOffer:
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
