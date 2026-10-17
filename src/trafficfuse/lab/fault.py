"""Deterministic fault injection for the synthetic lab workload.

The faulty service version must degrade *reproducibly from test inputs*, not
from uncontrolled randomness (Phase 1A brief). The policy fails every N-th
request, where N is derived from the configured target rate:

    0%   -> never fails (interval 0)
    100% -> every request fails (interval 1)
    r%   -> every ``max(2, round(100 / r))``-th request fails

The rotation never overshoots the configured rate; for rates above 50% it
stabilises at ~50%.

The configured percentage is a *target*, never a measured outcome. Only real
HTTP responses may be reported as measurements (AGENTS.md synthetic-metrics
rule; see ``trafficfuse.lab.probe``).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FAULT_RATE_PERCENT = 50


@dataclass(frozen=True)
class FaultPolicy:
    """Deterministic, per-process request-rotation fault policy."""

    rate_percent: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.rate_percent <= 100:
            raise ValueError(
                f"fault rate must be 0..100 percent, got {self.rate_percent!r}"
            )

    @property
    def failure_interval(self) -> int:
        """Fail every N-th request. 0 means never; 1 means every request."""
        if self.rate_percent <= 0:
            return 0
        if self.rate_percent >= 100:
            return 1
        return max(2, round(100 / self.rate_percent))

    def should_fail(self, request_number: int) -> bool:
        """Deterministic decision for the given 1-based request number."""
        if request_number < 1:
            raise ValueError(
                f"request_number must be >= 1, got {request_number!r}"
            )
        interval = self.failure_interval
        return interval > 0 and request_number % interval == 0
