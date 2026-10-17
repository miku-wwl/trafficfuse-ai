"""Environment-driven settings.

A local ``.env`` file at the repository root is loaded automatically when
present (``TRAFFICFUSE_*`` keys, see ``.env.example``). The process environment
always **takes precedence** over ``.env`` values: python-dotenv is used with
``override=False``, so exporting a variable explicitly overrides the file, and
the file never overrides an explicit environment variable. Only a file inside
this repository is loaded; its content is never executed as code.

No credentials are stored in source control. Anything sensitive belongs in the
git-ignored, operator-local ``.env`` or the process environment (AGENTS.md).

Invalid or ambiguous configuration fails closed: unknown environments,
out-of-range ports, malformed cluster/namespace names, a non-standard
Kubernetes context, or a non-dedicated kubeconfig path all raise ``ValueError``
instead of silently falling back to ambient state.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

SUPPORTED_ENVIRONMENTS = ("local", "test")
ENV_FILE_NAME = ".env"
REPO_ROOT = Path(__file__).resolve().parents[3]

# Standardized Phase 1 identity (see docs/PHASE1-PREFLIGHT.md). Anything else
# fails closed — never the ambient context, never a foreign cluster.
STANDARD_CLUSTER = "trafficfuse-lab"
ALLOWED_K8S_CONTEXT = "kind-trafficfuse-lab"
STANDARD_NAMESPACE = "trafficfuse-system"
DEDICATED_KUBECONFIG = ".runtime/kubeconfig/trafficfuse-lab.yaml"

_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MIN_PORT = 1
_MAX_PORT = 65535


def load_env_file(path: Path | None = None) -> bool:
    """Load a ``.env`` file without overriding existing process variables.

    Defaults to ``<repository root>/.env``. Returns ``True`` when a file was
    found and parsed, ``False`` when it is absent (a no-op, not an error).
    """
    candidate = Path(path) if path is not None else REPO_ROOT / ENV_FILE_NAME
    if not candidate.is_file():
        return False
    return bool(load_dotenv(candidate, override=False))


def _str_env(source: Mapping[str, str], key: str, default: str) -> str:
    raw = source.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw


def _int_env(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"invalid integer value for {key}: {raw!r} (expected a port number)"
        ) from exc


def _is_dns1123_label(value: str) -> bool:
    return bool(_DNS1123_LABEL.fullmatch(value)) and len(value) <= 63


def _is_dedicated_kubeconfig(value: str) -> bool:
    candidate = Path(value).expanduser()
    home_config = Path.home() / ".kube" / "config"
    if candidate.resolve() == home_config.resolve():
        return False
    if candidate.name != "trafficfuse-lab.yaml":
        return False
    parts = candidate.parts
    return "kubeconfig" in parts and ".runtime" in parts


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    host: str = "127.0.0.1"
    port: int = 8000
    kind_cluster: str = STANDARD_CLUSTER
    k8s_context: str = ALLOWED_K8S_CONTEXT
    namespace: str = STANDARD_NAMESPACE
    kubeconfig_path: str = DEDICATED_KUBECONFIG
    lab_port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Load settings. With no explicit mapping, the repository ``.env`` is
        loaded first (without overriding real process variables) and the process
        environment is used as the source of truth. The result is validated
        (fail closed) before it is returned."""
        if env is None:
            load_env_file()
            env = os.environ
        settings = cls(
            environment=_str_env(env, "TRAFFICFUSE_ENV", "local"),
            host=_str_env(env, "TRAFFICFUSE_HOST", "127.0.0.1"),
            port=_int_env(env, "TRAFFICFUSE_PORT", 8000),
            kind_cluster=_str_env(env, "TRAFFICFUSE_KIND_CLUSTER", STANDARD_CLUSTER),
            k8s_context=_str_env(env, "TRAFFICFUSE_K8S_CONTEXT", ALLOWED_K8S_CONTEXT),
            namespace=_str_env(env, "TRAFFICFUSE_NAMESPACE", STANDARD_NAMESPACE),
            kubeconfig_path=_str_env(
                env, "TRAFFICFUSE_KUBECONFIG_PATH", DEDICATED_KUBECONFIG
            ),
            lab_port=_int_env(env, "TRAFFICFUSE_LAB_PORT", 8080),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                f"unsupported environment {self.environment!r} for Phase 0; "
                f"expected one of {SUPPORTED_ENVIRONMENTS}"
            )
        for name, value in (("port", self.port), ("lab_port", self.lab_port)):
            if not _MIN_PORT <= value <= _MAX_PORT:
                raise ValueError(
                    f"{name} must be in {_MIN_PORT}..{_MAX_PORT}, got {value!r}"
                )
        if not _is_dns1123_label(self.kind_cluster):
            raise ValueError(
                f"kind_cluster {self.kind_cluster!r} is not a valid DNS-1123 label"
            )
        if self.kind_cluster != STANDARD_CLUSTER:
            raise ValueError(
                f"kind_cluster must be {STANDARD_CLUSTER!r}, got {self.kind_cluster!r}"
            )
        if not _is_dns1123_label(self.namespace):
            raise ValueError(
                f"namespace {self.namespace!r} is not a valid DNS-1123 label"
            )
        if self.namespace != STANDARD_NAMESPACE:
            raise ValueError(
                f"namespace must be {STANDARD_NAMESPACE!r}, got {self.namespace!r}"
            )
        if self.k8s_context != ALLOWED_K8S_CONTEXT:
            raise ValueError(
                f"k8s_context must be {ALLOWED_K8S_CONTEXT!r}, got {self.k8s_context!r}"
            )
        if not _is_dedicated_kubeconfig(self.kubeconfig_path):
            raise ValueError(
                "kubeconfig_path must be the dedicated .runtime/kubeconfig path "
                f"{DEDICATED_KUBECONFIG!r} (never the global kubeconfig), got "
                f"{self.kubeconfig_path!r}"
            )
