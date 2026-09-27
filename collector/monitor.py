"""Collector polling loop — القسم 4.6.

Read-only: never books, accepts, or declines anything on any platform. Each
platform configured in config.yaml's `platforms` list runs its own
concurrent loop (own browser context, session, login state, backoff) inside
one shared Playwright browser process; each cycle fetches that platform's
offer list, geocodes and filters by service area, deduplicates by
fingerprint, and publishes a `job.created` event per new offer for the
Matching Engine to pick up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from shared.config import get_config
from shared.db import mark_jobs_gone, upsert_job
from shared.events import publish
from shared.logging_config import configure_logging
from shared.models import Job, JobStatus, RawOffer, job_fingerprint
from shared import db as shared_db
from collector import geo, humanize, platforms, session
from collector.exceptions import LoginBlocked, PlatformChanged, RateLimited
from collector.parser import OfferParser

log = logging.getLogger("collector")

EMPTY_RESULT_ALERT_THRESHOLD = 20  # consecutive empty polls before suspecting a markup change


def build_parsers() -> list[OfferParser]:
    mode = os.environ.get("COLLECTOR_MODE", "demo").lower()
    if mode != "live":
        from collector.demo_parser import DemoOfferParser

        log.warning("COLLECTOR_MODE=demo — generating synthetic offers, not touching any real platform")
        return [DemoOfferParser()]

    parsers: list[OfferParser] = []
    for entry in get_config().platforms:
        if not entry.enabled:
            continue
        parser = platforms.build_parser(entry)
        if parser is None:
            log.error("no parser registered for platform", extra={"extra_fields": {"platform": entry.name, "key": entry.parser_key()}})
            continue
        parsers.append(parser)

    if not parsers:
        log.warning("no enabled platforms configured under COLLECTOR_MODE=live — nothing to poll")
    return parsers


def _within_active_hours(cfg_polling) -> bool:
    start, end = cfg_polling.active_window()
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # window spans midnight


def _seconds_until_active(cfg_polling) -> float:
    start, _ = cfg_polling.active_window()
    now = datetime.now()
    target = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if target <= now:
        target = target.replace(day=target.day + 1)
    return (target - now).total_seconds()


async def alert_manager(message: str, platform: str | None = None) -> None:
    log.warning("manager alert", extra={"extra_fields": {"message": message, "platform": platform}})
    text = f"[{platform}] {message}" if platform else message
    await publish("alert.manager", {"source": "collector", "message": text})


def normalize(raw: RawOffer, pickup_geo: tuple[float, float] | None, dropoff_geo: tuple[float, float] | None) -> Job:
    from matcher.routing import haversine_km

    route_km = None
    if pickup_geo and dropoff_geo:
        route_km = haversine_km(pickup_geo[0], pickup_geo[1], dropoff_geo[0], dropoff_geo[1])

    return Job(
        fp=job_fingerprint(raw),
        platform=raw.platform,
        platform_id=raw.platform_id,
        pickup_addr=raw.pickup_address,
        dropoff_addr=raw.dropoff_address,
        pickup_lat=pickup_geo[0] if pickup_geo else None,
        pickup_lon=pickup_geo[1] if pickup_geo else None,
        dropoff_lat=dropoff_geo[0] if dropoff_geo else None,
        dropoff_lon=dropoff_geo[1] if dropoff_geo else None,
        price_eur=raw.price_eur,
        route_km=route_km,
        pickup_date=raw.pickup_date,
        url=raw.url,
        status=JobStatus.OPEN,
        required_license=raw.required_license,
    )


async def process_cycle(parser: OfferParser, page, empty_streak: int) -> int:
    raw_offers = await parser.fetch_offers(page)
    seen_fps: set[str] = set()

    for raw in raw_offers:
        pickup_geo = await geo.geocode(raw.pickup_address)
        if pickup_geo is None:
            continue
        if not await geo.in_service_area(*pickup_geo):
            continue

        dropoff_geo = await geo.geocode(raw.dropoff_address)
        job = normalize(raw, pickup_geo, dropoff_geo)
        seen_fps.add(job.fp)

        if not await shared_db.is_new_job(job.fp):
            continue
        await upsert_job(job)
        await publish("job.created", {"fp": job.fp})
        log.info("new job discovered", extra={"extra_fields": {"fp": job.fp, "platform": parser.name, "pickup": job.pickup_addr}})

    gone = await mark_jobs_gone(seen_fps, since=datetime.now(timezone.utc), platform=parser.name)
    if gone:
        log.info("jobs marked gone", extra={"extra_fields": {"platform": parser.name, "count": gone}})

    empty_streak = empty_streak + 1 if not raw_offers else 0
    if empty_streak == EMPTY_RESULT_ALERT_THRESHOLD:
        await alert_manager(
            f"Keine Angebote in den letzten {empty_streak} Durchläufen gefunden — möglicherweise hat sich das Plattform-Design geändert",
            platform=parser.name,
        )
    return empty_streak


async def platform_loop(browser, parser: OfferParser) -> None:
    cfg = get_config()
    backoff = float(cfg.polling.base_seconds)
    empty_streak = 0
    heartbeat_name = f"collector:{parser.name}"

    ctx = await browser.new_context(
        storage_state=session.load_state_dict(parser.name), **humanize.BROWSER_CONTEXT_DEFAULTS
    )
    page = await ctx.new_page()

    while True:
        try:
            if not _within_active_hours(cfg.polling):
                wait_s = _seconds_until_active(cfg.polling)
                log.info("outside active hours, sleeping", extra={"extra_fields": {"platform": parser.name, "seconds": wait_s}})
                await asyncio.sleep(min(wait_s, 3600))
                continue

            if not await parser.is_logged_in(page):
                await parser.login(page, ctx)
                await session.save_state(ctx, parser.name)

            await humanize.act_like_a_person(page)
            empty_streak = await process_cycle(parser, page, empty_streak)
            await shared_db.beat(heartbeat_name)
            backoff = float(cfg.polling.base_seconds)

        except LoginBlocked as e:
            # Never auto-retry a CAPTCHA/2FA block: retrying every few
            # minutes while a real challenge is active just means hitting
            # the login page again and again, which looks exactly like the
            # automated behavior these checks exist to catch. القسم 4.2 is
            # explicit that this needs a human to actually resolve it, so
            # this platform's loop stops here — restarting the collector
            # (a deliberate action, once the manager has checked/logged in
            # manually) is what resumes it, not a timer.
            await alert_manager(
                f"Anmeldung gestoppt: {e} — Polling für diese Plattform angehalten, bis der Collector manuell neu gestartet wird",
                platform=parser.name,
            )
            log.error("stopping platform_loop after LoginBlocked — will not auto-retry", extra={"extra_fields": {"platform": parser.name}})
            return
        except RateLimited:
            await alert_manager("Von der Plattform ratenbegrenzt — vorübergehend pausiert", platform=parser.name)
            await asyncio.sleep(cfg.polling.rate_limit_pause_seconds)
        except PlatformChanged as e:
            await alert_manager(f"Mögliche Änderung im Plattform-Design: {e}", platform=parser.name)
            backoff = min(backoff * 2, cfg.polling.max_backoff_seconds)
        except Exception:
            log.exception("unexpected error in collector cycle", extra={"extra_fields": {"platform": parser.name}})
            backoff = min(backoff * 2, cfg.polling.max_backoff_seconds)

        if backoff <= cfg.polling.base_seconds:
            # Normal cycle, nothing went wrong: vary the wait like a person
            # actually checking a job board would, not a fixed clock tick.
            sleep_s = humanize.human_delay_seconds(cfg.polling.base_seconds, cfg.polling.jitter_seconds)
        else:
            # Recovering from an error — plain jitter around the escalated
            # backoff, since this is about being gentle with the platform
            # after a problem, not about looking human.
            jitter = random.uniform(-cfg.polling.jitter_seconds, cfg.polling.jitter_seconds)
            sleep_s = max(1.0, backoff + jitter)
        await asyncio.sleep(sleep_s)


async def run() -> None:
    configure_logging()
    parsers = build_parsers()
    if not parsers:
        raise RuntimeError("no platforms to poll — check config.yaml's `platforms` list and COLLECTOR_MODE")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await asyncio.gather(*(platform_loop(browser, parser) for parser in parsers))


if __name__ == "__main__":
    asyncio.run(run())
