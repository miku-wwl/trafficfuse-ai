"""Independent recovery verification: measured before/after, never assumed.

``RECOVERED`` requires *all* of: the post-intervention probe of the same live
Service measured an error rate below the degradation threshold; every request
was served by the expected version; the live selector now points at that
version; and the probe actually ran (same request count as before). An AIG
``Succeeded`` Action or a Kubernetes API success is an input to *when* to
verify, never evidence that the service recovered.
"""

from __future__ import annotations

from typing import Any

from trafficfuse.domain.evidence import ProbeSummary, ServiceSnapshot

RECOVERED = "RECOVERED"
NOT_RECOVERED = "NOT_RECOVERED"
INCONCLUSIVE = "INCONCLUSIVE"


def _delta(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    return round(after - before, 3)


def verify_recovery(
    *,
    before: ProbeSummary,
    after: ProbeSummary,
    before_service: ServiceSnapshot,
    after_service: ServiceSnapshot,
    expected_version: str,
    degraded_error_percent: float,
) -> dict[str, Any]:
    checks = [
        {
            "name": "error_rate_below_threshold",
            "passed": after.observed_error_percent < degraded_error_percent,
            "detail": f"after {after.observed_error_percent:g}% vs threshold {degraded_error_percent:g}% "
            f"(before {before.observed_error_percent:g}%)",
        },
        {
            "name": "served_by_expected_version",
            "passed": after.served_versions == {expected_version: after.total_requests},
            "detail": f"served versions after: {dict(after.served_versions)}",
        },
        {
            "name": "selector_points_at_expected_version",
            "passed": after_service.selector.get("version") == expected_version,
            "detail": f"selector before {dict(before_service.selector)} -> after {dict(after_service.selector)}",
        },
        {
            "name": "same_service_same_sample_size",
            "passed": after.target_service == before.target_service
            and after.total_requests == before.total_requests
            and after_service.uid == before_service.uid,
            "detail": f"{after.target_service} {after.total_requests} requests (before {before.total_requests}); "
            f"uid unchanged: {after_service.uid == before_service.uid}",
        },
    ]
    if all(check["passed"] for check in checks):
        verdict = RECOVERED
    elif after.observed_error_percent >= degraded_error_percent:
        verdict = NOT_RECOVERED
    else:
        verdict = INCONCLUSIVE
    return {
        "verdict": verdict,
        "checks": checks,
        "before": before.to_dict(),
        "after": after.to_dict(),
        "selector_before": dict(before_service.selector),
        "selector_after": dict(after_service.selector),
        "resource_version_before": before_service.resource_version,
        "resource_version_after": after_service.resource_version,
        "delta": {
            "error_percent": _delta(after.observed_error_percent, before.observed_error_percent),
            "error_count": after.error_count - before.error_count,
            "latency_p50_ms": _delta(after.latency_ms.get("p50"), before.latency_ms.get("p50")),
            "latency_p95_ms": _delta(after.latency_ms.get("p95"), before.latency_ms.get("p95")),
        },
    }
