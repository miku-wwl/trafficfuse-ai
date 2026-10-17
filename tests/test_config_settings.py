"""Settings smoke tests (offline, credential-free).

Covers defaults, environment overrides, `.env` file loading, process-environment
precedence over `.env`, missing files, empty values, invalid values, and the
Phase 1A fail-closed identity validation (cluster / context / namespace /
dedicated kubeconfig / port ranges).
"""

from pathlib import Path

import pytest

from trafficfuse.config import Settings, load_env_file

_ENV_KEYS = (
    "TRAFFICFUSE_ENV",
    "TRAFFICFUSE_HOST",
    "TRAFFICFUSE_PORT",
    "TRAFFICFUSE_KIND_CLUSTER",
    "TRAFFICFUSE_K8S_CONTEXT",
    "TRAFFICFUSE_NAMESPACE",
    "TRAFFICFUSE_KUBECONFIG_PATH",
    "TRAFFICFUSE_LAB_PORT",
)

DEDICATED_KUBECONFIG = ".runtime/kubeconfig/trafficfuse-lab.yaml"


def test_defaults_are_local_standardized_and_disposable() -> None:
    settings = Settings.from_env({})
    assert settings.environment == "local"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.kind_cluster == "trafficfuse-lab"
    assert settings.k8s_context == "kind-trafficfuse-lab"
    assert settings.namespace == "trafficfuse-system"
    assert settings.kubeconfig_path == DEDICATED_KUBECONFIG
    assert settings.lab_port == 8080


def test_environment_overrides() -> None:
    settings = Settings.from_env({"TRAFFICFUSE_ENV": "test", "TRAFFICFUSE_PORT": "9000"})
    assert settings.environment == "test"
    assert settings.port == 9000


def test_invalid_port_reports_clear_error() -> None:
    with pytest.raises(ValueError, match="invalid integer value for TRAFFICFUSE_PORT"):
        Settings.from_env({"TRAFFICFUSE_PORT": "not-a-number"})


def test_port_range_bounds() -> None:
    assert Settings.from_env({"TRAFFICFUSE_PORT": "1"}).port == 1
    assert Settings.from_env({"TRAFFICFUSE_LAB_PORT": "65535"}).lab_port == 65535
    for bad in ("0", "65536", "-1", "99999"):
        with pytest.raises(ValueError, match="must be in 1..65535"):
            Settings.from_env({"TRAFFICFUSE_PORT": bad})


def test_non_standard_cluster_rejected() -> None:
    with pytest.raises(ValueError, match="kind_cluster"):
        Settings.from_env({"TRAFFICFUSE_KIND_CLUSTER": "incident-lab"})
    with pytest.raises(ValueError, match="kind_cluster"):
        Settings.from_env({"TRAFFICFUSE_KIND_CLUSTER": "Bad_Cluster"})


def test_non_standard_namespace_rejected() -> None:
    with pytest.raises(ValueError, match="namespace"):
        Settings.from_env({"TRAFFICFUSE_NAMESPACE": "default"})


def test_non_standard_context_rejected() -> None:
    with pytest.raises(ValueError, match="k8s_context"):
        Settings.from_env({"TRAFFICFUSE_K8S_CONTEXT": "kind-playground-crd"})


def test_global_kubeconfig_rejected() -> None:
    global_config = str(Path.home() / ".kube" / "config")
    with pytest.raises(ValueError, match="kubeconfig"):
        Settings.from_env({"TRAFFICFUSE_KUBECONFIG_PATH": global_config})


def test_non_dedicated_kubeconfig_rejected() -> None:
    with pytest.raises(ValueError, match="kubeconfig"):
        Settings.from_env({"TRAFFICFUSE_KUBECONFIG_PATH": ".runtime/kubeconfig/other.yaml"})
    with pytest.raises(ValueError, match="kubeconfig"):
        Settings.from_env({"TRAFFICFUSE_KUBECONFIG_PATH": "kubeconfigs/trafficfuse-lab.yaml"})


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
    with pytest.raises(ValueError, match="unsupported environment"):
        Settings.from_env({"TRAFFICFUSE_ENV": "production"})
