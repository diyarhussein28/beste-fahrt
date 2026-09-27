"""Return Trip Finder service loop — القسم 8.2: 'وحدة القراءة تلتقط عرضاً
جديداً، يُفحص تلقائياً مقابل قائمة طلبات العودة المفتوحة'. Also periodically
expires watches whose deadline has passed (القسم 8.2, third bullet).
"""
from __future__ import annotations

import asyncio
import logging

from shared.config import get_config
from shared.db import get_job
from shared.events import ack, consume, publish
from shared.logging_config import configure_logging
from shared.models import WatchStatus

from returns.finder import check_job_against_watch
from returns.watches import close_watch, expire_watches, list_open_watches

log = logging.getLogger("returns")

STREAM = "job.created"
GROUP = "returns"
CONSUMER = "returns-1"
EXPIRE_SWEEP_INTERVAL_S = 300


async def check_job_against_open_watches(job_fp: str) -> None:
    job = await get_job(job_fp)
    if job is None:
        return

    config = get_config().return_trip
    if not config.enabled:
        return

    watches = await list_open_watches()
    for watch in watches:
        candidate = check_job_against_watch(job, watch, config)
        if candidate is None:
            continue

        await publish(
            "return.matched",
            {"watch_id": watch.id, "fp": job.fp, "category": candidate.category},
        )
        await close_watch(watch.id, WatchStatus.MATCHED, matched_fp=job.fp)
        log.info(
            "return match found for open watch",
            extra={"extra_fields": {"watch_id": watch.id, "driver_id": watch.driver_id, "fp": job.fp}},
        )


async def periodic_expiry_sweep() -> None:
    while True:
        try:
            await expire_watches()
        except Exception:
            log.exception("failed to expire return watches")
        await asyncio.sleep(EXPIRE_SWEEP_INTERVAL_S)


async def consume_new_jobs() -> None:
    async for message_id, payload in consume(STREAM, GROUP, CONSUMER):
        try:
            await check_job_against_open_watches(payload["fp"])
        except Exception:
            log.exception("failed to check job against return watches", extra={"extra_fields": {"payload": payload}})
        finally:
            await ack(STREAM, GROUP, message_id)


async def run() -> None:
    configure_logging()
    await asyncio.gather(consume_new_jobs(), periodic_expiry_sweep())


if __name__ == "__main__":
    asyncio.run(run())
