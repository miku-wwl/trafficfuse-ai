"""Integration surface for the pinned Agentic Intent Gate runtime.

Dependency pins and verification helpers. Runtime composition
(``ToolBinding`` / ``ToolCapability`` / ``ExecutableRegistry`` /
``CapabilityVerifier`` / policy / ``open_runtime``) lives in
``trafficfuse.infrastructure.governance``. Model output can never reach these
symbols: they live in trusted host code (AGENTS.md).
"""

from __future__ import annotations

import inspect
import json
import sys
from importlib import metadata
from pathlib import Path

# src/trafficfuse/infrastructure/runtime.py -> parents: [0]=infrastructure,
# [1]=trafficfuse, [2]=src, [3]=repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_SOURCE_ROOT = PROJECT_ROOT / "src"

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


def imported_from_interpreter_prefix() -> bool:
    """True when the runtime resolves inside the active interpreter environment.

    Portable across venv / virtualenv / uv / poetry / conda / system installs
    on Windows and Unix: the check uses ``sys.prefix``, never a hardcoded
    directory name such as ``.venv``.
    """
    return module_path().is_relative_to(Path(sys.prefix).resolve())


def imported_from_project_source_tree() -> bool:
    """True when the runtime resolves inside this project's own source tree
    (``src/``) — i.e. shared-runtime source was copied into this repository.

    The check deliberately targets the source tree, not the repository
    directory as a whole: a local virtualenv lives inside the repository but is
    not copied source. Shadowing from an *adjacent* checkout outside the
    project is caught by :func:`metadata_location_matches_import`.
    """
    return module_path().is_relative_to(PROJECT_SOURCE_ROOT)


def metadata_location_matches_import() -> bool:
    """Distribution metadata and the imported module agree on location.

    Guards against ``sys.path`` shadowing, where the import succeeds from one
    location (for example an adjacent shared-runtime checkout placed on
    ``PYTHONPATH``) while installed distribution metadata reports another.
    """
    distribution = metadata.distribution(RUNTIME_DISTRIBUTION)
    site_dir = Path(distribution.locate_file("")).resolve()
    # module_path() is <site-packages>/agentic_intent_gate/__init__.py;
    # parents[1] is the directory that contains the package (site-packages).
    return module_path().parents[1] == site_dir


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
