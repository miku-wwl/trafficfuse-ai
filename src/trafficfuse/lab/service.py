"""Synthetic lab workload: one FastAPI service in two modes.

``healthy``: ``/ready`` 200, ``/version`` identifies the healthy version,
``/checkout`` returns a synthetic success (HTTP 200).

``faulty``: ``/ready`` still returns 200 so the Pod remains a valid Service
endpoint, ``/version`` identifies the faulty version, and ``/checkout`` returns
HTTP 500 for a deterministic portion of requests (see ``fault.py``).

Determinism under load: the probe sends ``X-TrafficFuse-Request-Index`` with
its own sequence number. When present, the fault decision follows that index,
so the exact configured distribution holds globally even when the Service
load-balances across several faulty Pods. Any other client (or a missing /
invalid header) falls back to a per-process counter, which yields the exact
distribution only for single-observation sequences; this fallback is a
degradation of *global* exactness, never of determinism.

All responses are explicitly labelled synthetic. The checkout operation never
touches a real payment or business system. Run in-cluster with::

    TRAFFICFUSE_LAB_MODE=faulty TRAFFICFUSE_LAB_FAULT_RATE_PERCENT=50 \\
        python -m trafficfuse.lab.service
"""

from __future__ import annotations

import itertools
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from trafficfuse import __version__
from trafficfuse.lab.fault import (
    DEFAULT_FAULT_RATE_PERCENT,
    REQUEST_INDEX_HEADER,
    FaultPolicy,
    _MAX_REQUEST_NUMBER,
)

MODE_HEALTHY = "healthy"
MODE_FAULTY = "faulty"
MODES = (MODE_HEALTHY, MODE_FAULTY)
DEFAULT_LAB_PORT = 8080


def _parse_request_index(raw: str | None) -> int | None:
    """Parse an untrusted request-index header value; None when unusable.

    Only a plain decimal integer within the policy's supported range is
    accepted; anything else (missing, empty, non-numeric, negative, oversized)
    is ignored so the caller can fall back to the process counter.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text.isdigit():
        return None
    try:
        value = int(text)
    except ValueError:  # pragma: no cover - isdigit() already guarantees this
        return None
    if not 1 <= value <= _MAX_REQUEST_NUMBER:
        return None
    return value


def create_lab_app(*, mode: str = MODE_HEALTHY, fault_rate_percent: int = DEFAULT_FAULT_RATE_PERCENT) -> FastAPI:
    """Build the lab workload application for the given mode."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not 0 <= fault_rate_percent <= 100:
        raise ValueError(
            f"fault_rate_percent must be 0..100, got {fault_rate_percent!r}"
        )

    effective_rate = fault_rate_percent if mode == MODE_FAULTY else 0
    policy = FaultPolicy(rate_percent=effective_rate)
    counter = itertools.count(1)

    app = FastAPI(
        title="TrafficFuse Lab Service",
        version=__version__,
        summary="Synthetic failure-lab workload (Phase 1A)",
    )

    @app.get("/ready")
    async def ready():
        # Always 200: even the faulty version must remain a valid endpoint.
        return {"status": "ready", "version": mode}

    @app.get("/version")
    async def version():
        return {
            "component": "trafficfuse-lab-service",
            "version": mode,
            "platform_version": __version__,
            "workload": "synthetic",
        }

    @app.get("/checkout")
    async def checkout(request: Request):
        index = _parse_request_index(request.headers.get(REQUEST_INDEX_HEADER))
        sequence_source = "header"
        if index is None:
            # Degraded global exactness: per-process counter. Still fully
            # deterministic for any single observation sequence.
            index = next(counter)
            sequence_source = "local-counter"
        if policy.should_fail(index):
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "synthetic": True,
                    "version": mode,
                    "request_number": index,
                    "request_sequence": sequence_source,
                    "detail": "synthetic faulty checkout (deterministic fault injection)",
                },
            )
        return {
            "status": "ok",
            "synthetic": True,
            "version": mode,
            "request_number": index,
            "request_sequence": sequence_source,
            "transaction_id": f"synthetic-checkout-{index}",
            "detail": "synthetic checkout success; no real payment system was touched",
        }

    return app


def _main() -> None:
    """Container/local entrypoint driven by environment variables."""
    import uvicorn

    mode = os.environ.get("TRAFFICFUSE_LAB_MODE", MODE_HEALTHY)
    rate = int(os.environ.get("TRAFFICFUSE_LAB_FAULT_RATE_PERCENT", str(DEFAULT_FAULT_RATE_PERCENT)))
    port = int(os.environ.get("TRAFFICFUSE_LAB_PORT", str(DEFAULT_LAB_PORT)))
    uvicorn.run(create_lab_app(mode=mode, fault_rate_percent=rate), host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover - exercised via container command
    _main()
