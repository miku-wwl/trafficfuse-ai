"""Governed reroute chain against the real installed AIG runtime (real SQLite).

The Kubernetes side is the in-memory simulation lab labelled TEST_FIXTURE:
these tests prove the governance and executor contracts, never live routing.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest
from agentic_intent_gate.runtime import (
    ActionCommand,
    AdmissionCommand,
    ApprovalChoice,
    ApprovalDecisionRequest,
)

from trafficfuse.domain.evidence import SOURCE_FIXTURE
from trafficfuse.infrastructure.governance import (
    CAPABILITY_FILE,
    RECEIPTS_FILE,
    open_governance,
    provision_profile,
)
from trafficfuse.infrastructure.reroute import (
    STATE_REJECTED,
    STATE_SUCCEEDED,
    EvidenceAnchor,
)
from trafficfuse.infrastructure.simulation import SimulatedLab

DIGEST = "ab" * 32


def _plan(lab: SimulatedLab, **overrides) -> dict:
    snap = lab.snapshot()
    arguments = {
        "cluster": "trafficfuse-lab",
        "namespace": "trafficfuse-system",
        "service": "trafficfuse-live",
        "service_uid": snap.uid,
        "expected_resource_version": snap.resource_version,
        "from_version": "faulty",
        "to_version": "healthy",
        "evidence_digest": DIGEST,
    }
    arguments.update(overrides.pop("arguments", {}))
    plan = {
        "goal": "Route live checkout traffic back to the healthy version",
        "target": "trafficfuse-live",
        "steps": [
            {"tool_name": "reroute_service_traffic", "tool_version": "1", "arguments": arguments}
        ],
        "risk_class": "SIDE_EFFECT",
    }
    plan.update(overrides)
    return plan


@pytest.fixture()
def env(tmp_path):
    directory = tmp_path / "profile"
    provision_profile(directory, "simulation")
    lab = SimulatedLab(SOURCE_FIXTURE, artifacts_root=tmp_path / "artifacts")
    anchors = {}

    def lookup(digest):
        return anchors.get(digest)

    snap = lab.snapshot()
    anchors[DIGEST] = EvidenceAnchor(DIGEST, SOURCE_FIXTURE, snap.uid, snap.resource_version, "faulty", True)
    token = (directory / CAPABILITY_FILE).read_text("ascii")
    with open_governance(directory, "simulation", mutation=lab, evidence_lookup=lookup) as gov:
        yield gov, lab, anchors, "Bearer " + token, directory


async def _propose(gov, plan: dict):
    _, intent = gov.provider.stage(json.dumps(plan), "test proposal")
    return await gov.runtime.run_service.create(intent)


def _approve(gov, run):
    return gov.runtime.run_service.decide(
        run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, run.version, "reviewed exact plan")
    )


def _admit_authorize(gov, run, header):
    principal = gov.runtime.verifier.verify(header)
    action = gov.runtime.gateway.admit(
        AdmissionCommand(run.run_id, run.version, principal.subject_id, str(uuid4())), principal
    ).action
    action = gov.runtime.gateway.authorize(
        ActionCommand(action.action_id, action.version, str(uuid4())), principal
    ).action
    return principal, action


def test_full_governed_chain_switches_once_and_persists_receipt(env):
    gov, lab, _, header, directory = env

    async def flow():
        run = await _propose(gov, _plan(lab))
        assert run.state.value == "AwaitingApproval" and run.version == 4
        run = _approve(gov, run)
        assert run.state.value == "Approved" and run.version == 5
        assert lab.snapshot().selector["version"] == "faulty"  # approval dispatches nothing
        principal, action = _admit_authorize(gov, run, header)
        assert action.state.value == "Authorized"
        assert lab.patch_calls == 0  # authorization dispatches nothing either
        command = ActionCommand(action.action_id, action.version, str(uuid4()))
        result = await gov.runtime.gateway.execute(command, principal)
        replay = await gov.runtime.gateway.execute(command, principal)
        return result, replay

    result, replay = asyncio.run(flow())
    action = result.action
    assert action.state.value == "Succeeded"
    assert replay.replayed and replay.action == action
    assert lab.patch_calls == 1  # exactly one dispatch, replay is not a second effect
    assert lab.snapshot().selector == {"app": "trafficfuse", "version": "healthy"}
    (record,) = gov.receipts.records()
    assert record.state == STATE_SUCCEEDED
    assert record.receipt == action.result["receipt_reference"]
    assert record.effect_key == action.effect_key
    # Durable state, read independently of the objects above.
    with closing(sqlite3.connect(directory / RECEIPTS_FILE)) as db:
        rows = db.execute("SELECT state, receipt FROM effects").fetchall()
    assert rows == [(STATE_SUCCEEDED, record.receipt)]
    stored = gov.runtime.actions.get(action.action_id)
    assert stored.state.value == "Succeeded" and stored.authorization["subject_id"] == "trafficfuse-operator"


def test_execute_without_authorization_is_refused(env):
    gov, lab, _, header, _ = env

    async def flow():
        run = _approve(gov, await _propose(gov, _plan(lab)))
        principal = gov.runtime.verifier.verify(header)
        action = gov.runtime.gateway.admit(
            AdmissionCommand(run.run_id, 5, principal.subject_id, str(uuid4())), principal
        ).action
        with pytest.raises(Exception) as caught:
            await gov.runtime.gateway.execute(
                ActionCommand(action.action_id, action.version, str(uuid4())), principal
            )
        return caught.value

    error = asyncio.run(flow())
    assert getattr(error, "code", None) is not None
    assert lab.patch_calls == 0 and lab.snapshot().selector["version"] == "faulty"


def test_wrong_capability_token_cannot_authorize(env):
    gov, lab, *_ = env
    with pytest.raises(Exception) as caught:
        gov.runtime.verifier.verify("Bearer " + "0" * 64)
    assert caught.value.code.value == "AUTHENTICATION_REQUIRED"
    assert lab.patch_calls == 0


def test_unapproved_run_cannot_be_admitted(env):
    gov, lab, _, header, _ = env

    async def flow():
        run = await _propose(gov, _plan(lab))
        principal = gov.runtime.verifier.verify(header)
        with pytest.raises(Exception) as caught:
            gov.runtime.gateway.admit(
                AdmissionCommand(run.run_id, run.version, principal.subject_id, str(uuid4())), principal
            )
        return caught.value

    error = asyncio.run(flow())
    assert error.code.value == "RUN_NOT_ACTION_ELIGIBLE"


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"target": "kube-dns"}, "foreign target"),
        ({"arguments": {"service": "trafficfuse-healthy"}}, "argument/target mismatch"),
        ({"arguments": {"namespace": "kube-system"}}, "foreign namespace"),
        ({"arguments": {"to_version": "faulty"}}, "route to faulty"),
        ({"arguments": {"to_version": "canary"}}, "unknown version"),
        ({"arguments": {"execution_scope": "00000000-0000-4000-8000-000000000000"}}, "authority field"),
        ({"arguments": {"kubeconfig": "~/.kube/config"}}, "credential field"),
        ({"risk_class": "READ_ONLY"}, "risk downgrade"),
        ({"arguments": {"expected_resource_version": "635\n"}}, "trailing newline"),
    ],
)
def test_unsafe_plans_are_rejected_by_aig_validation(env, overrides, why):
    gov, lab, *_ = env
    run = asyncio.run(_propose(gov, _plan(lab, **overrides)))
    assert run.state.value == "PlanRejected", why
    assert run.error.code.value == "PLAN_VALIDATION_ERROR"
    assert lab.patch_calls == 0


def test_two_step_plan_is_rejected(env):
    gov, lab, *_ = env
    plan = _plan(lab)
    plan["steps"] = plan["steps"] * 2
    run = asyncio.run(_propose(gov, plan))
    assert run.state.value == "PlanRejected"


def test_stale_resource_version_is_refused_without_effect(env):
    gov, lab, _, header, _ = env

    async def flow():
        run = _approve(gov, await _propose(gov, _plan(lab)))
        principal, action = _admit_authorize(gov, run, header)
        lab.mutate_out_of_band()  # the Service changed after approval
        return await gov.runtime.gateway.execute(
            ActionCommand(action.action_id, action.version, str(uuid4())), principal
        )

    result = asyncio.run(flow())
    assert result.action.state.value == "Failed"
    assert lab.patch_calls == 0
    (record,) = gov.receipts.records()
    assert record.state == STATE_REJECTED and "stale target" in record.detail


def test_plan_citing_unknown_evidence_is_refused(env):
    gov, lab, anchors, header, _ = env
    anchors.clear()

    async def flow():
        run = _approve(gov, await _propose(gov, _plan(lab)))
        principal, action = _admit_authorize(gov, run, header)
        return await gov.runtime.gateway.execute(
            ActionCommand(action.action_id, action.version, str(uuid4())), principal
        )

    assert asyncio.run(flow()).action.state.value == "Failed"
    assert lab.patch_calls == 0


def test_unjustified_evidence_is_refused(env):
    gov, lab, anchors, header, _ = env
    snap = lab.snapshot()
    anchors[DIGEST] = EvidenceAnchor(DIGEST, SOURCE_FIXTURE, snap.uid, snap.resource_version, "faulty", False)

    async def flow():
        run = _approve(gov, await _propose(gov, _plan(lab)))
        principal, action = _admit_authorize(gov, run, header)
        return await gov.runtime.gateway.execute(
            ActionCommand(action.action_id, action.version, str(uuid4())), principal
        )

    assert asyncio.run(flow()).action.state.value == "Failed"
    assert lab.patch_calls == 0


def test_rejected_plan_approval_cannot_be_reused_for_another_plan(env):
    gov, lab, _, header, _ = env

    async def flow():
        first = _approve(gov, await _propose(gov, _plan(lab)))
        second = await _propose(gov, _plan(lab, goal="A different plan"))
        principal = gov.runtime.verifier.verify(header)
        with pytest.raises(Exception) as caught:
            gov.runtime.gateway.admit(
                AdmissionCommand(second.run_id, 5, principal.subject_id, str(uuid4())), principal
            )
        return first, caught.value

    first, error = asyncio.run(flow())
    assert first.state.value == "Approved"
    assert error.code.value in {"RUN_NOT_ACTION_ELIGIBLE", "STALE_RUN_VERSION"}


def test_stale_evidence_is_refused_without_effect(env):
    gov, lab, anchors, header, _ = env
    snap = lab.snapshot()
    anchors[DIGEST] = EvidenceAnchor(
        DIGEST, SOURCE_FIXTURE, snap.uid, snap.resource_version, "faulty", True, stale=True
    )

    async def flow():
        run = _approve(gov, await _propose(gov, _plan(lab)))
        principal, action = _admit_authorize(gov, run, header)
        return await gov.runtime.gateway.execute(
            ActionCommand(action.action_id, action.version, str(uuid4())), principal
        )

    assert asyncio.run(flow()).action.state.value == "Failed"
    assert lab.patch_calls == 0
    (record,) = gov.receipts.records()
    assert "freshness window" in record.detail


def test_anchor_freshness_window_uses_bundle_collection_time():
    from datetime import datetime, timedelta, timezone

    from trafficfuse.application.evidence_collection import MAX_EVIDENCE_AGE_SECONDS, anchor_for
    from trafficfuse.domain.evidence import EvidenceBundle, ServiceSnapshot, service_item

    collected = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
    snap = ServiceSnapshot("trafficfuse-live", "trafficfuse-system", "209a93f5-52d0-479b-b891-97c107899ff6",
                           "635", {"app": "trafficfuse", "version": "faulty"}, {})
    bundle = EvidenceBundle("b", SOURCE_FIXTURE, collected.isoformat(), {"cluster": "trafficfuse-lab"},
                            (service_item("EV-1", SOURCE_FIXTURE, collected.isoformat(), snap),))
    fresh = anchor_for(bundle, now=collected + timedelta(seconds=MAX_EVIDENCE_AGE_SECONDS))
    stale = anchor_for(bundle, now=collected + timedelta(seconds=MAX_EVIDENCE_AGE_SECONDS + 1))
    assert fresh.stale is False and stale.stale is True
