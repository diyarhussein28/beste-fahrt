"""Two-leg return chains — القسم 8.9. Only used when no single return trip
gets the driver within `home_radius_km` of home; searches one intermediate
stop deep (never more, to avoid combinatorial blow-up) and only continues a
branch that already moved the driver closer to home.
"""
from __future__ import annotations

from datetime import datetime

from shared.config import ReturnTripConfig
from shared.models import Job

from matcher.routing import haversine_km
from returns.finder import train_cost
from returns.queries import candidates_near_point, estimate_eta, row_to_job

Chain = list[Job]


def chain_value(chain: Chain, from_lat: float, from_lon: float, dist_b_h_km: float, config: ReturnTripConfig) -> float:
    """Sum of leg prices minus the deadhead between each leg, plus the train
    fare the driver would otherwise have paid for the whole B->H trip —
    mirrors القسم 8.6's net_value, extended across every leg of the chain.
    """
    total = sum(leg.price_eur or 0.0 for leg in chain)
    prev_lat, prev_lon = from_lat, from_lon
    for leg in chain:
        if leg.pickup_lat is not None and leg.pickup_lon is not None:
            deadhead = haversine_km(prev_lat, prev_lon, leg.pickup_lat, leg.pickup_lon)
            total -= train_cost(deadhead, config)
        prev_lat, prev_lon = leg.dropoff_lat or prev_lat, leg.dropoff_lon or prev_lon
    return total + train_cost(dist_b_h_km, config)


async def find_chains(
    from_lat: float,
    from_lon: float,
    home_lat: float,
    home_lon: float,
    after: datetime,
    config: ReturnTripConfig,
    max_legs: int = 2,
) -> list[Chain]:
    dist_b_h = haversine_km(from_lat, from_lon, home_lat, home_lon)
    first_leg_rows = await candidates_near_point(
        from_lat, from_lon, home_lat, home_lon, after,
        config.pickup_radius_km, config.min_buffer_minutes, config.max_wait_hours,
    )

    chains: list[Chain] = []
    for row in first_leg_rows:
        r1 = row_to_job(row)
        if float(row["remaining_km"]) <= config.home_radius_km:
            chains.append([r1])
            continue
        if max_legs < 2 or r1.dropoff_lat is None or r1.dropoff_lon is None:
            continue

        eta_x = estimate_eta(r1.pickup_date, r1.route_km)
        second_leg_rows = await candidates_near_point(
            r1.dropoff_lat, r1.dropoff_lon, home_lat, home_lon, eta_x,
            config.pickup_radius_km, config.min_buffer_minutes, config.max_wait_hours,
        )
        for row2 in second_leg_rows:
            if float(row2["remaining_km"]) <= config.home_radius_km:
                chains.append([r1, row_to_job(row2)])

    chains.sort(key=lambda c: chain_value(c, from_lat, from_lon, dist_b_h, config), reverse=True)
    return chains[:3]
