"""Environment-driven settings.

A local ``.env`` file at the repository root is loaded automatically when
present (``TRAFFICFUSE_*`` keys, see ``.env.example``). The process environment
always **takes precedence** over ``.env`` values: python-dotenv is used with
``override=False``, so exporting a variable explicitly overrides the file, and
the file never overrides an explicit environment variable. Only a file inside
this repository is loaded; its content is never executed as code.

No credentials are stored in source control. Anything sensitive belongs in the
git-ignored, operator-local ``.env`` or the process environment (AGENTS.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

SUPPORTED_ENVIRONMENTS = ("local", "test")
ENV_FILE_NAME = ".env"
REPO_ROOT = Path(__file__).resolve().parents[3]


def load_env_file(path: Path | None = None) -> bool:
    """Load a ``.env`` file without overriding existing process variables.

    Defaults to ``<repository root>/.env``. Returns ``True`` when a file was
    found and parsed, ``False`` when it is absent (a no-op, not an error).
    """
    candidate = Path(path) if path is not None else REPO_ROOT / ENV_FILE_NAME
    if not candidate.is_file():
        return False
    return bool(load_dotenv(candidate, override=False))


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


def _str_env(source: Mapping[str, str], key: str, default: str) -> str:
    raw = source.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    host: str = "127.0.0.1"
    port: int = 8000
    kind_cluster: str = "trafficfuse-local"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Load settings. With no explicit mapping, the repository ``.env`` is
        loaded first (without overriding real process variables) and the process
        environment is used as the source of truth."""
        if env is None:
            load_env_file()
            env = os.environ
        return cls(
            environment=_str_env(env, "TRAFFICFUSE_ENV", "local"),
            host=_str_env(env, "TRAFFICFUSE_HOST", "127.0.0.1"),
            port=_int_env(env, "TRAFFICFUSE_PORT", 8000),
            kind_cluster=_str_env(env, "TRAFFICFUSE_KIND_CLUSTER", "trafficfuse-local"),
        )

    def validate(self) -> None:
        if self.environment not in SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                f"unsupported environment {self.environment!r} for Phase 0; "
                f"expected one of {SUPPORTED_ENVIRONMENTS}"
            )
