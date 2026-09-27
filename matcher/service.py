"""Matching Engine service loop — consumes `job.created` (published by the
Collector), ranks available drivers, and publishes a `dispatch.ready` event
for the Dispatcher. Also the integration point for the Return Trip Finder —
القسم 8.11: 'استدعاء Return Finder قبل إرسال أي عرض ذهاب بعيد'.
"""
from __future__ import annotations

import asyncio
import logging

from shared.config import get_config
from shared.db import get_job, ranked_drivers_for_job
from shared.events import ack, consume, publish
from shared.logging_config import configure_logging
from shared.models import RankedDriver

from matcher.engine import is_profitable, rank_candidates, should_broadcast

log = logging.getLogger("matcher")

STREAM = "job.created"
GROUP = "matcher"
CONSUMER = "matcher-1"

# NOTE: `ranked_drivers_for_job` already computes approach_km via PostGIS
# ST_Distance (straight-line, on the geography type — accurate enough for
# ranking). Refining the closest few with real OSRM road distance
# (matcher/routing.refine_top_candidates) is a phase-5 optimization per
# القسم 12 — wire it in once OSRM is actually deployed, since it needs each
# driver's raw lat/lon rather than just their precomputed approach_km.


async def rank_job(job_fp: str) -> tuple[object, list[RankedDriver]] | tuple[None, list]:
    job = await get_job(job_fp)
    if job is None:
        return None, []

    config = get_config()
    candidates = await ranked_drivers_for_job(job_fp, limit=10)
    ranked = rank_candidates(candidates, job, config.matching)
    return job, ranked


async def handle_job(job_fp: str) -> None:
    config = get_config()
    job, ranked = await rank_job(job_fp)
    if job is None or not ranked:
        log.info("no available driver for job", extra={"extra_fields": {"fp": job_fp}})
        return

    if not is_profitable(job, ranked[0].approach_km, config.matching):
        log.info("job below profitability threshold, skipping dispatch", extra={"extra_fields": {"fp": job_fp}})
        return

    mode = "broadcast" if should_broadcast(
        job, ranked[0].approach_km, config.matching, config.dispatch.broadcast_if_eur_per_km_above
    ) else "sequential"

    await publish("dispatch.ready", {"fp": job_fp, "mode": mode})
    log.info("job ranked and queued for dispatch", extra={"extra_fields": {"fp": job_fp, "mode": mode, "candidates": len(ranked)}})


async def run() -> None:
    configure_logging()
    async for message_id, payload in consume(STREAM, GROUP, CONSUMER):
        try:
            await handle_job(payload["fp"])
        except Exception:
            log.exception("failed to handle job.created event", extra={"extra_fields": {"payload": payload}})
        finally:
            await ack(STREAM, GROUP, message_id)


if __name__ == "__main__":
    asyncio.run(run())
