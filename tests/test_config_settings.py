"""Settings smoke test (offline, credential-free)."""

import pytest

from trafficfuse.config import Settings


def test_defaults_are_local_and_disposable() -> None:
    settings = Settings.from_env({})
    assert settings.environment == "local"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.kind_cluster == "trafficfuse-local"


def test_environment_overrides() -> None:
    settings = Settings.from_env({"TRAFFICFUSE_ENV": "test", "TRAFFICFUSE_PORT": "9000"})
    assert settings.environment == "test"
    assert settings.port == 9000


def test_non_phase_zero_environment_rejected() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"TRAFFICFUSE_ENV": "production"}).validate()
