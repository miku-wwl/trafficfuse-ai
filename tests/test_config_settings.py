"""Settings smoke tests (offline, credential-free).

Covers defaults, environment overrides, `.env` file loading, process-environment
precedence over `.env`, missing files, empty values and invalid values.
"""

import pytest

from trafficfuse.config import Settings, load_env_file

_ENV_KEYS = (
    "TRAFFICFUSE_ENV",
    "TRAFFICFUSE_HOST",
    "TRAFFICFUSE_PORT",
    "TRAFFICFUSE_KIND_CLUSTER",
)


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


def test_invalid_port_reports_clear_error() -> None:
    with pytest.raises(ValueError, match="invalid integer value for TRAFFICFUSE_PORT"):
        Settings.from_env({"TRAFFICFUSE_PORT": "not-a-number"})


def test_empty_value_falls_back_to_default() -> None:
    settings = Settings.from_env({"TRAFFICFUSE_PORT": "", "TRAFFICFUSE_ENV": ""})
    assert settings.port == 8000
    assert settings.environment == "local"


def test_missing_env_file_is_a_noop(tmp_path) -> None:
    assert load_env_file(tmp_path / "does-not-exist.env") is False


def test_env_file_loads_and_process_env_takes_precedence(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TRAFFICFUSE_ENV=test\nTRAFFICFUSE_PORT=8123\nTRAFFICFUSE_HOST=10.0.0.5\n",
        encoding="utf-8",
    )
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert load_env_file(env_file) is True
    settings = Settings.from_env()
    assert settings.environment == "test"
    assert settings.port == 8123
    assert settings.host == "10.0.0.5"

    # An explicit process environment variable always overrides the .env file.
    monkeypatch.setenv("TRAFFICFUSE_PORT", "9999")
    assert Settings.from_env().port == 9999


def test_non_phase_zero_environment_rejected() -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"TRAFFICFUSE_ENV": "production"}).validate()
