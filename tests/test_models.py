from shared.models import FINGERPRINT_LENGTH, RawOffer, job_fingerprint


def make_offer(**kwargs) -> RawOffer:
    base = dict(platform="demo", pickup_address="A", dropoff_address="B")
    base.update(kwargs)
    return RawOffer(**base)


def test_fingerprint_is_short_enough_for_telegram_callback_data():
    # Two fps must fit together in "accboth:<fp>:<fp>" under Telegram's
    # 64-byte callback_data limit — this is what a bare 64-char SHA-256
    # digest broke in production against a platform with no exposed id.
    fp = job_fingerprint(make_offer(pickup_address="Somewhere Quite Long", dropoff_address="Also Long Enough"))
    assert len(fp) == FINGERPRINT_LENGTH
    assert len(f"accboth:{fp}:{fp}".encode("utf-8")) <= 64


def test_fingerprint_namespaced_by_platform_when_using_platform_id():
    a = make_offer(platform="movacarpro", platform_id="12345")
    b = make_offer(platform="another_platform", platform_id="12345")
    assert job_fingerprint(a) != job_fingerprint(b)


def test_fingerprint_stable_for_same_platform_and_id():
    a = make_offer(platform="movacarpro", platform_id="12345")
    b = make_offer(platform="movacarpro", platform_id="12345")
    assert job_fingerprint(a) == job_fingerprint(b)


def test_fingerprint_hash_fallback_namespaced_by_platform():
    a = make_offer(platform="movacarpro", pickup_address="X", dropoff_address="Y")
    b = make_offer(platform="another_platform", pickup_address="X", dropoff_address="Y")
    assert job_fingerprint(a) != job_fingerprint(b)


def test_fingerprint_hash_fallback_stable_for_identical_offers():
    a = make_offer(pickup_address="X", dropoff_address="Y", price_eur=10.0)
    b = make_offer(pickup_address="X", dropoff_address="Y", price_eur=10.0)
    assert job_fingerprint(a) == job_fingerprint(b)
