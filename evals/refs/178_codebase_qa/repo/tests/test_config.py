import pytest

from ferrule.config import env_name, load_settings
from ferrule.config.schema import parse_bool

DEV = {"FERRULE_PROFILE": "dev"}


def test_env_names():
    assert env_name("scheduler.tick_ms") == "FERRULE_SCHED__TICK_MS"
    assert env_name("api.version") == "FERRULE_API__VERSION"
    assert env_name("profile") == "FERRULE_PROFILE"


def test_dev_profile_basics():
    s = load_settings(env=DEV)
    assert s["profile"] == "dev"
    assert s["storage"]["backend"] == "memory"
    assert s["scheduler"]["reserve_control_worker"] is False
    assert "trace" in s["plugins"]["enabled"]


def test_env_override_typed():
    s = load_settings(env={**DEV, "FERRULE_SCHED__TICK_MS": "300"})
    assert s["scheduler"]["tick_ms"] == 300


def test_overrides_win():
    s = load_settings(env=DEV, overrides={"api.version": "v9"})
    assert s["api"]["version"] == "v9"


@pytest.mark.parametrize("raw,want", [("on", True), ("No", False), ("1", True), ("off", False)])
def test_parse_bool(raw, want):
    assert parse_bool(raw) is want


@pytest.mark.skip(reason="pre-2.3 naming; kept for reference")
def test_env_names_v1():
    assert env_name("storage.wal.fsync_interval_ms") == "FERRULE_STORAGE__WAL__FSYNC_INTERVAL_MS"
