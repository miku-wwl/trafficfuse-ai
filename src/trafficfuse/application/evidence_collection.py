"""Collect one provenance-labelled evidence bundle and assess degradation.

The detection policy is deliberately simple and explicit (host-owned, never
model-chosen): the live Service is *degraded* when the measured live error
rate is at least ``DEGRADED_ERROR_PERCENT``; remediation is *justified* only
when, in the same bundle, the healthy-version baseline is itself healthy and
the live selector does not already point at the healthy version.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from trafficfuse.domain.evidence import (
    EvidenceBundle,
    ServiceSnapshot,
    deployment_item,
    probe_item,
    routing_item,
    service_item,
)
from trafficfuse.infrastructure.live_cluster import ProbeRun
from trafficfuse.infrastructure.reroute import EvidenceAnchor

DEGRADED_ERROR_PERCENT = 5.0
BASELINE_HEALTHY_ERROR_PERCENT = 1.0
# Added after the first live run, where approval arrived ~2 h after detection:
# live preconditions still held, but evidence that old should not justify an
# intervention. Older bundles are refused before dispatch (no effect).
MAX_EVIDENCE_AGE_SECONDS = 30 * 60


class ClusterPort(Protocol):
    source: str

    def identity(self) -> dict[str, Any]: ...
    async def service(self) -> ServiceSnapshot: ...
    async def endpoints(self) -> list[dict[str, Any]]: ...
    async def deployments(self) -> list[dict[str, Any]]: ...
    async def pods(self) -> list[dict[str, Any]]: ...
    async def run_probe(self, scenario: str, *, run_directory: str) -> ProbeRun: ...


@dataclass(frozen=True)
class Assessment:
    degraded: bool
    baseline_healthy: bool
    remediation_justified: bool
    live_error_percent: float | None
    baseline_error_percent: float | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "degraded": self.degraded,
            "baseline_healthy": self.baseline_healthy,
            "remediation_justified": self.remediation_justified,
            "live_error_percent": self.live_error_percent,
            "baseline_error_percent": self.baseline_error_percent,
            "reasons": list(self.reasons),
            "policy": {
                "degraded_error_percent": DEGRADED_ERROR_PERCENT,
                "baseline_healthy_error_percent": BASELINE_HEALTHY_ERROR_PERCENT,
            },
        }


def assess(bundle: EvidenceBundle) -> Assessment:
    live, baseline, service = bundle.probe("live"), bundle.probe("baseline"), bundle.service()
    reasons = []
    degraded = live is not None and live.observed_error_percent >= DEGRADED_ERROR_PERCENT
    reasons.append(
        f"live error rate {live.observed_error_percent:g}% "
        f"{'>=' if degraded else '<'} {DEGRADED_ERROR_PERCENT:g}% threshold"
        if live
        else "no live probe"
    )
    baseline_ok = (
        baseline is not None
        and baseline.observed_error_percent < BASELINE_HEALTHY_ERROR_PERCENT
        and baseline.served_versions.get("healthy", 0) == baseline.total_requests
    )
    reasons.append(
        f"healthy baseline {baseline.observed_error_percent:g}% errors, "
        f"{'all' if baseline_ok else 'not all'} served by version=healthy"
        if baseline
        else "no healthy baseline"
    )
    routable = service is not None and service.selector.get("version") not in (None, "healthy")
    if service is not None:
        reasons.append(f"live selector version={service.selector.get('version')}")
    return Assessment(
        degraded=degraded,
        baseline_healthy=baseline_ok,
        remediation_justified=bool(degraded and baseline_ok and routable),
        live_error_percent=live.observed_error_percent if live else None,
        baseline_error_percent=baseline.observed_error_percent if baseline else None,
        reasons=tuple(reasons),
    )


def anchor_for(bundle: EvidenceBundle, *, now: datetime | None = None) -> EvidenceAnchor | None:
    service = bundle.service()
    if service is None:
        return None
    age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(bundle.collected_at)
    return EvidenceAnchor(
        digest=bundle.digest,
        source=bundle.source,
        service_uid=service.uid,
        resource_version=service.resource_version,
        from_version=service.selector.get("version", ""),
        remediation_justified=assess(bundle).remediation_justified,
        stale=age.total_seconds() > MAX_EVIDENCE_AGE_SECONDS,
    )


async def collect_bundle(cluster: ClusterPort, *, bundle_id: str) -> EvidenceBundle:
    """Probe the live Service and the healthy baseline, then snapshot routing."""
    live_run, baseline_run = await asyncio.gather(
        cluster.run_probe("live", run_directory=bundle_id),
        cluster.run_probe("baseline", run_directory=bundle_id),
    )
    # Routing is read after the probes so the snapshot (and its resourceVersion,
    # which becomes the Plan precondition) is the freshest observation.
    service, endpoints, deployments = await asyncio.gather(
        cluster.service(), cluster.endpoints(), cluster.deployments()
    )
    observed = datetime.now(timezone.utc).isoformat()
    source = cluster.source
    items = [
        probe_item("EV-1", source, live_run.summary),
        probe_item("EV-2", source, baseline_run.summary),
        service_item("EV-3", source, observed, service),
        routing_item("EV-4", source, observed, endpoints),
    ]
    for index, deployment in enumerate(deployments, start=5):
        items.append(deployment_item(f"EV-{index}", source, observed, deployment))
    return EvidenceBundle(
        bundle_id=bundle_id,
        source=source,
        collected_at=observed,
        cluster=cluster.identity(),
        items=tuple(items),
    )


async def collect_verification(cluster: ClusterPort, *, bundle_id: str) -> EvidenceBundle:
    """Post-intervention evidence: the same live Service, probed again."""
    verify_run = await cluster.run_probe("verify", run_directory=bundle_id)
    service, endpoints = await asyncio.gather(cluster.service(), cluster.endpoints())
    observed = datetime.now(timezone.utc).isoformat()
    source = cluster.source
    return EvidenceBundle(
        bundle_id=bundle_id,
        source=source,
        collected_at=observed,
        cluster=cluster.identity(),
        items=(
            probe_item("EV-1", source, verify_run.summary),
            service_item("EV-2", source, observed, service),
            routing_item("EV-3", source, observed, endpoints),
        ),
    )
