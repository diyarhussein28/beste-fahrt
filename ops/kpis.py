"""KPI queries — القسم 15. Each function covers one row of the table there.
Used by both the admin dashboard and the automatic weekly report.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from shared.db import get_engine


@dataclass
class Kpis:
    window_days: int
    discovery_to_dispatch_avg_s: float | None  # زمن التوجيه: dispatches.sent_at - jobs.first_seen
    acceptance_rate: float | None  # نسبة القبول: accept / كل الإرسالات
    market_age_avg_s: float | None  # عمر العرض في السوق: gone_at - first_seen
    return_rate: float | None  # نسبة العودة المدفوعة
    avg_deadhead_km: float | None  # الكم الفارغ لكل رحلة: متوسط approach_km للرحلات المقبولة
    avg_eur_per_km: float | None  # الإيراد لكل كم
    round_trip_eur_per_km: float | None  # ربحية الجولة (للرحلات ذات عودة)
    train_cost_saved_eur: float | None  # تكلفة القطار الموفّرة


async def compute_kpis(window_days: int = 7) -> Kpis:
    async with get_engine().connect() as conn:
        dispatch_time = (
            await conn.execute(
                text(
                    """
                    SELECT AVG(EXTRACT(EPOCH FROM d.sent_at - j.first_seen))
                    FROM dispatches d JOIN jobs j ON j.fp = d.job_fp
                    WHERE d.sent_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

        acceptance = (
            await conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE response = 'accept')::float
                        / NULLIF(COUNT(*) FILTER (WHERE response IS NOT NULL), 0)
                    FROM dispatches
                    WHERE sent_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

        market_age = (
            await conn.execute(
                text(
                    """
                    SELECT AVG(EXTRACT(EPOCH FROM gone_at - first_seen))
                    FROM jobs
                    WHERE gone_at IS NOT NULL AND gone_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

        deadhead = (
            await conn.execute(
                text(
                    """
                    SELECT AVG(d.approach_km)
                    FROM dispatches d
                    WHERE d.response = 'accept' AND d.sent_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

        eur_per_km = (
            await conn.execute(
                text(
                    """
                    SELECT AVG(j.price_eur / NULLIF(d.approach_km + j.route_km, 0))
                    FROM dispatches d JOIN jobs j ON j.fp = d.job_fp
                    WHERE d.response = 'accept' AND d.sent_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

        return_stats = (
            await conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE rw.status IN ('matched', 'closed'))::float
                        / NULLIF(COUNT(*), 0) AS rate,
                        AVG(
                            ST_Distance(rw.from_geom, rw.home_geom) / 1000 * 0.15
                        ) FILTER (WHERE rw.status IN ('matched', 'closed')) AS train_cost_saved
                    FROM return_watches rw
                    WHERE rw.created_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).mappings().first()

        round_trip = (
            await conn.execute(
                text(
                    """
                    SELECT AVG((jo.price_eur + jr.price_eur) / NULLIF(jo.route_km + jr.route_km, 0))
                    FROM return_watches rw
                    JOIN jobs jo ON jo.fp = rw.outbound_fp
                    JOIN jobs jr ON jr.fp = rw.matched_fp
                    WHERE rw.status IN ('matched', 'closed')
                      AND rw.created_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": window_days},
            )
        ).scalar()

    return Kpis(
        window_days=window_days,
        discovery_to_dispatch_avg_s=dispatch_time,
        acceptance_rate=acceptance,
        market_age_avg_s=market_age,
        return_rate=return_stats["rate"] if return_stats else None,
        avg_deadhead_km=deadhead,
        avg_eur_per_km=eur_per_km,
        round_trip_eur_per_km=round_trip,
        train_cost_saved_eur=return_stats["train_cost_saved"] if return_stats else None,
    )
