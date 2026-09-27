from datetime import datetime

from ops.kpis import Kpis
from ops.weekly_report import _seconds_until_next_report, render_report


def make_kpis(**kwargs) -> Kpis:
    base = dict(
        window_days=7,
        discovery_to_dispatch_avg_s=45.0,
        acceptance_rate=0.62,
        market_age_avg_s=930.0,
        return_rate=0.41,
        avg_deadhead_km=6.3,
        avg_eur_per_km=1.35,
        round_trip_eur_per_km=0.98,
        train_cost_saved_eur=88.5,
    )
    base.update(kwargs)
    return Kpis(**base)


def test_render_report_contains_all_metrics():
    text = render_report(make_kpis())
    assert "62%" in text
    assert "41%" in text
    assert "6.30 كم" in text
    assert "1.35 €/كم" in text
    assert "88.50 €" in text


def test_render_report_handles_missing_data_gracefully():
    kpis = make_kpis(
        discovery_to_dispatch_avg_s=None,
        acceptance_rate=None,
        market_age_avg_s=None,
        return_rate=None,
        avg_deadhead_km=None,
        avg_eur_per_km=None,
        round_trip_eur_per_km=None,
        train_cost_saved_eur=None,
    )
    text = render_report(kpis)
    assert "—" in text
    assert "None" not in text


def test_seconds_until_next_report_same_day_before_report_time():
    # Monday 06:00 -> report fires the same day at 08:00
    now = datetime(2026, 9, 28, 6, 0)  # a Monday
    assert now.weekday() == 0
    seconds = _seconds_until_next_report(now)
    assert seconds == 2 * 3600


def test_seconds_until_next_report_same_day_after_report_time_rolls_to_next_week():
    now = datetime(2026, 9, 28, 9, 0)  # Monday, after 08:00
    seconds = _seconds_until_next_report(now)
    assert seconds == 7 * 24 * 3600 - 3600


def test_seconds_until_next_report_midweek():
    now = datetime(2026, 9, 30, 12, 0)  # Wednesday
    seconds = _seconds_until_next_report(now)
    days_ahead = seconds / 86400
    assert 4.5 < days_ahead < 5.5  # lands back on Monday
