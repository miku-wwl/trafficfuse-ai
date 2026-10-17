"""Deterministic fault injection with exact configured-rate semantics.

The faulty service version must degrade *exactly as configured*, reproducibly
from test inputs, and never from uncontrolled randomness (Phase 1A.1 review
finding P2). The previous interval-rotation policy was accurate only for rates
that divide 100 evenly (25%, 50%) and capped at ~50% for higher rates.

This module implements an exact Bresenham-style distribution::

    failures over request indices k = 0 .. N-1 at configured rate P:
        floor((k + 1) * P / 100) - floor(k * P / 100)

Properties (all covered by tests/test_fault_matrix.py):

* ``0%``   never injects a failure;
* ``100%`` always injects a failure;
* over a complete 100-request deterministic sequence, an integer rate ``P``
  produces **exactly P** injected failures — no more, no fewer;
* failures are spread as evenly as possible (never clustered at the start or
  end), so short observation windows are representative;
* the same ``(rate, request_number)`` pair always produces the same outcome;
* invalid rates and request numbers are rejected (fail closed).

Relationship to real measurements (AGENTS.md synthetic-metrics rule):

* the configured percentage is a **policy input**, never a measured outcome;
* the *client-observed* HTTP failure rate is only ever reported by
  ``trafficfuse.lab.probe`` from real responses;
* when the probe supplies the ``X-TrafficFuse-Request-Index`` header, the fault
  decision follows the probe's own sequence number, so the exact distribution
  holds globally across multiple faulty Pods (each Pod evaluates the same pure
  function of the same index). Without the header (any other client), each Pod
  falls back to its own process counter and the realized rate is only an
  approximation of the configured rate. Neither case changes the policy input.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FAULT_RATE_PERCENT = 50

# Header carrying the probe's deterministic request sequence number. The lab
# service uses it when present; untrusted clients may supply anything, so the
# value is strictly validated and never trusted beyond integer bounds.
REQUEST_INDEX_HEADER = "X-TrafficFuse-Request-Index"

_MAX_REQUEST_NUMBER = 2**53 - 1  # keep the arithmetic well inside float/int range


@dataclass(frozen=True)
class FaultPolicy:
    """Deterministic, exact-rate fault policy.

    ``should_fail(request_number)`` is a pure function of the configured rate
    and the 1-based request number; it holds no state, so it is safe under
    concurrent evaluation and identical across processes and restarts.
    """

    rate_percent: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.rate_percent, bool) or not isinstance(self.rate_percent, int):
            raise ValueError(
                f"fault rate must be an integer 0..100 percent, got {self.rate_percent!r}"
            )
        if not 0 <= self.rate_percent <= 100:
            raise ValueError(
                f"fault rate must be 0..100 percent, got {self.rate_percent!r}"
            )

    def should_fail(self, request_number: int) -> bool:
        """Deterministic decision for the given 1-based request number."""
        if isinstance(request_number, bool) or not isinstance(request_number, int):
            raise ValueError(
                f"request_number must be an integer >= 1, got {request_number!r}"
            )
        if request_number < 1:
            raise ValueError(
                f"request_number must be >= 1, got {request_number!r}"
            )
        if request_number > _MAX_REQUEST_NUMBER:
            raise ValueError(
                f"request_number must be <= {_MAX_REQUEST_NUMBER}, got {request_number!r}"
            )
        rate = self.rate_percent
        if rate <= 0:
            return False
        if rate >= 100:
            return True
        previous = request_number - 1  # 0-based index
        return (previous + 1) * rate // 100 > previous * rate // 100

    def expected_failures(self, request_count: int) -> int:
        """Exact number of failures over request numbers ``1..request_count``.

        This is the mathematical expectation the deterministic sequence
        realizes exactly (used by tests and documentation, never reported as a
        measurement).
        """
        if isinstance(request_count, bool) or not isinstance(request_count, int):
            raise ValueError(
                f"request_count must be an integer >= 1, got {request_count!r}"
            )
        if request_count < 1:
            raise ValueError(
                f"request_count must be >= 1, got {request_count!r}"
            )
        return request_count * self.rate_percent // 100
