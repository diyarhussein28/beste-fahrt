"""Domain models shared across services. Mirror the SQL schema in
migrations/0001_init.sql (see القسم 5 و8.7 من الوثيقة التقنية).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

try:
    from enum import StrEnum  # Python 3.11+
except ImportError:  # pragma: no cover — collector's Playwright base image ships Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class DriverStatus(StrEnum):
    AVAILABLE = "available"
    ON_JOB = "on_job"
    OFF_DUTY = "off_duty"


class JobStatus(StrEnum):
    OPEN = "open"
    DISPATCHED = "dispatched"
    TAKEN_BY_US = "taken_by_us"
    GONE = "gone"


class WatchStatus(StrEnum):
    OPEN = "open"
    MATCHED = "matched"
    CLOSED = "closed"
    EXPIRED = "expired"


class LocationSource(StrEnum):
    LIVE_SHARE = "live_share"
    LAST_DROPOFF = "last_dropoff"
    MANUAL = "manual"


class DispatchResponse(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"
    TIMEOUT = "timeout"


class Driver(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    name: str
    telegram_chat_id: int | None = None
    phone: str | None = None
    status: DriverStatus = DriverStatus.OFF_DUTY
    location_consent: bool = False
    active: bool = True
    license_classes: list[str] = Field(default_factory=list)
    jobs_today: int = 0
    home_lat: float | None = None
    home_lon: float | None = None
    home_city: str | None = None
    allow_overnight: bool = False


class DriverLocation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    driver_id: int
    lat: float
    lon: float
    source: LocationSource
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RawOffer(BaseModel):
    """What the platform-specific parser produces, before normalization.
    `platform` is the source's registry name (e.g. "movacarpro", "demo") —
    required so multiple platforms can be polled at once without their
    fingerprints or job records colliding.
    """

    platform: str
    platform_id: str | None = None
    pickup_address: str
    dropoff_address: str
    pickup_date: datetime | None = None
    price_eur: float | None = None
    url: str | None = None
    required_license: str | None = None  # e.g. a special-plate/trailer requirement tag


class Job(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fp: str
    platform: str = ""
    platform_id: str | None = None
    pickup_addr: str
    dropoff_addr: str
    pickup_lat: float | None = None
    pickup_lon: float | None = None
    dropoff_lat: float | None = None
    dropoff_lon: float | None = None
    price_eur: float | None = None
    route_km: float | None = None
    pickup_date: datetime | None = None
    url: str | None = None
    status: JobStatus = JobStatus.OPEN
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    gone_at: datetime | None = None
    required_license: str | None = None  # e.g. a license class needed for large vehicles

    @property
    def eur_per_km(self) -> float | None:
        if not self.price_eur or not self.route_km:
            return None
        return self.price_eur / self.route_km


FINGERPRINT_LENGTH = 16  # hex chars = 64 bits — see the docstring below for why


def job_fingerprint(raw: RawOffer) -> str:
    """Stable id for dedup — see القسم 4.4.

    Always a short hash, never the platform's raw id or the full SHA-256
    digest verbatim: this value ends up inside Telegram inline-button
    callback_data (e.g. "acc:<fp>", or two of them at once in
    "accboth:<fp>:<fp>"), which has a hard 64-byte limit. A platform's own
    id is short in practice but not guaranteed to be, and a full SHA-256
    digest (64 hex chars) alone already blows the limit once combined with
    even the shortest prefix — that combination is exactly what surfaced
    this in production against a real platform with no exposed offer id.
    64 bits (16 hex chars) is still effectively collision-free at this
    application's scale (a fleet's total lifetime job count, not billions).

    Namespaced by platform (hashed in, not concatenated raw) so two
    different platforms can't collide even if they reuse the same
    numbering scheme for their own ids.
    """
    if raw.platform_id:
        seed = f"{raw.platform}:{raw.platform_id}"
    else:
        seed = "|".join(
            [
                raw.platform,
                raw.pickup_address.strip().lower(),
                raw.dropoff_address.strip().lower(),
                raw.pickup_date.isoformat() if raw.pickup_date else "",
                f"{raw.price_eur:.2f}" if raw.price_eur is not None else "",
            ]
        )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


class Dispatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    job_fp: str
    driver_id: int
    rank: int
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    response: DispatchResponse | None = None
    responded_at: datetime | None = None


class ServiceArea(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    active: bool = True
    expires_at: datetime | None = None


class ReturnWatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    driver_id: int
    outbound_fp: str
    from_lat: float  # B — outbound drop-off point
    from_lon: float
    home_lat: float  # H — driver's home base
    home_lon: float
    available_at: datetime  # ETA_B
    expires_at: datetime
    status: WatchStatus = WatchStatus.OPEN
    matched_fp: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RankedDriver(BaseModel):
    driver: Driver
    approach_km: float
    loc_age_min: float
    score: float


class ReturnCandidate(BaseModel):
    job: Job
    deadhead_km: float
    remaining_km: float
    progress: float
    net_value: float
    wait_penalty: float
    return_score: float
    category: str  # A/B/C/D — see القسم 8.4
