"""Environment-driven settings.

Phase 0 settings are local-development only and contain no credentials.
Anything sensitive (future kubeconfigs, model-provider credentials) belongs
in operator-local, gitignored configuration — never in source control
(AGENTS.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SUPPORTED_ENVIRONMENTS = ("local", "test")


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    host: str = "127.0.0.1"
    port: int = 8000
    kind_cluster: str = "trafficfuse-local"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(
            environment=source.get("TRAFFICFUSE_ENV", "local"),
            host=source.get("TRAFFICFUSE_HOST", "127.0.0.1"),
            port=int(source.get("TRAFFICFUSE_PORT", "8000")),
            kind_cluster=source.get("TRAFFICFUSE_KIND_CLUSTER", "trafficfuse-local"),
        )

    def validate(self) -> None:
        if self.environment not in SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                f"unsupported environment {self.environment!r} for Phase 0; "
                f"expected one of {SUPPORTED_ENVIRONMENTS}"
            )
