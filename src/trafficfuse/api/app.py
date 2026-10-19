"""FastAPI application: incident command-center API + dashboard hosting.

Trust boundaries: HTTP input is untrusted. Mutating routes are accepted only
from loopback clients, only with the ``X-TrafficFuse-Client`` header (a custom
header forces a CORS preflight, which this server never grants, so a hostile
web page cannot forge requests — CSRF), and never in ``replay`` mode. The Host
header must be on the allowlist (``TRAFFICFUSE_ALLOWED_HOSTS``; loopback names by
default), defeating DNS-rebinding reads. Execution capability
tokens arrive per request in the ``Authorization`` header and are never
returned, logged or stored. The governance runtime is opened once in the
lifespan and held for the process lifetime (AIG single-owner fence).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from trafficfuse import __version__
from trafficfuse.application.incidents import REDTEAM_SCENARIOS, OperationRefused
from trafficfuse.domain.evidence import SOURCE_DESCRIPTIONS
from trafficfuse.infrastructure import runtime as aig_runtime

DASHBOARD_DIST = Path(__file__).resolve().parents[3] / "dashboard" / "dist"
CLIENT_HEADER = "x-trafficfuse-client"
DEFAULT_ALLOWED_HOSTS = "127.0.0.1,localhost,testserver"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,47}$")


class ApproveRequest(BaseModel):
    plan_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    approver: str = Field(min_length=1, max_length=48)
    reason: str | None = Field(default=None, max_length=512)


class RejectRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=48)
    reason: str | None = Field(default=None, max_length=512)


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host in ("testclient", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_app(*, service: Any = None, meta: dict[str, Any] | None = None, manage_runtime: bool = True) -> FastAPI:
    """Build the app. Tests inject ``service``/``meta``; servers open the runtime."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with AsyncExitStack() as stack:
            if service is None and manage_runtime:
                from trafficfuse.api.composition import open_product

                built, built_meta = await stack.enter_async_context(open_product())
                application.state.service, application.state.meta = built, built_meta
            yield
            if application.state.service is not None and hasattr(application.state.service, "drain"):
                await application.state.service.drain()

    application = FastAPI(
        title="TrafficFuse AI",
        version=__version__,
        summary="Evidence-Governed AI Release Circuit Breaker",
        description=(
            "Consumer application of the pinned Agentic Intent Gate runtime: evidence-grounded "
            "AI diagnosis, governed Kubernetes traffic remediation and independent recovery proof."
        ),
        lifespan=lifespan,
    )
    application.state.service = service
    application.state.meta = meta or {"mode": "uninitialized"}

    def svc(request: Request):
        current = request.app.state.service
        if current is None:
            raise HTTPException(503, {"code": "NOT_READY", "message": "service is not initialized"})
        return current

    def guard_mutation(request: Request) -> None:
        if request.app.state.meta.get("mode") == "replay":
            raise HTTPException(403, {"code": "REPLAY_READ_ONLY", "message": "replay mode is read-only"})
        if not _is_loopback(request):
            raise HTTPException(403, {"code": "LOOPBACK_ONLY", "message": "mutating operations are local-only"})

    @application.exception_handler(OperationRefused)
    async def refused(request: Request, exc: OperationRefused):
        return JSONResponse({"detail": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @application.middleware("http")
    async def mutation_boundary(request: Request, call_next):
        # Runs before routing and body validation, so read-only replay and the
        # loopback-only rule never depend on a route remembering its guard.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.app.state.meta.get("mode") == "replay":
                return JSONResponse(
                    {"detail": {"code": "REPLAY_READ_ONLY", "message": "replay mode is read-only"}}, status_code=403
                )
            if not _is_loopback(request):
                return JSONResponse(
                    {"detail": {"code": "LOOPBACK_ONLY", "message": "mutating operations are local-only"}},
                    status_code=403,
                )
            if not request.headers.get(CLIENT_HEADER):
                return JSONResponse(
                    {"detail": {"code": "CLIENT_HEADER_REQUIRED", "message": "missing X-TrafficFuse-Client header"}},
                    status_code=403,
                )
        return await call_next(request)

    allowed_hosts = [
        host.strip()
        for host in os.environ.get("TRAFFICFUSE_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
        if host.strip()
    ]
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @application.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "version": __version__, "runtime": aig_runtime.runtime_summary()}

    @application.get("/api/meta")
    def get_meta(request: Request) -> dict[str, Any]:
        return request.app.state.meta | {"sources": SOURCE_DESCRIPTIONS, "version": __version__}

    @application.get("/api/incidents")
    def list_incidents(request: Request) -> list[dict[str, Any]]:
        return svc(request).summaries()

    @application.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: str, request: Request) -> dict[str, Any]:
        return svc(request).view(incident_id)

    @application.post("/api/incidents")
    async def detect(request: Request) -> dict[str, Any]:
        guard_mutation(request)
        return await svc(request).detect()

    @application.post("/api/incidents/{incident_id}/diagnose")
    async def diagnose(incident_id: str, request: Request) -> dict[str, Any]:
        guard_mutation(request)
        return svc(request).start_diagnosis(incident_id)

    @application.post("/api/incidents/{incident_id}/approve")
    def approve(incident_id: str, body: ApproveRequest, request: Request) -> dict[str, Any]:
        guard_mutation(request)
        if not _NAME.fullmatch(body.approver):
            raise HTTPException(422, {"code": "INVALID_APPROVER", "message": "approver must be a short name"})
        return svc(request).approve(
            incident_id, fingerprint=body.plan_fingerprint, approver=body.approver, reason=body.reason
        )

    @application.post("/api/incidents/{incident_id}/reject")
    def reject(incident_id: str, body: RejectRequest, request: Request) -> dict[str, Any]:
        guard_mutation(request)
        if not _NAME.fullmatch(body.approver):
            raise HTTPException(422, {"code": "INVALID_APPROVER", "message": "approver must be a short name"})
        return svc(request).reject(incident_id, approver=body.approver, reason=body.reason)

    @application.post("/api/incidents/{incident_id}/authorize")
    def authorize(incident_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        guard_mutation(request)
        return svc(request).authorize(incident_id, authorization)

    @application.post("/api/incidents/{incident_id}/execute")
    async def execute(incident_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        guard_mutation(request)
        return await svc(request).execute(incident_id, authorization)

    @application.post("/api/incidents/{incident_id}/verify")
    async def verify(incident_id: str, request: Request) -> dict[str, Any]:
        guard_mutation(request)
        return svc(request).start_verification(incident_id)

    @application.get("/api/refusals")
    def refusals(request: Request) -> list[dict[str, Any]]:
        return svc(request).store.refusals()

    @application.post("/api/redteam/{scenario}")
    async def redteam(scenario: str, request: Request) -> dict[str, Any]:
        guard_mutation(request)
        return await svc(request).redteam(scenario)

    @application.get("/api/redteam")
    def redteam_catalog() -> dict[str, Any]:
        return REDTEAM_SCENARIOS

    @application.get("/api/audit")
    def audit(request: Request) -> dict[str, Any]:
        service_ = svc(request)
        receipts = service_.gov.receipts.records() if getattr(service_, "gov", None) else []
        return {
            "events": service_.store.events(),
            "receipts": [record.to_dict() for record in receipts],
        }

    @application.post("/api/lab/reset")
    def lab_reset(request: Request) -> dict[str, Any]:
        guard_mutation(request)
        service_ = svc(request)
        if request.app.state.meta.get("mode") != "simulation":
            raise HTTPException(
                403,
                {"code": "OPERATOR_ONLY", "message": "live lab reset is an operator kubectl action, not an API"},
            )
        service_.cluster.reset()
        service_.store.add_event(
            None, stage="LAB_RESET", actor="operator", outcome="reset",
            summary="Simulated lab reset: live selector back to version=faulty", detail={},
        )
        return {"status": "reset"}

    if DASHBOARD_DIST.is_dir():
        application.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")

    return application


app = create_app()


def describe(value: Any) -> str:  # pragma: no cover - debugging helper
    return json.dumps(value, indent=2, default=str)
