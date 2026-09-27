"""Weekly KPI report — القسم 15: 'يُرسل النظام تقريراً أسبوعياً تلقائياً
للمدير'. Runs continuously and fires every Monday at 08:00 local time;
`render_report` is separated out so it's testable without a clock or a DB.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from shared.events import publish
from shared.logging_config import configure_logging

from ops.kpis import Kpis, compute_kpis

log = logging.getLogger("ops.weekly_report")

REPORT_WEEKDAY = 0  # Monday
REPORT_HOUR = 8


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "—"
    minutes = value / 60
    return f"{minutes:.1f} Min." if minutes >= 1 else f"{value:.0f} Sek."


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "—"


def _fmt_num(value: float | None, unit: str) -> str:
    return f"{value:.2f} {unit}" if value is not None else "—"


def render_report(kpis: Kpis) -> str:
    lines = [
        f"📊 Wochenbericht (letzte {kpis.window_days} Tage)",
        f"⏱️ Zustellzeit (Ø): {_fmt_seconds(kpis.discovery_to_dispatch_avg_s)}",
        f"✅ Annahmequote: {_fmt_pct(kpis.acceptance_rate)}",
        f"⌛ Angebotsalter am Markt: {_fmt_seconds(kpis.market_age_avg_s)}",
        f"🔁 Anteil bezahlter Rückfahrten: {_fmt_pct(kpis.return_rate)}",
        f"🛣️ Leerkilometer pro Fahrt: {_fmt_num(kpis.avg_deadhead_km, 'km')}",
        f"💶 Umsatz pro km: {_fmt_num(kpis.avg_eur_per_km, '€/km')}",
        f"🎯 Tour-Rentabilität (Hin+Rück): {_fmt_num(kpis.round_trip_eur_per_km, '€/km')}",
        f"🚆 Ersparte Zugkosten (geschätzt): {_fmt_num(kpis.train_cost_saved_eur, '€')}",
    ]
    return "\n".join(lines)


def _seconds_until_next_report(now: datetime) -> float:
    days_ahead = (REPORT_WEEKDAY - now.weekday()) % 7
    target = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


async def run() -> None:
    configure_logging()
    while True:
        wait_s = _seconds_until_next_report(datetime.now())
        log.info("waiting for next weekly report", extra={"extra_fields": {"seconds": wait_s}})
        await asyncio.sleep(wait_s)
        try:
            kpis = await compute_kpis(window_days=7)
            await publish("alert.manager", {"source": "weekly_report", "message": render_report(kpis)})
        except Exception:
            log.exception("failed to generate weekly report")


if __name__ == "__main__":
    asyncio.run(run())
