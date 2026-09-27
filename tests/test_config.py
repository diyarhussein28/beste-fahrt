import os

from shared.config import AppConfig, PlatformEntry, get_platform_credentials


def test_platform_entry_parser_key_defaults_to_name():
    entry = PlatformEntry(name="movacarpro")
    assert entry.parser_key() == "movacarpro"


def test_platform_entry_parser_key_uses_explicit_parser():
    entry = PlatformEntry(name="my_movacarpro_account_2", parser="movacarpro")
    assert entry.parser_key() == "movacarpro"


def test_app_config_parses_platforms_list():
    raw = {
        "platforms": [
            {"name": "movacarpro", "enabled": True, "login_url": "https://movacarpro.com/login"},
            {"name": "another", "parser": "generic", "enabled": False},
        ],
        "service_area": {"center": [51.0459, 7.0192], "radius_km": 40},
    }
    cfg = AppConfig.model_validate(raw)
    assert len(cfg.platforms) == 2
    assert cfg.platforms[0].name == "movacarpro"
    assert cfg.platforms[1].enabled is False


def test_app_config_platforms_defaults_to_empty_list():
    cfg = AppConfig.model_validate({"service_area": {"center": [51.0, 7.0]}})
    assert cfg.platforms == []


def test_get_platform_credentials_reads_namespaced_env_vars(monkeypatch):
    monkeypatch.setenv("PLATFORM_MOVACARPRO_USERNAME", "someone@example.com")
    monkeypatch.setenv("PLATFORM_MOVACARPRO_PASSWORD", "hunter2")
    username, password = get_platform_credentials("movacarpro")
    assert username == "someone@example.com"
    assert password == "hunter2"


def test_get_platform_credentials_sanitizes_platform_name(monkeypatch):
    monkeypatch.setenv("PLATFORM_MY_PLATFORM_2_USERNAME", "u")
    username, _ = get_platform_credentials("my-platform-2")
    assert username == "u"


def test_get_platform_credentials_missing_returns_empty_strings():
    username, password = get_platform_credentials("nonexistent_platform_xyz")
    assert username == ""
    assert password == ""
