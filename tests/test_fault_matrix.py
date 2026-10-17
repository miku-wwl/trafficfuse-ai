"""Deterministic fault-rate matrix (Phase 1A.1 finding P2).

Required matrix: 0%, 1%, 10%, 25%, 30%, 45%, 50%, 75%, 90%, 99%, 100% —
verified over complete 100-request sequences, multiple 100-request cycles,
reproducible instances, concurrency, invalid inputs, and the probe-driven
multi-replica case. Every assertion is computed from the pure policy function
or from real HTTP responses; nothing is faked.
"""

import asyncio
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from trafficfuse.lab.fault import FaultPolicy
from trafficfuse.lab.probe import ProbeConfig, run_probe
from trafficfuse.lab.service import MODE_FAULTY, create_lab_app

MATRIX_RATES = (0, 1, 10, 25, 30, 45, 50, 75, 90, 99, 100)


def _realized_failures(rate: int, count: int) -> int:
    policy = FaultPolicy(rate)
    return sum(policy.should_fail(n) for n in range(1, count + 1))


# --- exact 100-request sequences ------------------------------------------------


@pytest.mark.parametrize("rate", MATRIX_RATES)
def test_exact_rate_over_100_requests(rate: int) -> None:
    assert _realized_failures(rate, 100) == rate


@pytest.mark.parametrize("rate", MATRIX_RATES)
def test_exact_rate_over_multiple_100_request_cycles(rate: int) -> None:
    # The distribution continues seamlessly across cycle boundaries.
    assert _realized_failures(rate, 200) == 2 * rate
    assert _realized_failures(rate, 300) == 3 * rate
    assert FaultPolicy(rate).expected_failures(200) == 2 * rate
    assert FaultPolicy(rate).expected_failures(300) == 3 * rate


@pytest.mark.parametrize("rate", MATRIX_RATES)
def test_failures_are_evenly_distributed_not_clustered(rate: int) -> None:
    policy = FaultPolicy(rate)
    sequence = [policy.should_fail(n) for n in range(1, 101)]
    # Failures in the first 10 and last 10 requests match the exact prefix
    # counts — failures are spread across the whole sequence, not clustered at
    # a boundary.
    in_first_ten = sum(sequence[:10])
    in_last_ten = sum(sequence[90:])
    assert in_first_ten == policy.expected_failures(10)
    assert in_last_ten == rate - policy.expected_failures(90)
    if 0 < rate < 100:
        longest_ok_run = 1
        run = 0
        for failed in sequence:
            run = 0 if failed else run + 1
            longest_ok_run = max(longest_ok_run, run)
        assert longest_ok_run <= (100 + rate - 1) // rate


# --- reproducibility --------------------------------------------------------------


@pytest.mark.parametrize("rate", MATRIX_RATES)
def test_reproducible_across_instances(rate: int) -> None:
    first = [FaultPolicy(rate).should_fail(n) for n in range(1, 101)]
    second = [FaultPolicy(rate).should_fail(n) for n in range(1, 101)]
    assert first == second


def test_reproducible_across_replays_of_same_instance() -> None:
    policy = FaultPolicy(45)
    runs = tuple(tuple(policy.should_fail(n) for n in range(1, 101)) for _ in range(3))
    assert runs[0] == runs[1] == runs[2]
    assert sum(runs[0]) == 45


# --- boundary and invalid values ---------------------------------------------------


@pytest.mark.parametrize("rate", [0, 100])
def test_boundary_rates_accepted(rate: int) -> None:
    policy = FaultPolicy(rate)
    assert policy.rate_percent == rate


@pytest.mark.parametrize("rate", [-1, 101, 1000, True, False, 50.0, "50", None])
def test_invalid_rates_rejected(rate: object) -> None:
    with pytest.raises(ValueError):
        FaultPolicy(rate)  # type: ignore[arg-type]


@pytest.mark.parametrize("number", [0, -1, 1000000000000000000000, True, 2.5, "7", None])
def test_invalid_request_numbers_rejected(number: object) -> None:
    with pytest.raises(ValueError):
        FaultPolicy(50).should_fail(number)  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [0, -5, 2.5, True, None])
def test_expected_failures_rejects_invalid_counts(count: object) -> None:
    with pytest.raises(ValueError):
        FaultPolicy(50).expected_failures(count)  # type: ignore[arg-type]


# --- concurrency --------------------------------------------------------------------


def test_policy_is_safe_under_concurrent_evaluation() -> None:
    # The policy is a pure function: concurrent evaluation from many threads
    # must equal the serial result, record for record.
    policy = FaultPolicy(37)
    numbers = list(range(1, 501))
    serial = [policy.should_fail(n) for n in numbers]
    results: dict[int, bool] = {}
    lock = threading.Lock()

    def worker(chunk: list[int]) -> None:
        local = {n: policy.should_fail(n) for n in chunk}
        with lock:
            results.update(local)

    with ThreadPoolExecutor(max_workers=8) as pool:
        chunks = [numbers[i::8] for i in range(8)]
        list(pool.map(worker, chunks))

    assert [results[n] for n in numbers] == serial
    assert sum(serial) == FaultPolicy(37).expected_failures(500)


# --- probe-driven multi-replica behavior ---------------------------------------------


def _probe(app, *, requests: int, concurrency: int = 8) -> tuple[int, float]:
    config = ProbeConfig(
        target_url="http://trafficfuse-lab/checkout",
        scenario_id=f"matrix-{requests}",
        target_label="service:trafficfuse-live",
        total_requests=requests,
        concurrency=concurrency,
    )
    evidence = asyncio.run(run_probe(config, transport=httpx.ASGITransport(app=app)))
    return evidence.error_count, evidence.observed_error_percent


@pytest.mark.parametrize("rate", [0, 1, 25, 30, 45, 50, 75, 90, 99, 100])
def test_probe_measures_exact_configured_rate_over_100_requests(rate: int) -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    errors, observed = _probe(app, requests=100)
    assert errors == rate
    assert observed == float(rate)


@pytest.mark.parametrize("rate", [25, 50, 75])
def test_exact_rate_across_two_independent_faulty_replicas(rate: int) -> None:
    # Simulate the Kubernetes Service load-balancing one probe sequence across
    # two faulty Pods. The probe stamps every request, so each Pod evaluates the
    # same pure function of the same index: the global realized count is exact
    # regardless of routing. This does NOT prove distributed request ordering.
    replica_a = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    replica_b = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    order = list(range(1, 101))
    random.Random(rate).shuffle(order)
    errors = 0
    for index in order:
        app = replica_a if index % 2 == 0 else replica_b
        status = _probe_status(app, index)
        if status == 500:
            errors += 1
    assert errors == rate


def _probe_status(app, index: int) -> int:
    async def _run() -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://trafficfuse-lab"
        ) as client:
            response = await client.get(
                "/checkout", headers={"X-TrafficFuse-Request-Index": str(index)}
            )
            return response.status_code

    return asyncio.run(_run())


def test_missing_header_per_pod_counters_are_bounded_but_not_exact() -> None:
    # Documented limitation (not a PASS claim): without the probe header each
    # Pod uses its own process counter. Each replica's count is exact for its
    # own prefix, but the combined total can round differently from the exact
    # global count — bounded by at most one request per replica. The probe's
    # request-index header removes even this deviation (see
    # test_exact_rate_across_two_independent_faulty_replicas).
    rate = 45
    replica_a = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    replica_b = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    split_a, split_b = 139, 1  # deliberately uneven Service routing
    a_errors = sum(_probe_status_via_counter(replica_a) == 500 for _ in range(split_a))
    b_errors = sum(_probe_status_via_counter(replica_b) == 500 for _ in range(split_b))
    total = split_a + split_b
    assert a_errors == FaultPolicy(rate).expected_failures(split_a) == 62
    assert b_errors == FaultPolicy(rate).expected_failures(split_b) == 0
    exact_global = FaultPolicy(rate).expected_failures(total)
    assert a_errors + b_errors == exact_global - 1  # bounded rounding deviation
    assert abs(a_errors + b_errors - exact_global) <= 2  # one per replica


def _probe_status_via_counter(app) -> int:
    async def _run() -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://trafficfuse-lab"
        ) as client:
            response = await client.get("/checkout")
            return response.status_code

    return asyncio.run(_run())
