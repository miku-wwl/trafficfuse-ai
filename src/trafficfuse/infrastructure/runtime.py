"""Integration surface for the pinned Agentic Intent Gate runtime.

Phase 0 scope: dependency pins and verification helpers only.

Runtime composition (``ToolBinding`` / ``ToolCapability`` /
``ExecutableRegistry`` / ``CapabilityVerifier`` / policy / ``open_runtime``)
is Phase 3 work and is deliberately absent here. Model output can never
reach these symbols: they live in trusted host code (AGENTS.md).
"""

from __future__ import annotations

import inspect
import json
from importlib import metadata
from pathlib import Path

# --- Immutable pins (must stay in sync with pyproject.toml) -------------------

RUNTIME_DISTRIBUTION = "agentic-intent-gate"
RUNTIME_VERSION = "0.2.0"
RUNTIME_PUBLIC_MODULE = "agentic_intent_gate.runtime"
PINNED_COMMIT = "7261fdd2f47d2b15fabe8faac527eeee37338eb1"
PINNED_SOURCE_URL = "https://github.com/miku-wwl/agentic-intent-gate.git"
TRUSTED_EXECUTION_BUDGET_SECONDS = 5  # default profile; 15s profile is a later reviewed change

PUBLIC_API_SYMBOLS = (
    "ToolBinding",
    "ToolCapability",
    "ExecutableRegistry",
    "CapabilityVerifier",
    "BoundCapabilityAuthorization",
    "execution_budget_binding",
    "open_runtime",
)


def installed_version() -> str:
    """Version of the *installed* agentic-intent-gate distribution."""
    return metadata.version(RUNTIME_DISTRIBUTION)


def resolved_git_commit() -> str | None:
    """Commit recorded in the installed distribution (PEP 610 ``direct_url.json``).

    Returns ``None`` when the distribution was not installed from a Git source.
    """
    distribution = metadata.distribution(RUNTIME_DISTRIBUTION)
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return None
    info = json.loads(raw)
    return info.get("vcs_info", {}).get("commit_id")


def module_path() -> Path:
    """Filesystem location the runtime is imported from (must be inside .venv)."""
    module = __import__(RUNTIME_DISTRIBUTION.replace("-", "_"))
    return Path(module.__file__).resolve()


def import_public_api() -> dict[str, object]:
    """Import and return the supported public integration symbols."""
    module = __import__(RUNTIME_PUBLIC_MODULE, fromlist=list(PUBLIC_API_SYMBOLS))
    return {name: getattr(module, name) for name in PUBLIC_API_SYMBOLS}


def default_execution_budget_seconds() -> int:
    """Default trusted adapter budget declared by ``open_runtime`` (must be 5)."""
    symbols = import_public_api()
    parameter = inspect.signature(symbols["open_runtime"]).parameters[
        "execution_timeout_seconds"
    ]
    return int(parameter.default)


def runtime_summary() -> dict[str, object]:
    """Compact runtime identity for the health endpoint and verification reports."""
    return {
        "distribution": RUNTIME_DISTRIBUTION,
        "version": installed_version(),
        "public_module": RUNTIME_PUBLIC_MODULE,
        "trusted_execution_budget_seconds": TRUSTED_EXECUTION_BUDGET_SECONDS,
    }
