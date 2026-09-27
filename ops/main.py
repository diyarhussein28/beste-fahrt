"""Entrypoint for the `ops` container: heartbeat watchdog + weekly report,
both lightweight enough to share one process (القسم 9.3, 15).
"""
from __future__ import annotations

import asyncio

from shared.logging_config import configure_logging

from ops import heartbeat, weekly_report


async def run() -> None:
    configure_logging()
    await asyncio.gather(heartbeat.run(), weekly_report.run())


if __name__ == "__main__":
    asyncio.run(run())
