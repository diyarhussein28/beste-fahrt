"""OfferParser for movacarpro.com — an Angular/Ionic app (web-component
markup like <ion-input>, <ion-button>, class names that look hashed/
generated), so this targets the login form by its accessible labels/roles
(confirmed by inspecting the public, unauthenticated login page directly —
never by logging in) and the ride list by its visible text patterns rather
than guessed CSS classes, since Ionic's own classes aren't stable across
builds.

This is a first pass, not a validated integration: the ride-list markup was
only seen in screenshots (login requires real credentials, which are never
entered by anyone but the deployed collector itself — القسم 4.2/11.2), so
the text-pattern heuristics in `_parse_card_text` need confirming against
the live DOM once deployed. If `fetch_offers` starts returning nothing,
that's collector/monitor.py's PlatformChanged/empty-streak alert doing its
job — القسم 9.3 — not a silent failure.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from shared.config import get_platform_credentials
from shared.models import RawOffer
from collector import session
from collector.exceptions import LoginBlocked
from collector.parser import OfferParser

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

log = logging.getLogger("collector.movacarpro")

LOGIN_URL = "https://movacarpro.com/login"
CAPTCHA_MARKERS = ["captcha", "recaptcha", "hcaptcha", "cf-challenge", "are you human"]
TWO_FA_MARKERS = ["two-factor", "2fa", "verification code", "one-time code"]

# Visible German tags on a ride card that imply a vehicle/driver requirement
# beyond a plain transport — mapped onto Job.required_license so the
# existing license-fit scoring (matcher/engine.py) applies unchanged.
REQUIREMENT_TAGS = {
    "Zusatzqualifikation": "zusatzqualifikation",
    "Rotes Nummernschild erforderlich": "rotes_nummernschild",
    "Transport auf Anhänger": "anhaenger",
}

_TIER_RE = re.compile(r"\b(Bronze|Silber|Gold)\b")
# The optional German weekday abbreviation is non-capturing but still part
# of the match, so `.start()` lands before it — otherwise it leaks into the
# route segment ("Saal a. d. Donau Fr") since it sits right before the date.
_DATE_RE = re.compile(r"(?:Mo|Di|Mi|Do|Fr|Sa|So)?\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})")
_PRICE_RE = re.compile(r"([\d.]+,\d{2})\s*€")

# Extracts every ride-card-shaped block of text from the page: any leaf-ish
# element whose own text contains a price, walked up to the nearest ancestor
# that also mentions the route arrow — robust to Angular's generated class
# names, which change across builds/deploys. `opacity` flags a card the UI
# has grayed out (e.g. a Silber/Gold ride a Bronze account can't book), so
# it can be excluded rather than dangling a driver an offer they can't take.
_CARD_EXTRACTION_JS = r"""
() => {
  const priceRe = /\d+[.,]\d{2}\s*€/;
  const seen = new Set();
  const cards = [];
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (el.children.length > 0) continue;
    if (!priceRe.test(el.textContent || '')) continue;
    let node = el;
    let card = null;
    for (let i = 0; i < 8 && node; i++) {
      if ((node.textContent || '').includes('→')) { card = node; break; }
      node = node.parentElement;
    }
    if (!card || seen.has(card)) continue;
    seen.add(card);
    const opacity = parseFloat(getComputedStyle(card).opacity || '1');
    cards.push([card.textContent.replace(/\s+/g, ' ').trim(), opacity < 0.99]);
  }
  return cards;
}
"""


class MovacarProParser(OfferParser):
    async def is_logged_in(self, page: "Page") -> bool:
        await page.goto(self.entry.offers_url or LOGIN_URL, wait_until="domcontentloaded")
        return "/login" not in page.url

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        username, password = get_platform_credentials(self.name)
        if not username or not password:
            raise RuntimeError(f"PLATFORM_{self.name.upper()}_USERNAME/_PASSWORD not set")

        await page.goto(self.entry.login_url or LOGIN_URL, wait_until="domcontentloaded")
        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found on movacarpro login page")

        await page.fill('input[type="email"]', username)
        await page.fill('input[type="password"]', password)

        agree = page.locator("ion-checkbox#agree-to-conditions")
        if await agree.count() and (await agree.get_attribute("aria-checked")) != "true":
            await agree.click()

        await page.get_by_role("button", name="Einloggen").click()
        await page.wait_for_load_state("networkidle")

        if "/login" in page.url:
            raise LoginBlocked("still on the login page after submitting — wrong credentials, or an unhandled 2FA/verification step")

        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found after submitting credentials")

        await session.save_state(ctx, self.name)
        log.info("movacarpro login successful, session state saved")

    async def fetch_offers(self, page: "Page") -> list[RawOffer]:
        try:
            await page.get_by_text("Alle Fahrten", exact=True).click(timeout=5000)
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass  # already on the right page, or the nav text changed — parse whatever is on screen

        raw_cards = await page.evaluate(_CARD_EXTRACTION_JS)
        offers: list[RawOffer] = []
        for text, locked in raw_cards:
            if locked:
                continue  # e.g. a Silber/Gold ride this account's tier can't book
            offer = self._parse_card_text(text)
            if offer is not None:
                offers.append(offer)
        return offers

    def _parse_card_text(self, text: str) -> RawOffer | None:
        price_matches = _PRICE_RE.findall(text)
        if not price_matches:
            return None
        price_eur = float(price_matches[-1].replace(".", "").replace(",", "."))

        tier_match = _TIER_RE.search(text)
        route_start = tier_match.end() if tier_match else 0

        date_match = _DATE_RE.search(text)
        route_end = date_match.start() if date_match else len(text)

        route_segment = text[route_start:route_end]
        parts = [p.strip(" .") for p in route_segment.split("→")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return None
        pickup_address, dropoff_address = parts[0], parts[1]

        pickup_date = None
        if date_match:
            day, month, year, hour, minute = date_match.groups()
            try:
                pickup_date = datetime(int(year), int(month), int(day), int(hour), int(minute))
            except ValueError:
                pickup_date = None

        required_license = next((tag for label, tag in REQUIREMENT_TAGS.items() if label in text), None)

        return RawOffer(
            platform=self.name,
            pickup_address=pickup_address,
            dropoff_address=dropoff_address,
            pickup_date=pickup_date,
            price_eur=price_eur,
            required_license=required_license,
        )

    @staticmethod
    def _looks_blocked(body: str) -> bool:
        return any(m in body for m in CAPTCHA_MARKERS + TWO_FA_MARKERS)
