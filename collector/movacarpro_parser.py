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

ROOT_URL = "https://movacarpro.com/"
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
# element whose own text contains a price, walked up until an ancestor
# mentions BOTH the route arrow and a date (confirmed against the live DOM:
# the nearest ancestor with just the arrow stops one level short, missing
# the date row entirely) — robust to Angular's generated class names, which
# change across builds/deploys. `opacity` flags a card the UI has grayed out
# (e.g. a Silber/Gold ride a Bronze account can't book), so it can be
# excluded rather than dangling a driver an offer they can't take.
_CARD_EXTRACTION_JS = r"""
() => {
  const priceRe = /\d+[.,]\d{2}\s*€/;
  const dateRe = /\d{2}\.\d{2}\.\d{4}/;
  const seen = new Set();
  const cards = [];
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (el.children.length > 0) continue;
    if (!priceRe.test(el.textContent || '')) continue;
    let node = el;
    let card = null;
    for (let i = 0; i < 14 && node; i++) {
      const t = node.textContent || '';
      if (t.includes('→') && dateRe.test(t)) { card = node; break; }
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
        # If we're already sitting on an authenticated movacarpro page from
        # a previous cycle, trust it rather than reloading: an Angular SPA
        # can carry session state that isn't fully captured by cookies (some
        # of it may live only in memory or sessionStorage), so a full reload
        # can drop a session that was otherwise still perfectly valid.
        if "movacarpro.com" in page.url and "/login" not in page.url:
            return True
        await page.goto(self.entry.offers_url or ROOT_URL, wait_until="domcontentloaded")
        # `domcontentloaded` fires before Angular's client-side auth guard has
        # had a chance to run and redirect an unauthenticated visitor to
        # /login — reading page.url immediately can catch it mid-flight and
        # wrongly conclude "logged in".
        await page.wait_for_timeout(1500)
        return "/login" not in page.url

    async def login(self, page: "Page", ctx: "BrowserContext") -> None:
        username, password = get_platform_credentials(self.name)
        if not username or not password:
            raise RuntimeError(f"PLATFORM_{self.name.upper()}_USERNAME/_PASSWORD not set")

        await page.goto(self.entry.login_url or LOGIN_URL, wait_until="domcontentloaded")
        # `domcontentloaded` fires before Angular finishes bootstrapping and
        # rendering the real form — checking page content immediately can
        # catch the pre-render shell (whatever transient markup/scripts it
        # contains) and false-positive as a CAPTCHA. Wait for the actual
        # email field instead of a fixed delay.
        try:
            await page.wait_for_selector('input[type="email"]', timeout=10000)
        except Exception:
            body = (await page.content()).lower()
            if self._looks_blocked(body):
                raise LoginBlocked("CAPTCHA/2FA marker found on movacarpro login page")
            raise LoginBlocked("login form did not appear (no email field) — possible layout change or challenge")

        body = (await page.content()).lower()
        if self._looks_blocked(body):
            raise LoginBlocked("CAPTCHA/2FA marker found on movacarpro login page")

        await self._dismiss_cookie_banner(page)

        await page.fill('input[type="email"]', username)
        await page.fill('input[type="password"]', password)

        agree = page.locator("ion-checkbox#agree-to-conditions")
        if await agree.count() and (await agree.get_attribute("aria-checked")) != "true":
            await agree.click()

        await page.get_by_role("button", name="Einloggen").click()
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # a live dashboard poller may never go fully idle — not fatal
        await page.wait_for_timeout(1500)  # let a client-side redirect (success or bounce-back) settle

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
            # Not networkidle: a dashboard with a live notification/badge
            # poller may never go fully idle, which would otherwise stall
            # every single cycle for the full default timeout.
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass  # already on the right page, or the nav text changed — parse whatever is on screen

        if "/login" in page.url:
            # The in-memory/session state we thought was valid wasn't —
            # report zero offers this cycle; is_logged_in() will see /login
            # on the next cycle and log back in properly.
            log.warning("ended up back on the login page while fetching offers")
            return []

        raw_cards = await page.evaluate(_CARD_EXTRACTION_JS)
        offers: list[RawOffer] = []
        for text, locked in raw_cards:
            log.debug("movacarpro card text", extra={"extra_fields": {"locked": locked, "text": text[:400]}})
            if locked:
                continue  # e.g. a Silber/Gold ride this account's tier can't book
            offer = self._parse_card_text(text)
            if offer is not None:
                offers.append(offer)
        return offers

    def _parse_card_text(self, text: str) -> RawOffer | None:
        price_match = _PRICE_RE.search(text)
        if not price_match:
            return None
        price_eur = float(price_match.group(1).replace(".", "").replace(",", "."))

        # Confirmed against the live DOM: cards read "[Bronze] 56,00 € Weiden
        # → Saal a. d. Donau [date...]" — the price sits between the tier
        # badge and the route, not after it as the visual (right-aligned)
        # layout would suggest.
        tier_match = _TIER_RE.search(text)
        route_start = max(tier_match.end() if tier_match else 0, price_match.end())

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

    @staticmethod
    async def _dismiss_cookie_banner(page: "Page") -> None:
        """The Cookiefirst consent dialog otherwise sits on top of the login
        form and intercepts every click. Denies non-essential cookies rather
        than accepting, same default as a human clicking through it — this
        is about the collector's own browsing, not anything shown to a
        fleet driver.
        """
        try:
            await page.get_by_role("button", name="Deny all cookies").click(timeout=5000)
        except Exception:
            pass  # banner didn't appear this time (e.g. choice already persisted)
