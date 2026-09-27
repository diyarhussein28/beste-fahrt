from datetime import datetime, timezone

from admin.history import group_history, week_start_of


def make_row(driver_id, driver_name, sent_at, response=None, price_eur=100.0, route_km=50.0, approach_km=5.0, rank=1):
    return dict(
        sent_at=sent_at,
        rank=rank,
        response=response,
        responded_at=None,
        approach_km=approach_km,
        driver_id=driver_id,
        driver_name=driver_name,
        job_fp="a" * 64,
        pickup_addr="A",
        dropoff_addr="B",
        price_eur=price_eur,
        route_km=route_km,
    )


def test_week_start_of_is_monday():
    # 2026-09-30 is a Wednesday
    assert week_start_of(datetime(2026, 9, 30).date()).weekday() == 0


def test_group_history_empty():
    assert group_history([]) == []


def test_group_history_groups_by_driver():
    rows = [
        make_row(1, "Ahmed", datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc), response="accept"),
        make_row(2, "Bilal", datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc), response="decline"),
    ]
    drivers = group_history(rows)
    assert [d.driver_name for d in drivers] == ["Ahmed", "Bilal"]  # sorted by name


def test_group_history_splits_into_weeks_and_sums_accepted_revenue():
    week1_day = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)  # Tuesday
    week2_day = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)  # following week
    rows = [
        make_row(1, "Ahmed", week1_day, response="accept", price_eur=100.0, route_km=40.0, approach_km=10.0),
        make_row(1, "Ahmed", week1_day, response="decline", price_eur=999.0),  # declined -> not counted in revenue
        make_row(1, "Ahmed", week2_day, response="accept", price_eur=50.0, route_km=20.0, approach_km=5.0),
    ]
    drivers = group_history(rows)
    assert len(drivers) == 1
    history = drivers[0]
    assert len(history.weeks) == 2

    # most recent week first
    latest, earliest = history.weeks
    assert latest.week_start > earliest.week_start
    assert latest.total_offers == 1
    assert latest.accepted == 1
    assert latest.total_revenue_eur == 50.0
    assert latest.total_km == 25.0

    assert earliest.total_offers == 2
    assert earliest.accepted == 1
    assert earliest.declined == 1
    assert earliest.total_revenue_eur == 100.0
    assert earliest.total_km == 50.0


def test_group_history_entries_most_recent_first():
    older = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 2, tzinfo=timezone.utc)
    rows = [
        make_row(1, "Ahmed", older, response="accept"),
        make_row(1, "Ahmed", newer, response="decline"),
    ]
    history = group_history(rows)[0]
    assert history.entries[0]["sent_at"] == newer
    assert history.entries[1]["sent_at"] == older
