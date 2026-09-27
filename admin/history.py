"""Groups the full dispatch history by driver and ISO week for the admin
panel's history tab. Pure function, no DB access, so it's unit-testable —
shared.db.list_dispatch_history() does the actual query.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class WeekStats:
    week_start: date
    week_end: date
    total_offers: int = 0
    accepted: int = 0
    declined: int = 0
    total_revenue_eur: float = 0.0
    total_km: float = 0.0


@dataclass
class DriverHistory:
    driver_id: int
    driver_name: str
    weeks: list[WeekStats] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)  # most-recent-first, for the raw log


def week_start_of(d: date) -> date:
    iso_year, iso_week, _ = d.isocalendar()
    return date.fromisocalendar(iso_year, iso_week, 1)


def group_history(rows: list[dict]) -> list[DriverHistory]:
    """`rows` is shared.db.list_dispatch_history()'s output — one row per
    dispatch attempt, already ordered by driver name then sent_at ascending.
    """
    by_driver: dict[int, DriverHistory] = {}

    for row in rows:
        driver_id = row["driver_id"]
        history = by_driver.get(driver_id)
        if history is None:
            history = DriverHistory(driver_id=driver_id, driver_name=row["driver_name"])
            by_driver[driver_id] = history

        history.entries.append(row)

        sent_at = row["sent_at"]
        week_start = week_start_of(sent_at.date())
        if not history.weeks or history.weeks[-1].week_start != week_start:
            history.weeks.append(WeekStats(week_start=week_start, week_end=week_start + timedelta(days=6)))
        week = history.weeks[-1]

        week.total_offers += 1
        if row["response"] == "accept":
            week.accepted += 1
            week.total_revenue_eur += float(row["price_eur"] or 0)
            week.total_km += float(row["route_km"] or 0) + float(row["approach_km"] or 0)
        elif row["response"] == "decline":
            week.declined += 1

    for history in by_driver.values():
        history.entries.reverse()  # most recent first for display
        history.weeks.reverse()  # most recent week first

    return sorted(by_driver.values(), key=lambda h: h.driver_name)
