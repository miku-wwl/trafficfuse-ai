"""Incident evidence: provenance-labelled, content-addressed, citeable.

Every item carries an ``evidence_id`` (``EV-1`` …) that the AI must cite, the
``source`` it came from, the observation time and a SHA-256 over its
canonical content. The bundle digest covers all items, so a Plan that carries
``evidence_digest`` is bound to exactly the evidence a human reviewed.

Source labels (AGENTS.md: synthetic data is never presented as real):

* ``LIVE_KIND``           observed now in the dedicated disposable Kind lab;
                          the *workload* is still the synthetic lab service.
* ``OFFLINE_SIMULATION``  in-process lab apps behind an in-memory Service —
                          no Kubernetes involved; development/demo fallback.
* ``RECORDED_REPLAY``     previously recorded ``LIVE_KIND`` evidence shown
                          read-only; nothing is re-measured.
* ``TEST_FIXTURE``        unit-test data only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

SOURCE_LIVE = "LIVE_KIND"
SOURCE_SIMULATION = "OFFLINE_SIMULATION"
SOURCE_REPLAY = "RECORDED_REPLAY"
SOURCE_FIXTURE = "TEST_FIXTURE"
SOURCES = (SOURCE_LIVE, SOURCE_SIMULATION, SOURCE_REPLAY, SOURCE_FIXTURE)

SOURCE_DESCRIPTIONS = {
    SOURCE_LIVE: "Observed live in the disposable local Kind lab (synthetic lab workload).",
    SOURCE_SIMULATION: "Offline simulation: in-process lab services, no Kubernetes involved.",
    SOURCE_REPLAY: "Recorded live-Kind evidence replayed read-only; nothing re-measured.",
    SOURCE_FIXTURE: "Unit-test fixture data.",
}

BUNDLE_SCHEMA = "trafficfuse-evidence-bundle-v1"
_EVIDENCE_ID = re.compile(r"^EV-[1-9][0-9]?$")


class EvidenceError(ValueError):
    """Raised when evidence is malformed or its provenance is inconsistent."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ServiceSnapshot:
    name: str
    namespace: str
    uid: str
    resource_version: str
    selector: Mapping[str, str]
    labels: Mapping[str, str]
    managed_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "uid": self.uid,
            "resource_version": self.resource_version,
            "selector": dict(self.selector),
            "labels": dict(self.labels),
            "managed_fields": list(self.managed_fields),
        }


def service_from_k8s(document: Any) -> ServiceSnapshot:
    """Strictly parse a Service object as returned by the API server."""
    try:
        metadata = document["metadata"]
        spec = document["spec"]
        selector = spec.get("selector") or {}
        labels = metadata.get("labels") or {}
        snapshot = ServiceSnapshot(
            name=metadata["name"],
            namespace=metadata["namespace"],
            uid=metadata["uid"],
            resource_version=metadata["resourceVersion"],
            selector={str(k): str(v) for k, v in selector.items()},
            labels={str(k): str(v) for k, v in labels.items()},
            managed_fields=tuple(
                sorted(
                    {
                        str(entry.get("manager"))
                        for entry in metadata.get("managedFields") or ()
                        if isinstance(entry, dict) and entry.get("manager")
                    }
                )
            ),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise EvidenceError(f"not a Kubernetes Service object: {exc}") from None
    if document.get("kind") not in (None, "Service"):
        raise EvidenceError("object is not a Service")
    for value in (snapshot.name, snapshot.namespace, snapshot.uid, snapshot.resource_version):
        if not isinstance(value, str) or not value:
            raise EvidenceError("Service identity fields must be non-empty strings")
    return snapshot


@dataclass(frozen=True)
class ProbeSummary:
    """Aggregates computed only from the validated per-request records."""

    probe_id: str
    scenario: str
    target_service: str
    target_url: str
    total_requests: int
    success_count: int
    error_count: int
    observed_error_percent: float
    status_distribution: Mapping[str, int]
    served_versions: Mapping[str, int]
    served_pods: Mapping[str, int]
    latency_ms: Mapping[str, float | None]
    started_at_utc: str
    completed_at_utc: str
    evidence_sha256: str
    raw_logs_sha256: str | None = None
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "scenario": self.scenario,
            "target_service": self.target_service,
            "target_url": self.target_url,
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "observed_error_percent": self.observed_error_percent,
            "status_distribution": dict(self.status_distribution),
            "served_versions": dict(self.served_versions),
            "served_pods": dict(self.served_pods),
            "latency_ms": dict(self.latency_ms),
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "evidence_sha256": self.evidence_sha256,
            "raw_logs_sha256": self.raw_logs_sha256,
            "artifact_path": self.artifact_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProbeSummary":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})


def summarize_probe_document(
    document: Mapping[str, Any],
    *,
    probe_id: str,
    scenario: str,
    target_service: str,
    evidence_sha256: str,
    raw_logs_sha256: str | None = None,
    artifact_path: str | None = None,
) -> ProbeSummary:
    """Summarize an already strictly validated probe evidence document."""
    from trafficfuse.lab.evidence import validate_evidence_document

    result = validate_evidence_document(document)
    if not result.valid:
        raise EvidenceError("probe evidence failed strict validation: " + "; ".join(result.errors))
    records = document["records"]
    statuses = Counter(
        "no_http_response" if r["status_code"] is None else str(r["status_code"]) for r in records
    )
    versions = Counter(r.get("served_version") or "unreported" for r in records)
    pods = Counter(r.get("served_pod") or "unreported" for r in records)
    return ProbeSummary(
        probe_id=probe_id,
        scenario=scenario,
        target_service=target_service,
        target_url=document["target_url"],
        total_requests=document["total_requests"],
        success_count=document["success_count"],
        error_count=document["error_count"],
        observed_error_percent=float(document["observed_error_percent"]),
        status_distribution=dict(sorted(statuses.items())),
        served_versions=dict(sorted(versions.items())),
        served_pods=dict(sorted(pods.items())),
        latency_ms=dict(document["latency_ms"]),
        started_at_utc=document["started_at_utc"],
        completed_at_utc=document["completed_at_utc"],
        evidence_sha256=evidence_sha256,
        raw_logs_sha256=raw_logs_sha256,
        artifact_path=artifact_path,
    )


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    title: str
    source: str
    observed_at: str
    summary: str
    data: Mapping[str, Any]
    sha256: str = ""

    def __post_init__(self) -> None:
        if not _EVIDENCE_ID.fullmatch(self.evidence_id):
            raise EvidenceError(f"invalid evidence id {self.evidence_id!r}")
        if self.source not in SOURCES:
            raise EvidenceError(f"unknown evidence source {self.source!r}")
        digest = sha256_hex(self._content())
        if self.sha256 and self.sha256 != digest:
            raise EvidenceError(f"{self.evidence_id} content does not match its SHA-256")
        object.__setattr__(self, "sha256", digest)

    def _content(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "title": self.title,
            "source": self.source,
            "observed_at": self.observed_at,
            "summary": self.summary,
            "data": self.data,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content() | {"sha256": self.sha256}


@dataclass(frozen=True)
class EvidenceBundle:
    bundle_id: str
    source: str
    collected_at: str
    cluster: Mapping[str, Any]
    items: tuple[EvidenceItem, ...]
    digest: str = ""
    schema: str = field(default=BUNDLE_SCHEMA)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise EvidenceError(f"unknown bundle source {self.source!r}")
        ids = [item.evidence_id for item in self.items]
        if len(set(ids)) != len(ids) or not ids:
            raise EvidenceError("evidence ids must be present and unique")
        if any(item.source != self.source for item in self.items):
            raise EvidenceError("every evidence item must share the bundle's source label")
        digest = sha256_hex(self._content())
        if self.digest and self.digest != digest:
            raise EvidenceError("evidence bundle content does not match its digest")
        object.__setattr__(self, "digest", digest)

    def _content(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "bundle_id": self.bundle_id,
            "source": self.source,
            "collected_at": self.collected_at,
            "cluster": dict(self.cluster),
            "items": [item.to_dict() for item in self.items],
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content() | {"digest": self.digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceBundle":
        """Rebuild and re-verify every item hash and the bundle digest."""
        items = tuple(EvidenceItem(**item) for item in value["items"])
        return cls(
            bundle_id=value["bundle_id"],
            source=value["source"],
            collected_at=value["collected_at"],
            cluster=value["cluster"],
            items=items,
            digest=value["digest"],
            schema=value.get("schema", BUNDLE_SCHEMA),
        )

    def ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.items)

    def item(self, evidence_id: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.evidence_id == evidence_id), None)

    def of_kind(self, kind: str) -> tuple[EvidenceItem, ...]:
        return tuple(item for item in self.items if item.kind == kind)

    def probe(self, scenario: str) -> ProbeSummary | None:
        for item in self.of_kind("probe"):
            if item.data.get("scenario") == scenario:
                return ProbeSummary.from_dict(item.data)
        return None

    def service(self) -> ServiceSnapshot | None:
        for item in self.of_kind("service"):
            data = dict(item.data)
            return ServiceSnapshot(
                name=data["name"],
                namespace=data["namespace"],
                uid=data["uid"],
                resource_version=data["resource_version"],
                selector=data["selector"],
                labels=data["labels"],
                managed_fields=tuple(data.get("managed_fields", ())),
            )
        return None


def probe_item(evidence_id: str, source: str, summary: ProbeSummary) -> EvidenceItem:
    dist = ", ".join(f"HTTP {k}: {v}" for k, v in summary.status_distribution.items())
    served = ", ".join(f"{k}: {v}" for k, v in summary.served_versions.items())
    p95 = summary.latency_ms.get("p95")
    title = {
        "live": "Live Service probe (customer-facing)",
        "baseline": "Healthy-version baseline probe",
        "verify": "Post-intervention live Service probe",
    }.get(summary.scenario, f"Probe {summary.scenario}")
    text = (
        f"{summary.target_service}: {summary.error_count}/{summary.total_requests} requests failed "
        f"(observed {summary.observed_error_percent:g}% errors; {dist}); served by version "
        f"[{served}]; latency p95 {p95 if p95 is None else round(p95, 1)} ms"
    )
    return EvidenceItem(
        evidence_id, "probe", title, source, summary.completed_at_utc, text, summary.to_dict()
    )


def service_item(evidence_id: str, source: str, observed_at: str, snap: ServiceSnapshot) -> EvidenceItem:
    selector = ",".join(f"{k}={v}" for k, v in sorted(snap.selector.items()))
    text = (
        f"Service {snap.namespace}/{snap.name} (uid {snap.uid}, resourceVersion "
        f"{snap.resource_version}) selects pods with [{selector}]"
    )
    return EvidenceItem(
        evidence_id, "service", "Live Service routing selector", source, observed_at, text, snap.to_dict()
    )


def routing_item(
    evidence_id: str, source: str, observed_at: str, endpoints: Iterable[Mapping[str, Any]]
) -> EvidenceItem:
    rows = sorted(
        ({"pod": str(e.get("pod")), "ip": str(e.get("ip")), "ready": bool(e.get("ready"))} for e in endpoints),
        key=lambda row: row["pod"],
    )
    pods = ", ".join(f"{r['pod']}{'' if r['ready'] else ' (not ready)'}" for r in rows) or "none"
    text = f"Live Service endpoints currently route to: {pods}"
    return EvidenceItem(
        evidence_id, "routing", "Live Service endpoints", source, observed_at, text, {"endpoints": rows}
    )


def deployment_item(
    evidence_id: str, source: str, observed_at: str, deployment: Mapping[str, Any]
) -> EvidenceItem:
    text = (
        f"Deployment {deployment['name']} (version label {deployment['version']}): "
        f"{deployment['ready_replicas']}/{deployment['replicas']} ready, image {deployment['image']}, "
        f"created {deployment['created_at']}, generation {deployment['generation']}"
    )
    return EvidenceItem(
        evidence_id,
        "deployment",
        f"Deployment {deployment['name']}",
        source,
        observed_at,
        text,
        dict(deployment),
    )
