from shared.config import MatchingConfig, MatchingWeights
from shared.models import Driver, Job, RankedDriver
from matcher.engine import (
    driver_fits_job,
    eur_per_km,
    is_profitable,
    rank_candidates,
    should_broadcast,
)

WEIGHTS = MatchingConfig(weights=MatchingWeights(dist=1.0, stale=0.5, load=3.0, fit=1.0), stale_after_minutes=45)


def make_driver(**kwargs) -> Driver:
    base = dict(name="Test Driver", status="available")
    base.update(kwargs)
    return Driver(**base)


def make_job(**kwargs) -> Job:
    base = dict(fp="a" * 64, pickup_addr="A", dropoff_addr="B")
    base.update(kwargs)
    return Job(**base)


def test_closer_driver_ranks_first():
    job = make_job()
    near = RankedDriver(driver=make_driver(id=1), approach_km=2.0, loc_age_min=5, score=0)
    far = RankedDriver(driver=make_driver(id=2), approach_km=20.0, loc_age_min=5, score=0)
    ranked = rank_candidates([far, near], job, WEIGHTS)
    assert [c.driver.id for c in ranked] == [1, 2]


def test_busier_driver_ranks_behind_equally_close_idle_driver():
    job = make_job()
    idle = RankedDriver(driver=make_driver(id=1, jobs_today=0), approach_km=5.0, loc_age_min=5, score=0)
    busy = RankedDriver(driver=make_driver(id=2, jobs_today=4), approach_km=5.0, loc_age_min=5, score=0)
    ranked = rank_candidates([busy, idle], job, WEIGHTS)
    assert [c.driver.id for c in ranked] == [1, 2]


def test_stale_location_pushes_driver_down():
    job = make_job()
    fresh = RankedDriver(driver=make_driver(id=1), approach_km=5.0, loc_age_min=2, score=0)
    stale = RankedDriver(driver=make_driver(id=2), approach_km=5.0, loc_age_min=40, score=0)
    ranked = rank_candidates([stale, fresh], job, WEIGHTS)
    assert [c.driver.id for c in ranked] == [1, 2]


def test_hopelessly_stale_location_is_excluded():
    job = make_job()
    ancient = RankedDriver(driver=make_driver(id=1), approach_km=1.0, loc_age_min=999, score=0)
    ranked = rank_candidates([ancient], job, WEIGHTS)
    assert ranked == []


def test_driver_fits_job_no_requirement():
    driver = make_driver(license_classes=[])
    job = make_job(required_license=None)
    assert driver_fits_job(driver, job) is True


def test_driver_fits_job_with_requirement():
    job = make_job(required_license="BE")
    assert driver_fits_job(make_driver(license_classes=["BE"]), job) is True
    assert driver_fits_job(make_driver(license_classes=["B"]), job) is False


def test_unqualified_driver_ranks_behind_qualified_one_even_if_closer():
    job = make_job(required_license="BE")
    close_unqualified = RankedDriver(
        driver=make_driver(id=1, license_classes=[]), approach_km=1.0, loc_age_min=5, score=0
    )
    far_qualified = RankedDriver(
        driver=make_driver(id=2, license_classes=["BE"]), approach_km=10.0, loc_age_min=5, score=0
    )
    ranked = rank_candidates([close_unqualified, far_qualified], job, WEIGHTS)
    assert ranked[0].driver.id == 2


def test_eur_per_km():
    assert eur_per_km(65.0, 4.2, 38.0) == 65.0 / 42.2


def test_eur_per_km_missing_data_returns_none():
    assert eur_per_km(None, 4.0, 10.0) is None
    assert eur_per_km(50.0, 4.0, None) is None


def test_is_profitable_respects_threshold():
    config = MatchingConfig(min_eur_per_km=0.9)
    cheap = make_job(price_eur=10.0, route_km=100.0)  # 0.1 eur/km incl. 0 approach
    good = make_job(price_eur=65.0, route_km=38.0)
    assert is_profitable(cheap, approach_km=0.0, config=config) is False
    assert is_profitable(good, approach_km=4.2, config=config) is True


def test_is_profitable_disabled_threshold_accepts_everything():
    config = MatchingConfig(min_eur_per_km=0.0)
    job = make_job(price_eur=1.0, route_km=1000.0)
    assert is_profitable(job, approach_km=0.0, config=config) is True


def test_should_broadcast_threshold():
    job = make_job(price_eur=65.0, route_km=38.0)  # ~1.54 eur/km with 4.2km approach
    assert should_broadcast(job, approach_km=4.2, config=WEIGHTS, broadcast_threshold=1.8) is False
    assert should_broadcast(job, approach_km=4.2, config=WEIGHTS, broadcast_threshold=1.0) is True
    assert should_broadcast(job, approach_km=4.2, config=WEIGHTS, broadcast_threshold=None) is False
