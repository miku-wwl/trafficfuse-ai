"""HTTP-level incident flow (simulation lab, fixture model, real AIG runtime).

The diagnosis "model" here is a deterministic TEST_FIXTURE — it exercises the
grounding validator and the governance chain; it is never a real LLM result.
"""

from __future__ import annotations

import json
import time
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from trafficfuse.api.app import create_app
from trafficfuse.application.incidents import IncidentService
from trafficfuse.application.store import IncidentStore
from trafficfuse.domain.evidence import SOURCE_FIXTURE
from trafficfuse.infrastructure.governance import CAPABILITY_FILE, open_governance, provision_profile
from trafficfuse.infrastructure.ollama import fixture_response
from trafficfuse.infrastructure.simulation import SimulatedLab

GOOD = {
    "incident_summary": "Live checkout Service returns HTTP 500 for half of requests.",
    "likely_cause": "The live selector routes all traffic to the faulty version (EV-3, EV-1).",
    "evidence_refs": ["EV-1", "EV-2", "EV-3"],
    "confidence": "high",
    "uncertainty": "Root cause inside the faulty build is not visible in this evidence.",
    "recommended_action": "reroute_service_traffic",
    "proposal": {
        "service": "trafficfuse-live",
        "namespace": "trafficfuse-system",
        "from_version": "faulty",
        "to_version": "healthy",
        "goal": "Route live checkout traffic back to the healthy version",
    },
}


class FixtureModel:
    model = "TEST_FIXTURE"

    def __init__(self, payload=None, *, fail=False):
        self.payload = payload if payload is not None else GOOD
        self.fail = fail

    async def diagnose(self, prompt):
        assert "EV-1" in prompt and "trafficfuse-live" in prompt
        if self.fail:
            raise RuntimeError("model endpoint unreachable")
        return fixture_response(json.dumps(self.payload))


@pytest.fixture()
def build(tmp_path):
    stack = ExitStack()

    def _build(model=None, mode="simulation"):
        directory = tmp_path / f"aig-{len(stack._exit_callbacks)}"
        provision_profile(directory, "simulation")
        store = IncidentStore(tmp_path / f"incidents-{len(stack._exit_callbacks)}.sqlite3")
        lab = SimulatedLab(SOURCE_FIXTURE, artifacts_root=tmp_path / "artifacts")
        gov = stack.enter_context(
            open_governance(directory, "simulation", mutation=lab, evidence_lookup=IncidentService.evidence_lookup_for(store))
        )
        service = IncidentService(mode=mode, cluster=lab, governance=gov, store=store, model=model or FixtureModel())
        client = stack.enter_context(
            TestClient(create_app(service=service, meta={"mode": mode}), headers={"X-TrafficFuse-Client": "test"})
        )
        token = "Bearer " + (directory / CAPABILITY_FILE).read_text("ascii")
        return client, lab, token

    yield _build
    stack.close()


def _wait(client, incident_id, stages, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/incidents/{incident_id}").json()
        if body["stage"] in stages:
            return body
        time.sleep(0.05)
    raise AssertionError(f"stage never reached {stages}: {body['stage']}")


def _to_approval(client):
    incident = client.post("/api/incidents").json()
    assert incident["stage"] == "DETECTED"
    assert incident["assessment"]["remediation_justified"] is True
    client.post(f"/api/incidents/{incident['incident_id']}/diagnose")
    return _wait(client, incident["incident_id"], {"AWAITING_APPROVAL", "PLAN_REJECTED", "DIAGNOSIS_REJECTED"})


def test_end_to_end_governed_recovery(build):
    client, lab, token = build()
    incident = _to_approval(client)
    incident_id = incident["incident_id"]
    assert incident["stage"] == "AWAITING_APPROVAL"
    assert incident["run"]["state"] == "AwaitingApproval"
    fingerprint = incident["run"]["plan_fingerprint"]

    body = client.post(
        f"/api/incidents/{incident_id}/approve",
        json={"plan_fingerprint": fingerprint, "approver": "Alex SRE", "reason": "evidence reviewed"},
    ).json()
    assert body["stage"] == "APPROVED" and body["run"]["state"] == "Approved"
    assert lab.patch_calls == 0

    body = client.post(f"/api/incidents/{incident_id}/authorize", headers={"Authorization": token}).json()
    assert body["stage"] == "AUTHORIZED" and body["action"]["state"] == "Authorized"
    assert lab.patch_calls == 0

    body = client.post(f"/api/incidents/{incident_id}/execute", headers={"Authorization": token}).json()
    assert body["action"]["state"] == "Succeeded"
    assert body["receipt"]["state"] == "SUCCEEDED"
    assert body["receipt"]["receipt"] == body["action"]["result"]["receipt_reference"]
    assert lab.patch_calls == 1

    final = _wait(client, incident_id, {"RECOVERED", "NOT_RECOVERED", "VERIFICATION_INCONCLUSIVE"})
    verification = final["verification"]
    assert final["stage"] == "RECOVERED"
    assert verification["before"]["observed_error_percent"] == 50.0
    assert verification["after"]["observed_error_percent"] == 0.0
    assert verification["after"]["served_versions"] == {"healthy": 40}
    assert verification["selector_after"] == {"app": "trafficfuse", "version": "healthy"}
    stages = [e["stage"] for e in final["events"]]
    for expected in ("DETECT", "DIAGNOSE", "PLAN", "APPROVE", "ADMIT", "AUTHORIZE", "EXECUTE", "VERIFY"):
        assert expected in stages

    # A second execute is refused by the gate: no second effect.
    again = client.post(f"/api/incidents/{incident_id}/execute", headers={"Authorization": token})
    assert again.status_code == 409
    assert lab.patch_calls == 1


def test_stale_fingerprint_cannot_approve(build):
    client, lab, _ = build()
    incident = _to_approval(client)
    response = client.post(
        f"/api/incidents/{incident['incident_id']}/approve",
        json={"plan_fingerprint": "f" * 64, "approver": "Alex"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STALE_PLAN_FINGERPRINT"


def test_authorization_requires_the_capability(build):
    client, lab, token = build()
    incident = _to_approval(client)
    incident_id = incident["incident_id"]
    client.post(
        f"/api/incidents/{incident_id}/approve",
        json={"plan_fingerprint": incident["run"]["plan_fingerprint"], "approver": "Alex"},
    )
    assert client.post(f"/api/incidents/{incident_id}/authorize").status_code == 401
    wrong = client.post(f"/api/incidents/{incident_id}/authorize", headers={"Authorization": "Bearer " + "1" * 64})
    assert wrong.status_code == 401
    execute = client.post(f"/api/incidents/{incident_id}/execute", headers={"Authorization": token})
    assert execute.status_code == 409 and execute.json()["detail"]["code"] == "NOT_AUTHORIZED"
    assert lab.patch_calls == 0


def test_authorize_before_approval_is_refused_by_the_gate(build):
    client, lab, token = build()
    incident = _to_approval(client)
    response = client.post(f"/api/incidents/{incident['incident_id']}/authorize", headers={"Authorization": token})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUN_NOT_ACTION_ELIGIBLE"
    assert lab.patch_calls == 0


def test_invented_evidence_reference_is_rejected_before_the_gate(build):
    client, lab, _ = build(FixtureModel(GOOD | {"evidence_refs": ["EV-1", "EV-99"]}))
    incident = _to_approval(client)
    assert incident["stage"] == "DIAGNOSIS_REJECTED"
    assert "UNGROUNDED_REFERENCE" in incident["diagnosis_error"]
    assert incident["run"] is None


def test_model_authority_fields_are_rejected(build):
    client, lab, _ = build(FixtureModel(GOOD | {"execution_scope": "x", "kubeconfig": "~/.kube/config"}))
    incident = _to_approval(client)
    assert incident["stage"] == "DIAGNOSIS_REJECTED"
    assert "UNEXPECTED_FIELDS" in incident["diagnosis_error"]


def test_unsafe_model_proposal_is_refused_by_aig(build):
    unsafe = json.loads(json.dumps(GOOD))
    unsafe["proposal"]["namespace"] = "kube-system"
    client, lab, _ = build(FixtureModel(unsafe))
    incident = _to_approval(client)
    assert incident["stage"] == "PLAN_REJECTED"
    assert incident["run"]["state"] == "PlanRejected"
    assert incident["run"]["error"]["code"] == "PLAN_VALIDATION_ERROR"
    assert any(issue["code"].startswith("SCHEMA_") for issue in incident["plan"]["issues"])


def test_model_unavailable_is_reported_not_faked(build):
    client, lab, _ = build(FixtureModel(fail=True))
    incident = client.post("/api/incidents").json()
    client.post(f"/api/incidents/{incident['incident_id']}/diagnose")
    final = _wait(client, incident["incident_id"], {"MODEL_UNAVAILABLE"})
    assert final["diagnosis"] is None and final["run"] is None


@pytest.mark.parametrize("scenario", ["foreign-target", "route-to-faulty", "authority-injection", "multi-step-delete"])
def test_redteam_proposals_are_refused_by_aig(build, scenario):
    client, lab, _ = build()
    record = client.post(f"/api/redteam/{scenario}").json()
    assert record["run_state"] == "PlanRejected"
    assert record["error_code"] == "PLAN_VALIDATION_ERROR"
    assert record["origin"] == "RED_TEAM_FIXTURE"
    assert record["issues"]
    assert lab.patch_calls == 0
    assert client.get("/api/refusals").json()[0]["scenario"] == scenario


def test_replay_mode_is_read_only(build):
    client, lab, _ = build(mode="replay")
    for path in (
        "/api/incidents",
        "/api/redteam/foreign-target",
        "/api/lab/reset",
        "/api/incidents/INC-x/approve",  # refused before body validation
        "/api/incidents/INC-x/execute",
    ):
        response = client.post(path, json={})
        assert response.status_code == 403, path
        assert response.json()["detail"]["code"] == "REPLAY_READ_ONLY"


def test_meta_and_health(build):
    client, *_ = build()
    assert client.get("/health").json()["status"] == "ok"
    assert "LIVE_KIND" in client.get("/api/meta").json()["sources"]


def test_view_serves_per_request_records_only_with_matching_hash(build, tmp_path):
    client, lab, _ = build()
    incident = client.post("/api/incidents").json()
    live = next(p for p in incident["probes"] if p["scenario"] == "live")
    assert live["integrity"] == "verified" and len(live["records"]) == 40
    assert {r["version"] for r in live["records"]} == {"faulty"}
    assert incident["timing"]["seconds_from_detection"]["detected_at"] == 0.0
    # Tamper with the stored evidence document: the view refuses to render it.
    from pathlib import Path

    path = Path(live["artifact_path"]) / "probe-evidence.json"
    path.write_bytes(path.read_bytes().replace(b'"status_code": 500', b'"status_code": 200'))
    again = client.get(f"/api/incidents/{incident['incident_id']}").json()
    tampered = next(p for p in again["probes"] if p["scenario"] == "live")
    assert tampered["integrity"] == "mismatch" and tampered["records"] == []


def test_non_loopback_clients_cannot_mutate(build):
    client, lab, _ = build()
    remote = TestClient(client.app, client=("203.0.113.7", 50000), headers={"X-TrafficFuse-Client": "test"})
    response = remote.post("/api/incidents")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "LOOPBACK_ONLY"
    assert remote.get("/health").status_code == 200  # reads stay available


def test_mutations_require_the_client_header_csrf_guard(build):
    client, lab, _ = build()
    bare = TestClient(client.app)  # what a forged cross-site "simple" POST looks like
    response = bare.post("/api/incidents")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CLIENT_HEADER_REQUIRED"
    assert lab.patch_calls == 0 and client.get("/api/incidents").json() == []


def test_untrusted_host_header_is_rejected_dns_rebinding_guard(build):
    client, *_ = build()
    response = client.get("/api/meta", headers={"Host": "attacker.example"})
    assert response.status_code == 400
