"""Heartbeat monitor — القسم 9.3: 'إن غابت [نبضة أي مكوّن] 5 دقائق يُرسل
تنبيه للمدير على Telegram'. Each of collector/matcher/returns/dispatcher
writes its own heartbeat (shared.db.beat); this watches for gaps.
"""
from __future__ import annotations

import asyncio
import logging

from shared.config import get_config
from shared.db import stale_heartbeats
from shared.events import publish
from shared.logging_config import configure_logging

log = logging.getLogger("ops.heartbeat")

_already_alerted: set[str] = set()


async def check_once() -> None:
    cfg = get_config().ops
    stale = await stale_heartbeats(cfg.heartbeat_missing_alert_after_seconds)
    stale_components = {c for c, _ in stale}

    for component, last_beat in stale:
        if component in _already_alerted:
            continue
        await publish(
            "alert.manager",
            {"source": "heartbeat", "message": f"Kein Herzschlag von {component} seit {last_beat} — Dienst überprüfen"},
        )
        _already_alerted.add(component)
        log.warning("component missed heartbeat", extra={"extra_fields": {"component": component}})

    _already_alerted.intersection_update(stale_components)  # clear alert-once flag once it recovers


async def run() -> None:
    configure_logging()
    cfg = get_config().ops
    while True:
        try:
            await check_once()
        except Exception:
            log.exception("heartbeat check failed")
        await asyncio.sleep(cfg.heartbeat_interval_seconds)


if __name__ == "__main__":
    asyncio.run(run())
