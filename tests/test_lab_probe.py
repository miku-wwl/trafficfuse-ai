"""Probe behavior: real-response measurement, classification, evidence."""

import asyncio
import json

import httpx
import pytest

from trafficfuse.lab.probe import (
    OUTCOME_HTTP_ERROR,
    OUTCOME_MALFORMED,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    OUTCOME_TRANSPORT_ERROR,
    PROBE_EVIDENCE_SCHEMA,
    ProbeConfig,
    run_probe,
)
from trafficfuse.lab.service import MODE_FAULTY, MODE_HEALTHY, create_lab_app


def _transport(app):
    return httpx.ASGITransport(app=app)


def _config(**overrides) -> ProbeConfig:
    base = {
        "target_url": "http://trafficfuse-lab/checkout",
        "scenario_id": "phase1a-test",
        "target_label": "version:healthy",
    }
    base.update(overrides)
    return ProbeConfig(**base)


def test_probe_healthy_all_success() -> None:
    app = create_lab_app(mode=MODE_HEALTHY)
    evidence = asyncio.run(run_probe(_config(total_requests=20, concurrency=4), transport=_transport(app)))
    assert evidence.total_requests == 20
    assert evidence.success_count == 20
    assert evidence.error_count == 0
    assert evidence.observed_error_percent == 0.0
    assert evidence.schema == PROBE_EVIDENCE_SCHEMA
    assert evidence.evidence_source == "real_http_responses"
    assert evidence.synthetic_workload is True
    assert "never" in evidence.workload_declaration
    assert len(evidence.records) == 20
    assert {record.outcome for record in evidence.records} == {OUTCOME_SUCCESS}
    assert [record.sequence for record in evidence.records] == list(range(1, 21))
    assert evidence.latency_ms["min"] >= 0
    assert evidence.latency_ms["p50"] <= evidence.latency_ms["p95"] <= evidence.latency_ms["max"]


def test_probe_faulty_measures_real_error_rate() -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    evidence = asyncio.run(
        run_probe(
            _config(total_requests=20, concurrency=4, target_label="version:faulty"),
            transport=_transport(app),
        )
    )
    # 20 real requests against the deterministic rotation: exactly 10 fail.
    assert evidence.error_count == 10
    assert evidence.success_count == 10
    assert evidence.observed_error_percent == 50.0
    assert {record.status_code for record in evidence.records} == {200, 500}
    assert any(record.outcome == OUTCOME_HTTP_ERROR for record in evidence.records)


def test_probe_timeout_classification() -> None:
    # httpx enforces timeouts at the connection layer; this test covers the
    # probe's classification of the resulting exception (offline transport).
    async def timing_out_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    transport = httpx.MockTransport(timing_out_handler)
    evidence = asyncio.run(
        run_probe(_config(total_requests=2, concurrency=1, timeout_seconds=0.2), transport=transport)
    )
    assert all(record.outcome == OUTCOME_TIMEOUT for record in evidence.records)
    assert evidence.error_count == 2
    assert evidence.observed_error_percent == 100.0


def test_probe_transport_error_classification() -> None:
    async def broken_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(broken_handler)
    evidence = asyncio.run(run_probe(_config(total_requests=3, concurrency=1), transport=transport))
    assert all(record.outcome == OUTCOME_TRANSPORT_ERROR for record in evidence.records)
    assert evidence.error_count == 3


def test_probe_malformed_body_classification() -> None:
    async def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{not-json")

    transport = httpx.MockTransport(malformed_handler)
    evidence = asyncio.run(run_probe(_config(total_requests=4, concurrency=1), transport=transport))
    assert all(record.outcome == OUTCOME_MALFORMED for record in evidence.records)
    assert evidence.error_count == 4


def test_probe_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="target_url"):
        _config(target_url="")
    with pytest.raises(ValueError, match="scenario_id"):
        _config(scenario_id="")
    with pytest.raises(ValueError, match="total_requests"):
        _config(total_requests=0)
    with pytest.raises(ValueError, match="concurrency"):
        _config(concurrency=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        _config(timeout_seconds=0)


def test_evidence_json_round_trip_and_integrity() -> None:
    app = create_lab_app(mode=MODE_HEALTHY)
    evidence = asyncio.run(run_probe(_config(total_requests=6), transport=_transport(app)))
    payload = json.loads(evidence.to_json())
    assert payload["schema"] == PROBE_EVIDENCE_SCHEMA
    assert payload["evidence_source"] == "real_http_responses"
    assert payload["synthetic_workload"] is True
    assert payload["workload_declaration"]
    assert len(payload["records"]) == 6
    assert payload["records"][0]["sequence"] == 1
    assert payload["records"][-1]["sequence"] == 6
    assert isinstance(payload["observed_error_percent"], float)
    assert set(payload["latency_ms"]) == {"min", "p50", "p95", "max", "mean"}
    # Re-serialising is stable (audit integrity).
    assert json.loads(evidence.to_json()) == payload
