"""Loads runtime configuration: secrets from the environment (.env) and
business-logic tunables from config.yaml (see the القسم 4-8 sections of the
technical spec). Kept separate so ops can tune polling/matching/return-trip
behavior by editing config.yaml without touching code or redeploying secrets.
"""
from __future__ import annotations

import os
from datetime import time
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Selectors(BaseModel):
    offer_card: str
    offer_id: str = ""
    pickup_address: str = ""
    dropoff_address: str = ""
    pickup_date: str = ""
    price: str = ""
    offer_url: str = ""


class PlatformConfig(BaseModel):
    offers_url: str
    login_url: str = ""
    selectors: Selectors


class PollingConfig(BaseModel):
    base_seconds: int = 30
    jitter_seconds: int = 10
    active_hours: str = "06:00-21:00"
    max_backoff_seconds: int = 600
    rate_limit_pause_seconds: int = 900

    def active_window(self) -> tuple[time, time]:
        start_s, end_s = self.active_hours.split("-")
        h, m = (int(x) for x in start_s.split(":"))
        start = time(h, m)
        h, m = (int(x) for x in end_s.split(":"))
        end = time(h, m)
        return start, end


class ServiceAreaConfig(BaseModel):
    center: tuple[float, float]
    radius_km: float = 40.0


class MatchingWeights(BaseModel):
    dist: float = 1.0
    stale: float = 0.5
    load: float = 3.0
    fit: float = 1.0


class MatchingConfig(BaseModel):
    weights: MatchingWeights = Field(default_factory=MatchingWeights)
    stale_after_minutes: int = 45
    min_eur_per_km: float = 0.0
    top_n_for_road_distance: int = 5


class DispatchConfig(BaseModel):
    channel: str = "telegram"
    response_timeout_seconds: int = 45
    max_attempts: int = 3
    broadcast_if_eur_per_km_above: float | None = None


class ReturnTripConfig(BaseModel):
    enabled: bool = True
    trigger_min_outbound_km: float = 100.0
    pickup_radius_km: float = 30.0
    home_radius_km: float = 20.0
    min_progress: float = 0.3
    min_buffer_minutes: int = 30
    max_wait_hours: float = 3.0
    watch_expires_after_hours: float = 4.0
    max_chain_legs: int = 2
    transit_cost_per_km: float = 0.15


class PrivacyConfig(BaseModel):
    location_retention_days: int = 30
    show_full_address_in_alert: bool = False


class OpsConfig(BaseModel):
    heartbeat_interval_seconds: int = 60
    heartbeat_missing_alert_after_seconds: int = 300


class AppConfig(BaseModel):
    """Business-logic settings, loaded from config.yaml."""

    platform: PlatformConfig
    polling: PollingConfig = Field(default_factory=PollingConfig)
    service_area: ServiceAreaConfig
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    return_trip: ReturnTripConfig = Field(default_factory=ReturnTripConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    ops: OpsConfig = Field(default_factory=OpsConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)


class Secrets(BaseSettings):
    """Secrets and connection strings, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fleet:fleet@localhost:5432/fleet_dispatch"
    redis_url: str = "redis://localhost:6379/0"

    platform_username: str = ""
    platform_password: str = ""
    state_file: str = "./data/storage_state.json"
    state_enc_key: str = ""  # if unset, derived from admin_secret_key (see collector/session.py)

    telegram_bot_token: str = ""
    telegram_manager_chat_id: int = 0

    nominatim_url: str = "https://nominatim.openstreetmap.org"
    osrm_url: str = ""

    config_path: str = "config.yaml"

    admin_secret_key: str = "dev-only-change-me"


@lru_cache
def get_secrets() -> Secrets:
    return Secrets()


@lru_cache
def get_config() -> AppConfig:
    secrets = get_secrets()
    path = secrets.config_path
    if not os.path.isabs(path) and not Path(path).exists():
        # fall back to the repo-root config.yaml when run from a subpackage
        repo_root = Path(__file__).resolve().parent.parent
        path = repo_root / path
    return AppConfig.from_yaml(path)


settings = get_config
"""Callable alias kept for readability at call sites: `settings().matching...`"""
