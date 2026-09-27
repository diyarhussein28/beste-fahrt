from datetime import datetime, timezone

from shared.config import PrivacyConfig
from shared.models import Job, ReturnCandidate
from dispatcher.templates import (
    redact_address,
    render_combined_alert,
    render_manager_escalation,
    render_offer_alert,
    render_return_alert,
)

PRIVACY_FULL = PrivacyConfig(show_full_address_in_alert=True)
PRIVACY_REDACTED = PrivacyConfig(show_full_address_in_alert=False)


def make_job(**kwargs) -> Job:
    base = dict(
        fp="a" * 64,
        pickup_addr="Leverkusen-Opladen, 51379",
        dropoff_addr="Düsseldorf-Flingern, 40235",
        price_eur=65.0,
        route_km=38.0,
        pickup_date=datetime(2026, 9, 28, 9, 30, tzinfo=timezone.utc),
        url="https://platform.example/jobs/12345",
    )
    base.update(kwargs)
    return Job(**base)


def test_redact_address_keeps_only_city_when_disabled():
    assert redact_address("Leverkusen-Opladen, 51379", PRIVACY_REDACTED) == "Leverkusen-Opladen"


def test_redact_address_keeps_full_when_enabled():
    assert redact_address("Leverkusen-Opladen, 51379", PRIVACY_FULL) == "Leverkusen-Opladen, 51379"


def test_render_offer_alert_contains_key_fields():
    job = make_job()
    text, buttons = render_offer_alert(job, approach_km=4.2, privacy=PRIVACY_FULL)
    assert "4.2" in text
    assert "65,00" in text or "65.00" in text
    assert job.url in text
    assert buttons == [[("✅ Ich nehme ihn an", f"acc:{job.fp}"), ("❌ Kann ich nicht", f"dec:{job.fp}")]]


def test_render_offer_alert_redacts_address_by_default():
    job = make_job()
    text, _ = render_offer_alert(job, approach_km=4.2, privacy=PRIVACY_REDACTED)
    assert "51379" not in text
    assert "Leverkusen-Opladen" in text


def test_render_combined_alert_shows_both_legs():
    outbound = make_job(pickup_addr="Leverkusen", dropoff_addr="München", price_eur=320.0, route_km=590.0)
    ret_job = make_job(
        fp="b" * 64,
        pickup_addr="München-Pasing",
        dropoff_addr="Köln",
        price_eur=300.0,
        route_km=575.0,
    )
    ret = ReturnCandidate(
        job=ret_job,
        deadhead_km=12.0,
        remaining_km=0.0,
        progress=0.95,
        net_value=250.0,
        wait_penalty=0.0,
        return_score=250.0,
        category="A",
    )
    text, buttons = render_combined_alert(outbound, approach_km=5.0, ret=ret, privacy=PRIVACY_FULL)
    assert "München" in text
    assert "620,00" in text or "620.00" in text  # 320 + 300 combined total
    assert len(buttons[0]) == 3


def test_render_return_alert_has_train_option():
    ret_job = make_job(fp="c" * 64)
    ret = ReturnCandidate(
        job=ret_job, deadhead_km=18.0, remaining_km=3.0, progress=0.9,
        net_value=200.0, wait_penalty=0.0, return_score=200.0, category="A",
    )
    text, buttons = render_return_alert(ret, hours_after_eta=1.4, km_from_home=3.0, privacy=PRIVACY_FULL, watch_id=42)
    assert "Rückfahrt" in text
    labels = [label for row in buttons for label, _ in row]
    assert "🚆 Ich fahre mit dem Zug zurück" in labels


def test_render_manager_escalation_mentions_attempts():
    job = make_job()
    text = render_manager_escalation(job, attempts=3)
    assert "3" in text
    assert job.pickup_addr in text
