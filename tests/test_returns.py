from datetime import datetime, timedelta, timezone

from shared.config import ReturnTripConfig
from shared.models import Job, JobStatus, ReturnWatch, WatchStatus
from returns.finder import categorize, check_job_against_watch, score_leg, train_cost
from returns.chains import chain_value

CONFIG = ReturnTripConfig(
    pickup_radius_km=30,
    home_radius_km=20,
    min_progress=0.3,
    min_buffer_minutes=30,
    max_wait_hours=3,
    transit_cost_per_km=0.15,
    wait_penalty_eur_per_hour=5.0,
)

# H = home base, B = outbound drop-off (~445 km apart, roughly Leverkusen<->München-ish)
HOME = (50.0, 7.0)
B = (48.0, 11.5)


def make_job(**kwargs) -> Job:
    base = dict(fp="a" * 64, pickup_addr="X", dropoff_addr="Y", status=JobStatus.OPEN)
    base.update(kwargs)
    return Job(**base)


def make_watch(available_at: datetime, **kwargs) -> ReturnWatch:
    base = dict(
        id=1,
        driver_id=1,
        outbound_fp="a" * 64,
        from_lat=B[0],
        from_lon=B[1],
        home_lat=HOME[0],
        home_lon=HOME[1],
        available_at=available_at,
        expires_at=available_at + timedelta(hours=4),
        status=WatchStatus.OPEN,
    )
    base.update(kwargs)
    return ReturnWatch(**base)


def test_train_cost_scales_linearly():
    assert train_cost(100, CONFIG) == 15.0


def test_categorize_direct_return():
    assert categorize(progress=0.1, remaining_km=5.0, net_value=10.0, config=CONFIG) == "A"


def test_categorize_strong_approach():
    assert categorize(progress=0.7, remaining_km=100.0, net_value=10.0, config=CONFIG) == "B"


def test_categorize_partial_approach_requires_positive_value():
    assert categorize(progress=0.4, remaining_km=100.0, net_value=10.0, config=CONFIG) == "D"
    assert categorize(progress=0.4, remaining_km=100.0, net_value=-5.0, config=CONFIG) is None


def test_categorize_rejects_below_minimum_progress():
    assert categorize(progress=0.1, remaining_km=200.0, net_value=50.0, config=CONFIG) is None


def test_score_leg_rejects_missing_price():
    job = make_job(price_eur=None)
    assert score_leg(job, deadhead_km=5, remaining_km=5, dist_b_h_km=400, eta_b=datetime.now(timezone.utc), config=CONFIG) is None


def test_score_leg_direct_return_has_positive_score_for_good_price():
    job = make_job(price_eur=300.0, pickup_date=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    eta_b = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    result = score_leg(job, deadhead_km=12.0, remaining_km=3.0, dist_b_h_km=445.0, eta_b=eta_b, config=CONFIG)
    assert result is not None
    assert result.category == "A"
    assert result.return_score > 0


def test_check_job_against_watch_rejects_job_outside_pickup_radius():
    watch = make_watch(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
    far_pickup = Job(
        fp="b" * 64, pickup_addr="far", dropoff_addr="home-ish",
        pickup_lat=40.0, pickup_lon=11.5,  # ~890km away from B, way outside 30km radius
        dropoff_lat=HOME[0], dropoff_lon=HOME[1],
        price_eur=300.0, pickup_date=watch.available_at + timedelta(hours=1),
    )
    assert check_job_against_watch(far_pickup, watch, CONFIG) is None


def test_check_job_against_watch_rejects_job_moving_away_from_home():
    watch = make_watch(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
    away_job = Job(
        fp="c" * 64, pickup_addr="near-B", dropoff_addr="further-away",
        pickup_lat=B[0] + 0.05, pickup_lon=B[1] + 0.05,
        dropoff_lat=45.0, dropoff_lon=15.0,  # further from home than B is
        price_eur=200.0, pickup_date=watch.available_at + timedelta(hours=1),
    )
    assert check_job_against_watch(away_job, watch, CONFIG) is None


def test_check_job_against_watch_rejects_pickup_outside_time_window():
    watch = make_watch(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
    too_soon = Job(
        fp="d" * 64, pickup_addr="near-B", dropoff_addr="near-home",
        pickup_lat=B[0] + 0.05, pickup_lon=B[1] + 0.05,
        dropoff_lat=HOME[0], dropoff_lon=HOME[1],
        price_eur=200.0, pickup_date=watch.available_at + timedelta(minutes=5),  # inside the 30-min buffer
    )
    assert check_job_against_watch(too_soon, watch, CONFIG) is None

    too_late = too_soon.model_copy(update={"fp": "e" * 64, "pickup_date": watch.available_at + timedelta(hours=10)})
    assert check_job_against_watch(too_late, watch, CONFIG) is None


def test_check_job_against_watch_accepts_valid_direct_return():
    watch = make_watch(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
    good_job = Job(
        fp="f" * 64, pickup_addr="near-B", dropoff_addr="near-home",
        pickup_lat=B[0] + 0.05, pickup_lon=B[1] + 0.05,
        dropoff_lat=HOME[0] + 0.05, dropoff_lon=HOME[1] + 0.05,
        price_eur=250.0, pickup_date=watch.available_at + timedelta(hours=1),
    )
    result = check_job_against_watch(good_job, watch, CONFIG)
    assert result is not None
    assert result.category == "A"


def test_chain_value_subtracts_deadhead_between_legs():
    # A gap between B and leg1's pickup, and between leg1's dropoff and leg2's
    # pickup, so both deadhead legs are actually non-zero and get subtracted.
    leg1 = make_job(fp="1" * 64, price_eur=100.0, pickup_lat=47.8, pickup_lon=11.3, dropoff_lat=49.0, dropoff_lon=9.0)
    leg2 = make_job(fp="2" * 64, price_eur=100.0, pickup_lat=49.2, pickup_lon=8.8, dropoff_lat=50.0, dropoff_lon=7.0)
    value = chain_value([leg1, leg2], from_lat=B[0], from_lon=B[1], dist_b_h_km=445.0, config=CONFIG)
    # 200 in fares, minus two (small but nonzero) deadhead legs, plus the
    # train-fare baseline for B->H — strictly less than the no-deadhead case.
    assert value < 200.0 + train_cost(445.0, CONFIG)
    assert value > 0

    contiguous = chain_value(
        [leg1.model_copy(update={"pickup_lat": B[0], "pickup_lon": B[1]})], from_lat=B[0], from_lon=B[1],
        dist_b_h_km=445.0, config=CONFIG,
    )
    assert contiguous == 100.0 + train_cost(445.0, CONFIG)
