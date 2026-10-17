"""Consumer-owned deterministic HTTP probe.

The probe measures **real HTTP responses only**: observed error rates and
latency are computed from actual status codes and durations. A configured
target failure percentage is never reported as a measurement (AGENTS.md
synthetic-metrics rule).

Evidence is structured JSON with a per-request raw record retained for
auditability. The same component runs offline (in-process ASGI transport in
tests) and later inside the isolated Kind cluster (Job with the lab image).

Every request is stamped with the probe's deterministic sequence number
(``X-TrafficFuse-Request-Index``) so the lab service can apply the exact
configured fault distribution even when the Service load-balances across
several faulty Pods. This is a measurement input, not a measured outcome: the
observed error rate in the evidence is still computed solely from real
responses.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from trafficfuse.lab.fault import REQUEST_INDEX_HEADER

PROBE_EVIDENCE_SCHEMA = "trafficfuse-probe-evidence-v1"
EVIDENCE_SOURCE = "real_http_responses"

# The probe stamps every request with its own deterministic sequence number.
# The lab service uses it (when valid) so the configured fault distribution is
# exact across multiple faulty Pods; an absent or invalid index simply
# degrades the service to its per-process counter. See lab/fault.py.
REQUEST_INDEX_HEADER_NAME = REQUEST_INDEX_HEADER

OUTCOME_SUCCESS = "success"
OUTCOME_HTTP_ERROR = "http_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_TRANSPORT_ERROR = "transport_error"
OUTCOME_MALFORMED = "malformed"

WORKLOAD_DECLARATION = (
    "TrafficFuse synthetic lab workload (Phase 1A). All measurements in this "
    "evidence are computed from real HTTP responses; any configured target "
    "failure percentage is a policy input, never a measured outcome."
)


@dataclass(frozen=True)
class ProbeConfig:
    target_url: str
    scenario_id: str
    target_label: str
    total_requests: int = 20
    concurrency: int = 4
    timeout_seconds: float = 5.0
    synthetic_workload: bool = True

    def __post_init__(self) -> None:
        if not self.target_url:
            raise ValueError("target_url must not be empty")
        if not self.scenario_id:
            raise ValueError("scenario_id must not be empty")
        if not self.target_label:
            raise ValueError("target_label must not be empty")
        if self.total_requests < 1:
            raise ValueError(f"total_requests must be >= 1, got {self.total_requests!r}")
        if self.concurrency < 1:
            raise ValueError(f"concurrency must be >= 1, got {self.concurrency!r}")
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {self.timeout_seconds!r}")


@dataclass(frozen=True)
class ProbeRecord:
    sequence: int
    target_url: str
    outcome: str
    status_code: int | None
    duration_ms: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "target_url": self.target_url,
            "outcome": self.outcome,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProbeEvidence:
    schema: str
    scenario_id: str
    target_label: str
    target_url: str
    started_at_utc: str
    completed_at_utc: str
    total_requests: int
    success_count: int
    error_count: int
    observed_error_percent: float
    latency_ms: dict[str, float | None]
    evidence_source: str
    synthetic_workload: bool
    workload_declaration: str
    records: tuple[ProbeRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scenario_id": self.scenario_id,
            "target_label": self.target_label,
            "target_url": self.target_url,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "observed_error_percent": self.observed_error_percent,
            "latency_ms": self.latency_ms,
            "evidence_source": self.evidence_source,
            "synthetic_workload": self.synthetic_workload,
            "workload_declaration": self.workload_declaration,
            "records": [record.to_dict() for record in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    return (
        sorted_values[lower] * (upper - position)
        + sorted_values[upper] * (position - lower)
    )


async def _single_request(
    client: httpx.AsyncClient, config: ProbeConfig, sequence: int
) -> ProbeRecord:
    start = time.perf_counter()
    try:
        response = await client.get(
            config.target_url,
            headers={REQUEST_INDEX_HEADER_NAME: str(sequence)},
        )
    except httpx.TimeoutException as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return ProbeRecord(
            sequence, config.target_url, OUTCOME_TIMEOUT, None, round(duration_ms, 3), str(exc)
        )
    except httpx.TransportError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return ProbeRecord(
            sequence,
            config.target_url,
            OUTCOME_TRANSPORT_ERROR,
            None,
            round(duration_ms, 3),
            str(exc),
        )
    duration_ms = (time.perf_counter() - start) * 1000
    if not 200 <= response.status_code < 400:
        return ProbeRecord(
            sequence,
            config.target_url,
            OUTCOME_HTTP_ERROR,
            response.status_code,
            round(duration_ms, 3),
            f"HTTP {response.status_code}",
        )
    try:
        response.json()
    except ValueError as exc:  # malformed body from a supposedly healthy endpoint
        return ProbeRecord(
            sequence,
            config.target_url,
            OUTCOME_MALFORMED,
            response.status_code,
            round(duration_ms, 3),
            f"response body is not valid JSON: {exc}",
        )
    return ProbeRecord(
        sequence, config.target_url, OUTCOME_SUCCESS, response.status_code,
        round(duration_ms, 3), "",
    )


async def run_probe(
    config: ProbeConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProbeEvidence:
    """Run the probe. ``transport`` is injected in offline tests (ASGI transport);
    when omitted, the default network transport is used (in-cluster, Phase 1B)."""
    semaphore = asyncio.Semaphore(config.concurrency)
    started = datetime.now(timezone.utc)

    async with httpx.AsyncClient(transport=transport, timeout=config.timeout_seconds) as client:

        async def worker(sequence: int) -> ProbeRecord:
            async with semaphore:
                return await _single_request(client, config, sequence)

        results = await asyncio.gather(
            *(worker(sequence) for sequence in range(1, config.total_requests + 1))
        )
    completed = datetime.now(timezone.utc)

    records = tuple(results)
    success_count = sum(1 for record in records if record.outcome == OUTCOME_SUCCESS)
    error_count = len(records) - success_count
    observed_error_percent = round(100.0 * error_count / len(records), 2) if records else 0.0
    durations = sorted(record.duration_ms for record in records)
    latency_ms: dict[str, float | None] = {
        "min": durations[0] if durations else None,
        "p50": _percentile(durations, 0.50),
        "p95": _percentile(durations, 0.95),
        "max": durations[-1] if durations else None,
        "mean": round(sum(durations) / len(durations), 3) if durations else None,
    }
    return ProbeEvidence(
        schema=PROBE_EVIDENCE_SCHEMA,
        scenario_id=config.scenario_id,
        target_label=config.target_label,
        target_url=config.target_url,
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        total_requests=len(records),
        success_count=success_count,
        error_count=error_count,
        observed_error_percent=observed_error_percent,
        latency_ms=latency_ms,
        evidence_source=EVIDENCE_SOURCE,
        synthetic_workload=config.synthetic_workload,
        workload_declaration=WORKLOAD_DECLARATION,
        records=records,
    )
