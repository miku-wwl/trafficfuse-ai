"""Pinned shared-runtime verification (offline).

These tests are the Phase 0 acceptance evidence that the consumer resolves to
the immutable pinned Git commit — not a moving branch, an adjacent source
checkout, or a look-alike distribution — and they remain portable across
Windows/Unix and any standard virtual-environment tool.
"""

import sys

from trafficfuse.infrastructure import runtime


def test_installed_distribution_version_is_pinned() -> None:
    assert runtime.installed_version() == runtime.RUNTIME_VERSION == "0.2.0"


def test_resolves_to_pinned_commit() -> None:
    commit = runtime.resolved_git_commit()
    assert commit is not None, (
        "agentic-intent-gate must be installed from the pinned Git source "
        "(direct_url.json is missing)"
    )
    assert commit == runtime.PINNED_COMMIT


def test_imports_from_installed_environment_not_source_checkout() -> None:
    # Portable origin proof (no hardcoded directory names):
    #  1. the imported module resolves inside the active interpreter
    #     environment (sys.prefix) — venv / virtualenv / uv / poetry / conda /
    #     system installs, Windows and Unix alike;
    #  2. it is NOT resolved from this project's own source tree (src/) — no
    #     shared-runtime source was copied into the repository;
    #  3. installed distribution metadata agrees with the import location,
    #     which also rules out an adjacent checkout shadowing the distribution
    #     via PYTHONPATH or an editable install.
    module_file = runtime.module_path()
    assert runtime.imported_from_interpreter_prefix(), (
        f"agentic_intent_gate imported from {module_file}, which is outside the "
        f"active interpreter prefix {sys.prefix}"
    )
    assert not runtime.imported_from_project_source_tree(), (
        f"agentic_intent_gate is being imported from the project source tree: "
        f"{module_file}"
    )
    assert runtime.metadata_location_matches_import(), (
        f"installed distribution location does not match the imported module "
        f"{module_file} — possible sys.path shadowing"
    )


def test_public_api_symbols_importable() -> None:
    symbols = runtime.import_public_api()
    assert set(symbols) == set(runtime.PUBLIC_API_SYMBOLS)
    assert len(symbols) == 7


def test_default_execution_budget_is_five_seconds() -> None:
    assert (
        runtime.default_execution_budget_seconds()
        == runtime.TRUSTED_EXECUTION_BUDGET_SECONDS
    )
