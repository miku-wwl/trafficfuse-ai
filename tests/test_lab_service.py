"""Lab service behavior: healthy/faulty modes and deterministic faults."""

import asyncio

import httpx
import pytest

from trafficfuse.lab.fault import FaultPolicy
from trafficfuse.lab.service import MODE_FAULTY, MODE_HEALTHY, create_lab_app


def _get(app, path: str) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://trafficfuse-lab"
        ) as client:
            return await client.get(path)

    return asyncio.run(_run())


# --- fault policy ------------------------------------------------------------


def test_fault_policy_exact_rate_over_100_requests() -> None:
    # Phase 1A.1 (finding P2): a complete 100-request sequence at configured
    # integer rate P produces exactly P failures — no interval approximation.
    for rate in (0, 1, 10, 25, 30, 45, 50, 75, 90, 99, 100):
        policy = FaultPolicy(rate)
        failures = sum(policy.should_fail(n) for n in range(1, 101))
        assert failures == rate, f"rate {rate}% realized {failures} failures"


def test_fault_policy_high_rates_no_longer_capped() -> None:
    # Previous defect (finding P2): rates above 50% stabilised at ~50%. The
    # corrected policy realizes exactly 75 failures per 100 requests at 75%.
    policy = FaultPolicy(75)
    assert sum(policy.should_fail(n) for n in range(1, 101)) == 75
    assert [policy.should_fail(n) for n in range(1, 7)] == [
        False,
        True,
        True,
        True,
        False,
        True,
    ]


def test_fault_policy_deterministic_rotation() -> None:
    policy = FaultPolicy(rate_percent=25)
    pattern = [policy.should_fail(n) for n in range(1, 9)]
    assert pattern == [False, False, False, True, False, False, False, True]
    # Same inputs, same outcome, every run.
    assert [policy.should_fail(n) for n in range(1, 9)] == pattern


def test_fault_policy_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="0..100"):
        FaultPolicy(rate_percent=101)
    with pytest.raises(ValueError, match="0..100"):
        FaultPolicy(rate_percent=-1)
    with pytest.raises(ValueError, match=">= 1"):
        FaultPolicy(50).should_fail(0)


# --- service construction ----------------------------------------------------


def test_invalid_mode_and_rate_rejected() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        create_lab_app(mode="broken")
    with pytest.raises(ValueError, match="0..100"):
        create_lab_app(mode=MODE_FAULTY, fault_rate_percent=101)
    with pytest.raises(ValueError, match="0..100"):
        create_lab_app(mode=MODE_FAULTY, fault_rate_percent=-1)


# --- healthy version ---------------------------------------------------------


def test_healthy_endpoints() -> None:
    app = create_lab_app(mode=MODE_HEALTHY)
    ready = _get(app, "/ready")
    assert ready.status_code == 200
    assert ready.json()["version"] == "healthy"

    version = _get(app, "/version")
    body = version.json()
    assert body["version"] == "healthy"
    assert body["component"] == "trafficfuse-lab-service"
    assert body["workload"] == "synthetic"

    checkout = _get(app, "/checkout")
    assert checkout.status_code == 200
    payload = checkout.json()
    assert payload["status"] == "ok"
    assert payload["synthetic"] is True
    assert payload["transaction_id"].startswith("synthetic-checkout-")


def test_healthy_mode_ignores_fault_rate() -> None:
    app = create_lab_app(mode=MODE_HEALTHY, fault_rate_percent=100)
    assert all(_get(app, "/checkout").status_code == 200 for _ in range(5))


# --- faulty version ----------------------------------------------------------


def test_faulty_ready_stays_valid_endpoint() -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=100)
    ready = _get(app, "/ready")
    assert ready.status_code == 200
    assert ready.json()["version"] == "faulty"
    assert _get(app, "/version").json()["version"] == "faulty"


def test_faulty_checkout_is_deterministic() -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    statuses = [_get(app, "/checkout").status_code for _ in range(10)]
    assert statuses == [200, 500, 200, 500, 200, 500, 200, 500, 200, 500]


def test_faulty_all_fail_at_100_percent_and_never_at_0() -> None:
    app100 = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=100)
    assert all(_get(app100, "/checkout").status_code == 500 for _ in range(5))
    app0 = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=0)
    assert all(_get(app0, "/checkout").status_code == 200 for _ in range(5))


def test_faulty_error_body_is_labelled_synthetic() -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=100)
    response = _get(app, "/checkout")
    assert response.status_code == 500
    assert response.json()["synthetic"] is True
    assert "synthetic" in response.json()["detail"]


# --- probe-driven request index -------------------------------------------------


def _get_with_index(app, path: str, index: str | None) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://trafficfuse-lab"
        ) as client:
            headers = (
                {"X-TrafficFuse-Request-Index": index} if index is not None else {}
            )
            return await client.get(path, headers=headers)

    return asyncio.run(_run())


def test_header_index_drives_deterministic_fault_decision() -> None:
    # At 50%, the exact distribution fails on every second 0-based index, so
    # probe sequence 1..10 map to 500,200,500,... regardless of arrival order.
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    statuses = []
    for index in range(1, 11):
        response = _get_with_index(app, "/checkout", str(index))
        statuses.append(response.status_code)
        assert response.json()["request_sequence"] == "header"
        assert response.json()["request_number"] == index
    assert statuses == [200, 500, 200, 500, 200, 500, 200, 500, 200, 500]
    # Exact realized count over the complete sequence.
    assert statuses.count(500) == 5


def test_missing_or_invalid_header_falls_back_deterministically() -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    # Missing header: per-process counter, still exactly 5/10 at 50%.
    fallback = [_get_with_index(app, "/checkout", None) for _ in range(10)]
    assert [r.status_code for r in fallback].count(500) == 5
    assert all(r.json()["request_sequence"] == "local-counter" for r in fallback)
    # Invalid header values are ignored the same way.
    for bad in ("", "abc", "-3", "0", "1.5", "999999999999999999999999"):
        response = _get_with_index(app, "/checkout", bad)
        assert response.json()["request_sequence"] == "local-counter"
