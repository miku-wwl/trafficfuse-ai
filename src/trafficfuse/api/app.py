"""FastAPI application factory and health endpoint (Phase 0)."""

from __future__ import annotations

from fastapi import FastAPI

from trafficfuse import __version__
from trafficfuse.infrastructure import runtime as aig_runtime


def create_app() -> FastAPI:
    """Build the TrafficFuse application. Recreated per call for testability."""
    application = FastAPI(
        title="TrafficFuse AI",
        version=__version__,
        summary="Evidence-Governed AI Release Circuit Breaker",
        description=(
            "Consumer application of the pinned Agentic Intent Gate runtime. "
            "Phase 0: health endpoint only."
        ),
    )

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "runtime": aig_runtime.runtime_summary(),
        }

    return application


app = create_app()
