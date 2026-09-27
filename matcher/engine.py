"""Matching Engine — القسم 6. Ranks available drivers for a job and computes
the profitability index shown to the driver in the alert.
"""
from __future__ import annotations

from shared.config import MatchingConfig
from shared.models import Driver, Job, RankedDriver


def driver_fits_job(driver: Driver, job: Job) -> bool:
    if job.required_license is None:
        return True
    return job.required_license in driver.license_classes


def score_driver(
    driver: Driver,
    approach_km: float,
    loc_age_min: float,
    job: Job,
    weights: "MatchingConfig",
) -> float:
    """Lower is better — القسم 6.3. Distance and stale-location are costs;
    a driver already loaded with jobs today is pushed back for fairness;
    a driver who lacks a required qualification is pushed back hard rather
    than excluded outright, so they still get a shot if nobody else fits.
    """
    w = weights.weights
    fit_bonus = 100.0 if driver_fits_job(driver, job) else 0.0
    return (
        w.dist * approach_km
        + w.stale * (loc_age_min / 10)
        + w.load * driver.jobs_today
        - w.fit * fit_bonus
    )


def rank_candidates(
    candidates: list[RankedDriver], job: Job, config: MatchingConfig
) -> list[RankedDriver]:
    """Fills in `.score` for each candidate and returns them sorted, best first."""
    scored = [
        c.model_copy(update={"score": score_driver(c.driver, c.approach_km, c.loc_age_min, job, config)})
        for c in candidates
        if c.loc_age_min <= config.stale_after_minutes * 3  # ignore hopelessly stale locations
    ]
    return sorted(scored, key=lambda c: c.score)


def eur_per_km(price_eur: float | None, approach_km: float, route_km: float | None) -> float | None:
    """Profitability index shown to the driver — القسم 6.3 وقالب الرسالة 7.3."""
    if price_eur is None or route_km is None:
        return None
    total_km = approach_km + route_km
    if total_km <= 0:
        return None
    return price_eur / total_km


def is_profitable(job: Job, approach_km: float, config: MatchingConfig) -> bool:
    if config.min_eur_per_km <= 0:
        return True
    epk = eur_per_km(job.price_eur, approach_km, job.route_km)
    return epk is None or epk >= config.min_eur_per_km


def should_broadcast(job: Job, approach_km: float, config, broadcast_threshold: float | None) -> bool:
    """High-profitability jobs can be sent to several drivers at once —
    القسم 7.2: 'يمكن تفعيل وضع البث للعروض عالية الربحية'.
    """
    if not broadcast_threshold:
        return False
    epk = eur_per_km(job.price_eur, approach_km, job.route_km)
    return epk is not None and epk >= broadcast_threshold
