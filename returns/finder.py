"""Return Trip Finder — القسم 8. Scores candidate return legs against the
alternative cost of a train ticket home, and buckets them into the A/B/C/D
categories from القسم 8.4 (C — chains — is assigned by returns/chains.py).
"""
from __future__ import annotations

from datetime import datetime

from shared.config import ReturnTripConfig
from shared.models import Job, ReturnCandidate

from returns.queries import candidates_near_point, row_to_job

CATEGORY_A = "A"  # عودة مباشرة
CATEGORY_B = "B"  # تقريب قوي
CATEGORY_D = "D"  # تقريب جزئي
B_PROGRESS_THRESHOLD = 0.6  # القسم 8.4: "نسبة التقدّم ≥ 60%"


def train_cost(distance_km: float, config: ReturnTripConfig) -> float:
    return distance_km * config.transit_cost_per_km


def categorize(progress: float, remaining_km: float, net_value: float, config: ReturnTripConfig) -> str | None:
    if remaining_km <= config.home_radius_km:
        return CATEGORY_A
    if progress >= B_PROGRESS_THRESHOLD:
        return CATEGORY_B
    if progress >= config.min_progress and net_value > 0:
        return CATEGORY_D
    return None


def score_leg(
    job: Job,
    deadhead_km: float,
    remaining_km: float,
    dist_b_h_km: float,
    eta_b: datetime,
    config: ReturnTripConfig,
) -> ReturnCandidate | None:
    """القسم 8.6 — the core formula, independent of where the candidate job
    came from (a bulk DB query or a single job checked against one watch).
    """
    if dist_b_h_km <= 0 or job.price_eur is None:
        return None

    progress = (dist_b_h_km - remaining_km) / dist_b_h_km

    deadhead_cost = train_cost(deadhead_km, config)
    remaining_cost = train_cost(remaining_km, config) if remaining_km > config.home_radius_km else 0.0
    net_value = job.price_eur - deadhead_cost - remaining_cost + train_cost(dist_b_h_km, config)

    wait_hours = max(0.0, (job.pickup_date - eta_b).total_seconds() / 3600) if job.pickup_date else 0.0
    wait_penalty = config.wait_penalty_eur_per_hour * wait_hours
    return_score = net_value - wait_penalty

    category = categorize(progress, remaining_km, net_value, config)
    if category is None:
        return None

    return ReturnCandidate(
        job=job,
        deadhead_km=deadhead_km,
        remaining_km=remaining_km,
        progress=progress,
        net_value=net_value,
        wait_penalty=wait_penalty,
        return_score=return_score,
        category=category,
    )


def score_candidate(
    row: dict,
    dist_b_h_km: float,
    eta_b: datetime,
    config: ReturnTripConfig,
) -> ReturnCandidate | None:
    """القسم 8.6. `row` comes from returns.queries.candidates_near_point:
    it carries deadhead_km, remaining_km and the job fields.
    """
    return score_leg(
        row_to_job(row), float(row["deadhead_km"]), float(row["remaining_km"]), dist_b_h_km, eta_b, config
    )


def check_job_against_watch(job: Job, watch, config: ReturnTripConfig) -> ReturnCandidate | None:
    """Cheap in-process check (no DB round-trip) for whether a single
    just-discovered job satisfies one open return watch — used by
    returns/service.py so it doesn't have to re-run the bulk query for
    every open watch on every new job.
    """
    from matcher.routing import haversine_km

    if job.pickup_lat is None or job.pickup_lon is None or job.dropoff_lat is None or job.dropoff_lon is None:
        return None

    deadhead_km = haversine_km(watch.from_lat, watch.from_lon, job.pickup_lat, job.pickup_lon)
    if deadhead_km > config.pickup_radius_km:
        return None

    dist_b_h = haversine_km(watch.from_lat, watch.from_lon, watch.home_lat, watch.home_lon)
    remaining_km = haversine_km(job.dropoff_lat, job.dropoff_lon, watch.home_lat, watch.home_lon)
    if remaining_km >= dist_b_h:
        return None  # moves the driver away from home, not a valid return leg

    if job.pickup_date is not None:
        from datetime import timedelta

        min_pickup = watch.available_at + timedelta(minutes=config.min_buffer_minutes)
        max_pickup = watch.available_at + timedelta(hours=config.max_wait_hours)
        if not (min_pickup <= job.pickup_date <= max_pickup):
            return None

    return score_leg(job, deadhead_km, remaining_km, dist_b_h, watch.available_at, config)


async def find_candidates(
    from_lat: float,
    from_lon: float,
    home_lat: float,
    home_lon: float,
    eta_b: datetime,
    dist_b_h_km: float,
    config: ReturnTripConfig,
) -> list[ReturnCandidate]:
    rows = await candidates_near_point(
        from_lat,
        from_lon,
        home_lat,
        home_lon,
        after=eta_b,
        pickup_radius_km=config.pickup_radius_km,
        min_buffer_minutes=config.min_buffer_minutes,
        max_wait_hours=config.max_wait_hours,
    )
    scored = [score_candidate(row, dist_b_h_km, eta_b, config) for row in rows]
    candidates = [c for c in scored if c is not None]
    return sorted(candidates, key=lambda c: c.return_score, reverse=True)


async def find_before_dispatch(outbound: Job, home_lat: float, home_lon: float, config: ReturnTripConfig) -> ReturnCandidate | None:
    """البحث المسبق قبل إرسال تنبيه الذهاب — القسم 8.2 (النقطة الأولى) و8.11.
    Only called for outbound jobs above `trigger_min_outbound_km`.
    """
    from matcher.routing import haversine_km
    from returns.queries import estimate_eta

    if outbound.dropoff_lat is None or outbound.dropoff_lon is None:
        return None

    eta_b = estimate_eta(outbound.pickup_date, outbound.route_km)
    dist_b_h = haversine_km(outbound.dropoff_lat, outbound.dropoff_lon, home_lat, home_lon)

    candidates = await find_candidates(
        outbound.dropoff_lat, outbound.dropoff_lon, home_lat, home_lon, eta_b, dist_b_h, config
    )
    return candidates[0] if candidates else None
