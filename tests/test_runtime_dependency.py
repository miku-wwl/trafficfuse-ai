"""Pinned shared-runtime verification (offline).

These tests are the Phase 0 acceptance evidence that the consumer resolves to
the immutable pinned Git commit — not a moving branch, an adjacent source
checkout, or a look-alike distribution.
"""

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
    path = runtime.module_path()
    assert ".venv" in path.parts, f"expected import from local .venv, got {path}"


def test_public_api_symbols_importable() -> None:
    symbols = runtime.import_public_api()
    assert set(symbols) == set(runtime.PUBLIC_API_SYMBOLS)
    assert len(symbols) == 7


def test_default_execution_budget_is_five_seconds() -> None:
    assert (
        runtime.default_execution_budget_seconds()
        == runtime.TRUSTED_EXECUTION_BUDGET_SECONDS
    )
