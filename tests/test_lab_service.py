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


def test_fault_policy_intervals() -> None:
    assert FaultPolicy(0).failure_interval == 0
    assert FaultPolicy(100).failure_interval == 1
    assert FaultPolicy(50).failure_interval == 2
    assert FaultPolicy(30).failure_interval == 3
    assert FaultPolicy(25).failure_interval == 4


def test_fault_policy_never_overshoots_high_rates() -> None:
    # Rates above 50% stabilise at ~50% rather than overshooting to 100%.
    assert FaultPolicy(75).failure_interval == 2
    policy = FaultPolicy(75)
    assert [policy.should_fail(n) for n in range(1, 7)] == [
        False,
        True,
        False,
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
