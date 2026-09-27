"""Makes the collector's browsing pattern look like an actual person
checking a job board, not a script polling on a fixed clock — القسم 4.3:
"الاستعلام كل بضع ثوانٍ... يبدو سلوكاً آلياً واضحاً... الإنسان لا يحدّث
الصفحة كل 3 ثوانٍ لـ 15 ساعة يومياً". None of this bypasses any
protection; it only avoids *looking* automated to a platform that
fingerprints request timing or interaction behavior — the doc is explicit
that this pattern, not read-only access itself, is what actually gets an
account flagged.
"""
from __future__ import annotations

import asyncio
import logging
import random

log = logging.getLogger("collector.humanize")

# A real desktop Chrome fingerprint. Playwright's headless Chromium is close
# to this by default in recent versions, but locale/timezone/viewport are
# left at generic defaults otherwise — worth pinning to something that
# matches where the fleet actually operates.
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

BROWSER_CONTEXT_DEFAULTS = {
    "viewport": {"width": 1920, "height": 1080},
    "user_agent": DESKTOP_USER_AGENT,
    "locale": "de-DE",
    "timezone_id": "Europe/Berlin",
}


def human_delay_seconds(base_seconds: float, jitter_seconds: float) -> float:
    """A person checking a job board doesn't recheck on a clock — mostly
    quick rechecks, sometimes a longer pause (busy with something else),
    rarely a real break (driving, a meal, a delivery in progress). Centered
    on the configured base interval so the *average* polling frequency
    still matches what was tuned in config.yaml — this only breaks up the
    perfectly uniform pattern a fixed base+jitter produces.
    """
    roll = random.random()
    if roll < 0.75:  # quick recheck — the common case, same shape as before
        return max(3.0, base_seconds + random.uniform(-jitter_seconds, jitter_seconds))
    if roll < 0.93:  # got briefly distracted
        return base_seconds * random.uniform(2.0, 4.0)
    return base_seconds * random.uniform(8.0, 20.0)  # a longer break


async def act_like_a_person(page) -> None:
    """A few small, cheap interactions a real visitor leaves behind that
    calling page.evaluate() the instant a page loads never does: a short
    pause to actually "read" it, a bit of scrolling, a small mouse move.
    Best-effort and silent on failure — a cosmetic interaction must never
    break the real polling cycle.
    """
    try:
        await asyncio.sleep(random.uniform(0.6, 2.2))
        await page.mouse.move(random.randint(200, 800), random.randint(150, 500))
        if random.random() < 0.6:
            await page.mouse.wheel(0, random.randint(200, 900))
            await asyncio.sleep(random.uniform(0.3, 1.0))
    except Exception as e:
        log.debug("act_like_a_person interaction skipped", extra={"extra_fields": {"error": str(e)}})
