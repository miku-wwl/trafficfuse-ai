"""Live evidence adapter for the dedicated ``trafficfuse-lab`` Kind cluster.

Every command goes through :mod:`trafficfuse.infrastructure.kube` (exact
shapes, dedicated kubeconfig, exact context and namespace). This adapter only
*reads* cluster state and creates probe Jobs from the fixed template; it has no
path to the governed Service mutation, which lives in the AIG-registered
executor. Probe Jobs are never deleted or retried: a failed or incomplete Job
stops the operation and its preserved state is reported.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trafficfuse.domain.evidence import (
    SOURCE_LIVE,
    ProbeSummary,
    ServiceSnapshot,
    service_from_k8s,
    summarize_probe_document,
)
from trafficfuse.infrastructure import kube
from trafficfuse.lab.evidence_export import collect_evidence_from_log_bytes
from trafficfuse.lab.lifecycle import SafetyViolation, load_kubeconfig_document, verify_kubeconfig_document

PROBE_JOB_DEADLINE_SECONDS = 120.0
_POLL_SECONDS = 1.0


class ClusterOperationError(RuntimeError):
    """A cluster read or probe step failed; nothing is retried automatically."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProbeRun:
    summary: ProbeSummary
    job_name: str
    created_at: str
    finished_at: str


class LiveKindCluster:
    """Reads and probe Jobs against the dedicated lab; source ``LIVE_KIND``."""

    source = SOURCE_LIVE

    def __init__(self, artifacts_root: Path) -> None:
        self.artifacts_root = Path(artifacts_root)

    # --- identity --------------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        """Local, offline kubeconfig identity (endpoint + CA fingerprint only)."""
        identity = verify_kubeconfig_document(
            load_kubeconfig_document(kube.canonical_kubeconfig_path())
        )
        return {
            "cluster": identity.cluster,
            "context": identity.context,
            "namespace": kube.NAMESPACE,
            "api_server": identity.api_server,
            "ca_fingerprint_sha256": identity.ca_fingerprint_sha256,
        }

    # --- reads -----------------------------------------------------------------

    async def _read_json(self, shape: tuple[str, ...], what: str) -> Any:
        result = await kube.run_kubectl(kube.read_command(shape), timeout=15)
        if not result.ok:
            raise ClusterOperationError(f"{what} read failed: {kube.redacted_error(result)}")
        try:
            return result.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ClusterOperationError(f"{what} read returned invalid JSON") from exc

    async def service(self) -> ServiceSnapshot:
        return service_from_k8s(await self._read_json(kube.GET_SERVICE, "live Service"))

    async def endpoints(self) -> list[dict[str, Any]]:
        document = await self._read_json(kube.GET_ENDPOINTS, "live Service endpoints")
        rows: list[dict[str, Any]] = []
        for slice_ in document.get("items") or []:
            for endpoint in slice_.get("endpoints") or []:
                target = endpoint.get("targetRef") or {}
                conditions = endpoint.get("conditions") or {}
                rows.append(
                    {
                        "pod": target.get("name"),
                        "ip": (endpoint.get("addresses") or [None])[0],
                        "ready": bool(conditions.get("ready")),
                    }
                )
        return rows

    async def deployments(self) -> list[dict[str, Any]]:
        document = await self._read_json(kube.GET_DEPLOYMENTS, "lab Deployments")
        result = []
        for item in document.get("items") or []:
            metadata, spec, status = item["metadata"], item["spec"], item.get("status") or {}
            container = spec["template"]["spec"]["containers"][0]
            result.append(
                {
                    "name": metadata["name"],
                    "version": spec["template"]["metadata"]["labels"].get("version"),
                    "image": container.get("image"),
                    "replicas": spec.get("replicas", 0),
                    "ready_replicas": status.get("readyReplicas", 0),
                    "generation": metadata.get("generation"),
                    "created_at": metadata.get("creationTimestamp"),
                    "uid": metadata.get("uid"),
                }
            )
        return sorted(result, key=lambda d: d["name"])

    async def pods(self) -> list[dict[str, Any]]:
        document = await self._read_json(kube.GET_PODS, "lab Pods")
        result = []
        for item in document.get("items") or []:
            statuses = (item.get("status") or {}).get("containerStatuses") or [{}]
            result.append(
                {
                    "name": item["metadata"]["name"],
                    "version": (item["metadata"].get("labels") or {}).get("version"),
                    "phase": (item.get("status") or {}).get("phase"),
                    "ready": bool(statuses[0].get("ready")),
                    "restarts": statuses[0].get("restartCount", 0),
                    "image_id": statuses[0].get("imageID"),
                }
            )
        return sorted(result, key=lambda p: p["name"])

    # --- probe Jobs ------------------------------------------------------------

    async def run_probe(self, scenario: str, *, run_directory: str) -> ProbeRun:
        job_name = kube.probe_job_name(scenario)
        manifest = kube.render_probe_job(scenario, job_name)
        created_at = utc_now()
        result = await kube.run_kubectl(
            kube.create_probe_command(),
            timeout=20,
            stdin=json.dumps(manifest).encode("utf-8"),
        )
        if not result.ok:
            raise ClusterOperationError(
                f"probe Job {job_name} creation failed or is ambiguous "
                f"({kube.redacted_error(result)}); not retried"
            )
        deadline = time.monotonic() + PROBE_JOB_DEADLINE_SECONDS
        while True:
            status = await kube.run_kubectl(kube.job_status_command(job_name), timeout=15)
            if status.ok:
                job_status = status.json().get("status") or {}
                if job_status.get("succeeded", 0) >= 1:
                    break
                if job_status.get("failed", 0) >= 1:
                    raise ClusterOperationError(f"probe Job {job_name} failed; preserved for review")
            if time.monotonic() >= deadline:
                raise ClusterOperationError(f"probe Job {job_name} did not complete in time")
            await asyncio.sleep(_POLL_SECONDS)
        logs = await kube.run_kubectl(kube.job_logs_command(job_name), timeout=20)
        if not logs.ok:
            raise ClusterOperationError(f"probe Job {job_name} logs unavailable: {kube.redacted_error(logs)}")
        export = collect_evidence_from_log_bytes(
            logs.stdout,
            run_id=job_name,
            artifacts_root=self.artifacts_root / run_directory,
        )
        audit = json.loads(export.audit_path.read_text("utf-8"))
        summary = summarize_probe_document(
            export.document,
            probe_id=job_name,
            scenario=scenario,
            target_service=kube.PROBE_SCENARIOS[scenario],
            evidence_sha256=audit["evidence"]["sha256"],
            raw_logs_sha256=audit["raw_logs"]["sha256"],
            artifact_path=export.run_directory.as_posix(),
        )
        return ProbeRun(summary, job_name, created_at, utc_now())


def ensure_dedicated_kubeconfig() -> None:
    """Fail closed before any live operation if the dedicated file is absent."""
    path = kube.canonical_kubeconfig_path()
    if not path.is_file():
        raise SafetyViolation(
            "dedicated kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml is missing; "
            "live mode requires the approved trafficfuse-lab cluster"
        )
