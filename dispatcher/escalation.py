"""Escalation logic — القسم 7.2: أفضل سائق أولاً، ثم التالي عند الرفض أو
انتهاء المهلة، ثم ملخّص للمدير بعد استنفاد المحاولات. Runs inside the
dispatcher process; state lives in the `dispatches` table so a restart just
means an in-flight escalation is abandoned rather than corrupted.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import text

from shared.config import DispatchConfig
from shared.db import get_engine, log_dispatch, set_job_status
from shared.models import DispatchResponse, Job, JobStatus, RankedDriver

log = logging.getLogger("dispatcher.escalation")

SendToDriver = Callable[[RankedDriver, Job, int], Awaitable[None]]
SendToManager = Callable[[Job, int], Awaitable[None]]

_POLL_INTERVAL_S = 2.0


async def _wait_for_response(job_fp: str, driver_id: int, timeout_s: float) -> DispatchResponse | None:
    elapsed = 0.0
    async with get_engine().connect() as conn:
        while elapsed < timeout_s:
            row = (
                await conn.execute(
                    text(
                        "SELECT response FROM dispatches WHERE job_fp = :fp AND driver_id = :d "
                        "AND responded_at IS NOT NULL"
                    ),
                    {"fp": job_fp, "d": driver_id},
                )
            ).first()
            if row is not None:
                return DispatchResponse(row.response)
            await asyncio.sleep(_POLL_INTERVAL_S)
            elapsed += _POLL_INTERVAL_S
    return None


async def _job_still_open(job_fp: str) -> bool:
    async with get_engine().connect() as conn:
        row = (await conn.execute(text("SELECT status FROM jobs WHERE fp = :fp"), {"fp": job_fp})).first()
        return row is not None and row.status == JobStatus.OPEN.value


async def dispatch_sequential(
    job: Job,
    ranked: list[RankedDriver],
    config: DispatchConfig,
    send_to_driver: SendToDriver,
    send_to_manager: SendToManager,
) -> None:
    """أفضل سائق -> التالي عند الرفض/المهلة -> المدير بعد max_attempts."""
    for attempt, candidate in enumerate(ranked[: config.max_attempts], start=1):
        if not await _job_still_open(job.fp):
            return  # taken (accepted by someone, or gone from the platform) meanwhile

        driver_id = candidate.driver.id
        assert driver_id is not None
        await send_to_driver(candidate, job, attempt)
        await log_dispatch(job.fp, driver_id, rank=attempt, approach_km=candidate.approach_km)

        response = await _wait_for_response(job.fp, driver_id, config.response_timeout_seconds)
        if response == DispatchResponse.ACCEPT:
            await set_job_status(job.fp, JobStatus.DISPATCHED)
            log.info("job accepted", extra={"extra_fields": {"fp": job.fp, "driver_id": driver_id}})
            return
        # decline or timeout -> fall through to the next candidate
        log.info(
            "driver did not accept, escalating",
            extra={"extra_fields": {"fp": job.fp, "driver_id": driver_id, "response": response}},
        )

    if await _job_still_open(job.fp):
        await send_to_manager(job, min(len(ranked), config.max_attempts))


async def dispatch_broadcast(
    job: Job,
    ranked: list[RankedDriver],
    top_n: int,
    send_to_driver: SendToDriver,
) -> None:
    """للعروض عالية الربحية: يُرسل لأقرب top_n في آن واحد، وأول قبول يفوز —
    القسم 7.2. bot.py's accept handler enforces "first wins" atomically via
    the job's status transition.
    """
    for attempt, candidate in enumerate(ranked[:top_n], start=1):
        driver_id = candidate.driver.id
        assert driver_id is not None
        await send_to_driver(candidate, job, attempt)
        await log_dispatch(job.fp, driver_id, rank=attempt, approach_km=candidate.approach_km)
