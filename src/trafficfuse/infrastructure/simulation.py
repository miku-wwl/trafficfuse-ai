"""Offline simulation lab: in-memory Service + in-process lab workloads.

**No Kubernetes is involved.** This exists for offline development, tests and
a clearly labelled demo fallback. Everything it produces carries the source
label ``OFFLINE_SIMULATION`` (or ``TEST_FIXTURE`` in unit tests) and must never
be presented as live cluster evidence.

What is real here: the HTTP responses (the unmodified lab FastAPI app, served
in-process), the probe and its strict evidence export, the Agentic Intent Gate
runtime and the executor's receipt store. What is simulated: the Service
object, its endpoints and the routing between three in-process "pods". The
selector patch applies JSON-patch ``test`` semantics atomically, like the API
server, so stale-target refusals behave the same way.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from trafficfuse.domain.evidence import (
    SOURCE_SIMULATION,
    ServiceSnapshot,
    summarize_probe_document,
)
from trafficfuse.infrastructure.kube import (
    FIELD_MANAGER,
    HEALTHY_SERVICE,
    LIVE_SERVICE,
    PROBE_CONCURRENCY,
    PROBE_REQUESTS,
    PROBE_SCENARIOS,
    PROBE_TIMEOUT_SECONDS,
    SELECTOR_APP,
    SelectorPatch,
    probe_job_name,
)
from trafficfuse.infrastructure.live_cluster import ProbeRun, utc_now
from trafficfuse.infrastructure.reroute import PatchResponse
from trafficfuse.lab.evidence import render_stdout_document
from trafficfuse.lab.evidence_export import collect_evidence_from_log_bytes
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    EXPECTED_CONTEXT,
    MANAGED_BY_LABEL_KEY,
    NAMESPACE,
    OWNERSHIP_LABEL_KEY,
    OWNERSHIP_LABEL_VALUE,
)
from trafficfuse.lab.probe import ProbeConfig, run_probe
from trafficfuse.lab.service import MODE_FAULTY, MODE_HEALTHY, create_lab_app

_OWNER_LABELS = {
    "app.kubernetes.io/managed-by": OWNERSHIP_LABEL_VALUE,
    "app.kubernetes.io/name": "trafficfuse",
    "app.kubernetes.io/part-of": "trafficfuse",
    OWNERSHIP_LABEL_KEY: OWNERSHIP_LABEL_VALUE,
    "trafficfuse.io/role": "live-entrypoint",
}
assert _OWNER_LABELS[MANAGED_BY_LABEL_KEY] == OWNERSHIP_LABEL_VALUE


class _RoutingTransport(httpx.AsyncBaseTransport):
    """Round-robin requests across in-process pods matching a selector."""

    def __init__(self, lab: "SimulatedLab", service: str) -> None:
        self.lab, self.service = lab, service
        self._cycle = itertools.count()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        version = self.lab.selected_version(self.service)
        pods = [name for name, (pod_version, _) in self.lab.pods.items() if pod_version == version]
        if not pods:
            raise httpx.ConnectError("no ready endpoints for the Service selector", request=request)
        name = pods[next(self._cycle) % len(pods)]
        transport = httpx.ASGITransport(app=self.lab.pods[name][1])
        return await transport.handle_async_request(request)


class SimulatedLab:
    """In-memory ``trafficfuse-live`` routing over in-process lab workloads."""

    def __init__(self, source: str = SOURCE_SIMULATION, *, artifacts_root: Path | None = None) -> None:
        self.source = source
        self.artifacts_root = Path(artifacts_root or "artifacts/simulation")
        self._lock = threading.Lock()
        self.uid = str(uuid4())
        self._resource_version = 635
        self._version = MODE_FAULTY
        self._managers: set[str] = set()
        self.patch_calls = 0
        self.created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.pods = {
            "trafficfuse-faulty-sim-7c9d4-a1b2c": (
                MODE_FAULTY,
                create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50, pod_name="trafficfuse-faulty-sim-7c9d4-a1b2c"),
            ),
            "trafficfuse-faulty-sim-7c9d4-d3e4f": (
                MODE_FAULTY,
                create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50, pod_name="trafficfuse-faulty-sim-7c9d4-d3e4f"),
            ),
            "trafficfuse-healthy-sim-5b8e1-g5h6i": (
                MODE_HEALTHY,
                create_lab_app(mode=MODE_HEALTHY, pod_name="trafficfuse-healthy-sim-5b8e1-g5h6i"),
            ),
        }

    # --- simulated cluster state --------------------------------------------------

    def selected_version(self, service: str) -> str:
        if service == HEALTHY_SERVICE:
            return MODE_HEALTHY
        with self._lock:
            return self._version

    def snapshot(self) -> ServiceSnapshot:
        with self._lock:
            return ServiceSnapshot(
                name=LIVE_SERVICE,
                namespace=NAMESPACE,
                uid=self.uid,
                resource_version=str(self._resource_version),
                selector={"app": SELECTOR_APP, "version": self._version},
                labels=dict(_OWNER_LABELS),
                managed_fields=tuple(sorted(self._managers)),
            )

    def reset(self) -> None:
        """Simulated lab reset (the live equivalent is an operator re-apply)."""
        with self._lock:
            self._version = MODE_FAULTY
            self._resource_version += 1

    def mutate_out_of_band(self) -> None:
        """Test hook: an unrelated writer bumps resourceVersion (stale target)."""
        with self._lock:
            self._resource_version += 1

    # --- ClusterPort ------------------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        return {
            "cluster": CLUSTER_NAME,
            "context": EXPECTED_CONTEXT,
            "namespace": NAMESPACE,
            "api_server": "in-process simulation (no Kubernetes API)",
            "ca_fingerprint_sha256": None,
        }

    async def service(self) -> ServiceSnapshot:
        return self.snapshot()

    async def endpoints(self) -> list[dict[str, Any]]:
        version = self.selected_version(LIVE_SERVICE)
        return [
            {"pod": name, "ip": f"sim-{index}", "ready": True}
            for index, (name, (pod_version, _)) in enumerate(sorted(self.pods.items()))
            if pod_version == version
        ]

    async def deployments(self) -> list[dict[str, Any]]:
        return [
            {
                "name": f"trafficfuse-{version}",
                "version": version,
                "image": "trafficfuse-lab-service (in-process simulation)",
                "replicas": replicas,
                "ready_replicas": replicas,
                "generation": 1,
                "created_at": self.created_at,
                "uid": None,
            }
            for version, replicas in ((MODE_FAULTY, 2), (MODE_HEALTHY, 1))
        ]

    async def pods(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "version": version, "phase": "Running", "ready": True, "restarts": 0, "image_id": None}
            for name, (version, _) in sorted(self.pods.items())
        ]

    async def run_probe(self, scenario: str, *, run_directory: str) -> ProbeRun:
        job_name = probe_job_name(scenario)
        service = PROBE_SCENARIOS[scenario]
        created_at = utc_now()
        evidence = await run_probe(
            ProbeConfig(
                target_url=f"http://{service}.{NAMESPACE}.svc.cluster.local/checkout",
                scenario_id=job_name,
                target_label=f"service:{service}",
                total_requests=PROBE_REQUESTS,
                concurrency=PROBE_CONCURRENCY,
                timeout_seconds=PROBE_TIMEOUT_SECONDS,
            ),
            transport=_RoutingTransport(self, service),
        )
        export = collect_evidence_from_log_bytes(
            render_stdout_document(evidence.to_json()).encode("utf-8"),
            run_id=job_name,
            artifacts_root=self.artifacts_root / run_directory,
        )
        audit = json.loads(export.audit_path.read_text("utf-8"))
        summary = summarize_probe_document(
            export.document,
            probe_id=job_name,
            scenario=scenario,
            target_service=service,
            evidence_sha256=audit["evidence"]["sha256"],
            raw_logs_sha256=audit["raw_logs"]["sha256"],
            artifact_path=export.run_directory.as_posix(),
        )
        return ProbeRun(summary, job_name, created_at, utc_now())

    # --- ServiceMutationPort ----------------------------------------------------------

    async def read_service(self, *, timeout: float) -> ServiceSnapshot:
        return self.snapshot()

    async def patch_selector(self, patch: SelectorPatch, *, timeout: float) -> PatchResponse:
        with self._lock:
            self.patch_calls += 1
            if (
                patch.service_uid != self.uid
                or patch.expected_resource_version != str(self._resource_version)
                or self._version != patch.from_version
            ):
                return PatchResponse(True, None, "JSON patch test failed; no effect (simulated 422)")
            self._version = patch.to_version
            self._resource_version += 1
            self._managers.add(FIELD_MANAGER)
        return PatchResponse(True, self.snapshot(), "patched (simulated)")
