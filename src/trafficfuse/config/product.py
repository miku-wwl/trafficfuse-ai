"""Product runtime mode and local model settings (fail closed).

``TRAFFICFUSE_MODE``:

* ``simulation`` (default) — offline: in-process lab services, no Kubernetes.
* ``live`` — the approved disposable ``trafficfuse-lab`` Kind cluster via the
  dedicated kubeconfig/context; requires an explicitly provisioned live
  governance profile.
* ``replay`` — read-only display of recorded evidence; every mutating route is
  disabled and no governance runtime is opened.

The Ollama endpoint must be loopback; nothing here selects credentials,
execution identities, scopes or budgets — those live in reviewed host code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from trafficfuse.config.settings import REPO_ROOT, load_env_file
from trafficfuse.infrastructure.ollama import DEFAULT_ENDPOINT, DEFAULT_MODEL, validate_endpoint

MODES = ("simulation", "live", "replay")


@dataclass(frozen=True)
class ProductSettings:
    mode: str = "simulation"
    ollama_url: str = DEFAULT_ENDPOINT
    ollama_model: str = DEFAULT_MODEL
    state_root: Path = REPO_ROOT / ".runtime" / "trafficfuse"
    artifacts_root: Path = REPO_ROOT / "artifacts"
    replay_file: Path = REPO_ROOT / "demo" / "replay" / "recorded-incident.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProductSettings":
        if env is None:
            load_env_file()
            env = os.environ
        mode = (env.get("TRAFFICFUSE_MODE") or "simulation").strip()
        if mode not in MODES:
            raise ValueError(f"TRAFFICFUSE_MODE must be one of {MODES}, got {mode!r}")
        model = (env.get("TRAFFICFUSE_OLLAMA_MODEL") or DEFAULT_MODEL).strip()
        if not model or len(model) > 64 or any(c.isspace() for c in model):
            raise ValueError("TRAFFICFUSE_OLLAMA_MODEL must be a short model name")
        return cls(
            mode=mode,
            ollama_url=validate_endpoint((env.get("TRAFFICFUSE_OLLAMA_URL") or DEFAULT_ENDPOINT).strip()),
            ollama_model=model,
        )

    def profile_directory(self) -> Path:
        return self.state_root / self.mode / "aig"

    def store_path(self) -> Path:
        return self.state_root / self.mode / "incidents.sqlite3"
