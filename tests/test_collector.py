import asyncio
from datetime import datetime, timezone

import pytest

from shared.config import PollingConfig
from shared.models import RawOffer
from collector.monitor import _seconds_until_active, _within_active_hours, normalize
from collector.session import decrypt_state, encrypt_state
from collector.demo_parser import DemoOfferParser


def test_within_active_hours_normal_window(monkeypatch):
    cfg = PollingConfig(active_hours="06:00-21:00")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, 0)

    monkeypatch.setattr("collector.monitor.datetime", FakeDatetime)
    assert _within_active_hours(cfg) is True


def test_outside_active_hours_normal_window(monkeypatch):
    cfg = PollingConfig(active_hours="06:00-21:00")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 0)

    monkeypatch.setattr("collector.monitor.datetime", FakeDatetime)
    assert _within_active_hours(cfg) is False


def test_active_hours_window_spanning_midnight(monkeypatch):
    cfg = PollingConfig(active_hours="22:00-04:00")

    class FakeDatetimeNight(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 30)

    monkeypatch.setattr("collector.monitor.datetime", FakeDatetimeNight)
    assert _within_active_hours(cfg) is True

    class FakeDatetimeDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, 0)

    monkeypatch.setattr("collector.monitor.datetime", FakeDatetimeDay)
    assert _within_active_hours(cfg) is False


def test_normalize_computes_route_km_when_both_geocoded():
    raw = RawOffer(
        platform="demo",
        pickup_address="Leverkusen",
        dropoff_address="Köln",
        price_eur=65.0,
        pickup_date=datetime(2026, 9, 28, 9, 30, tzinfo=timezone.utc),
    )
    job = normalize(raw, pickup_geo=(51.0459, 7.0192), dropoff_geo=(50.9375, 6.9603))
    assert job.route_km is not None
    assert 5 < job.route_km < 20
    assert job.pickup_lat == 51.0459
    assert job.platform == "demo"


def test_normalize_without_dropoff_geo_has_no_route_km():
    raw = RawOffer(platform="demo", pickup_address="Leverkusen", dropoff_address="Unknown Place")
    job = normalize(raw, pickup_geo=(51.0459, 7.0192), dropoff_geo=None)
    assert job.route_km is None


def test_normalize_carries_required_license_through():
    raw = RawOffer(platform="movacarpro", pickup_address="A", dropoff_address="B", required_license="anhaenger")
    job = normalize(raw, pickup_geo=(51.0, 7.0), dropoff_geo=(51.0, 7.0))
    assert job.required_license == "anhaenger"


def test_session_state_encrypt_decrypt_roundtrip():
    state = {"cookies": [{"name": "session", "value": "abc123"}], "origins": []}
    token = encrypt_state(state)
    assert decrypt_state(token) == state


def test_demo_parser_returns_valid_raw_offers():
    parser = DemoOfferParser()
    seen_any = False
    for _ in range(20):
        offers = asyncio.run(parser.fetch_offers(page=None))
        for offer in offers:
            seen_any = True
            assert offer.pickup_address != offer.dropoff_address
            assert offer.price_eur > 0
    assert seen_any


def test_demo_parser_offers_persist_across_cycles():
    """A driver needs a few cycles to react to an alert — an offer must not
    vanish on the very next poll like a fresh spin of the wheel would.
    """
    parser = DemoOfferParser()
    first_ids: set[str] = set()
    for _ in range(20):
        first_ids = {o.platform_id for o in asyncio.run(parser.fetch_offers(page=None))}
        if first_ids:
            break
    assert first_ids, "expected at least one offer to spawn within 20 cycles"

    second_ids = {o.platform_id for o in asyncio.run(parser.fetch_offers(page=None))}
    assert first_ids & second_ids, "an offer should still be listed on the very next poll"


def test_demo_parser_caps_active_offers():
    parser = DemoOfferParser()
    for _ in range(50):
        offers = asyncio.run(parser.fetch_offers(page=None))
        assert len(offers) <= 5
