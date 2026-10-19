"""Executor contract, concurrency and restart tests (real AIG runtime, real SQLite).

Fault injection wraps the TEST_FIXTURE simulation lab; nothing here touches a
cluster. Every case asserts the durable receipt-store state and the number of
mutation dispatches, not only printed results.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest
from agentic_intent_gate.runtime import (
    EXTERNAL_PAYLOAD_REVISION,
    ActionCommand,
    AdmissionCommand,
    ApprovalChoice,
    ApprovalDecisionRequest,
    AttemptContext,
    ExecutionSpec,
    Outcome,
)

from trafficfuse.domain.evidence import SOURCE_FIXTURE, SOURCE_LIVE
from trafficfuse.infrastructure.governance import (
    CAPABILITY_FILE,
    RECEIPTS_FILE,
    open_governance,
    provision_profile,
)
from trafficfuse.infrastructure.reroute import (
    STATE_DISPATCHING,
    STATE_NOT_DISPATCHED,
    STATE_SUCCEEDED,
    STATE_UNKNOWN,
    EvidenceAnchor,
    PatchResponse,
    ReceiptStore,
    RerouteExecutor,
    binding_for,
)
from trafficfuse.infrastructure.simulation import SimulatedLab

DIGEST = "cd" * 32


class FaultyPort:
    """Simulation lab with injected faults on the mutation path."""

    def __init__(self, lab: SimulatedLab, *, patch="ok", post_read="ok", read="ok"):
        self.lab, self.patch, self.post_read, self.read = lab, patch, post_read, read
        self.source = lab.source
        self.reads = 0

    async def read_service(self, *, timeout):
        self.reads += 1
        if self.read == "fail":
            raise RuntimeError("API unreachable")
        snapshot = await self.lab.read_service(timeout=timeout)
        if self.reads > 1 and self.post_read == "mismatch":
            return replace(snapshot, selector={"app": "trafficfuse", "version": "faulty"})
        if self.reads > 1 and self.post_read == "fail":
            raise RuntimeError("re-read failed")
        return snapshot

    async def patch_selector(self, patch, *, timeout):
        if self.patch == "timeout":
            self.lab.patch_calls += 1
            return PatchResponse(False, None, "kubectl timed out")
        if self.patch == "raise":
            self.lab.patch_calls += 1
            raise RuntimeError("connection reset after send")
        if self.patch == "hang":
            self.lab.patch_calls += 1
            await asyncio.sleep(30)
        return await self.lab.patch_selector(patch, timeout=timeout)


def _arguments(lab, **overrides):
    snap = lab.snapshot()
    value = {
        "cluster": "trafficfuse-lab",
        "namespace": "trafficfuse-system",
        "service": "trafficfuse-live",
        "service_uid": snap.uid,
        "expected_resource_version": snap.resource_version,
        "from_version": "faulty",
        "to_version": "healthy",
        "evidence_digest": DIGEST,
    }
    value.update(overrides)
    return value


@pytest.fixture()
def unit(tmp_path):
    lab = SimulatedLab(SOURCE_FIXTURE, artifacts_root=tmp_path / "a")
    binding = binding_for("offline-sim-reroute", str(uuid4()))
    ReceiptStore.provision(tmp_path / "receipts.sqlite3", binding)
    receipts = ReceiptStore(tmp_path / "receipts.sqlite3", binding)
    snap = lab.snapshot()
    anchors = {DIGEST: EvidenceAnchor(DIGEST, SOURCE_FIXTURE, snap.uid, snap.resource_version, "faulty", True)}

    def build(port=None):
        return RerouteExecutor(binding, receipts, port or lab, anchors.get)

    return lab, binding, receipts, anchors, build


def _spec(binding, lab, **overrides):
    return ExecutionSpec(
        binding.tool_name,
        binding.tool_version,
        binding.target,
        _arguments(lab, **overrides.pop("arguments", {})),
        overrides.pop("adapter_id", binding.adapter_id),
        overrides.pop("binding_revision", binding.binding_revision),
        overrides.pop("execution_scope", binding.execution_scope),
        overrides.pop("identity_revision", EXTERNAL_PAYLOAD_REVISION),
    )


def _run(executor, spec, effect_key=None, *, budget=5.0):
    async def go():
        loop = asyncio.get_running_loop()
        context = AttemptContext(str(uuid4()), loop.time() + budget, asyncio.Event())
        return await executor.execute(spec, effect_key or str(uuid4()), context)

    return asyncio.run(go())


def test_success_persists_receipt_and_switches_once(unit):
    lab, binding, receipts, _, build = unit
    key = str(uuid4())
    outcome = _run(build(), _spec(binding, lab), key)
    assert outcome.category is Outcome.SUCCESS and outcome.effect_key == key
    assert lab.patch_calls == 1 and lab.snapshot().selector["version"] == "healthy"
    (record,) = receipts.records()
    assert (record.state, record.receipt) == (STATE_SUCCEEDED, outcome.receipt_reference)


def test_replayed_effect_key_returns_original_receipt_without_second_dispatch(unit):
    lab, binding, receipts, _, build = unit
    spec, key = _spec(binding, lab), str(uuid4())
    first = _run(build(), spec, key)
    second = _run(build(), spec, key)
    assert second.category is Outcome.SUCCESS and second.receipt_reference == first.receipt_reference
    assert lab.patch_calls == 1


def test_same_key_different_payload_is_a_confirmed_conflict(unit):
    lab, binding, receipts, _, build = unit
    key = str(uuid4())
    _run(build(), _spec(binding, lab), key)
    other = _spec(binding, lab, arguments={"expected_resource_version": "1"})
    outcome = _run(build(), other, key)
    assert outcome.category is Outcome.CONFIRMED_FAILURE and outcome.no_effect_reason == "PAYLOAD_CONFLICT"
    assert lab.patch_calls == 1


@pytest.mark.parametrize(
    "override",
    [
        {"adapter_id": "kind-kubectl-reroute"},
        {"binding_revision": "reroute-v2"},
        {"execution_scope": "00000000-0000-4000-8000-000000000000"},
        {"identity_revision": "demo-note-payload-v1"},
        {"arguments": {"to_version": "faulty"}},
        {"arguments": {"namespace": "kube-system"}},
    ],
)
def test_wrong_identity_or_input_is_rejected_before_any_io(unit, override):
    lab, binding, receipts, _, build = unit
    outcome = _run(build(), _spec(binding, lab, **override))
    assert outcome.category is Outcome.CONFIRMED_FAILURE and outcome.no_effect_reason == "INPUT_REJECTED"
    assert lab.patch_calls == 0 and receipts.records() == []


def test_store_identity_mismatch_is_confirmed_no_effect(unit, tmp_path):
    lab, binding, receipts, anchors, _ = unit
    with closing(sqlite3.connect(tmp_path / "receipts.sqlite3")) as db:
        db.execute("UPDATE identity SET binding='tampered'")
        db.commit()
    executor = RerouteExecutor(binding, receipts, lab, anchors.get)
    outcome = _run(executor, _spec(binding, lab))
    assert outcome.no_effect_reason == "STORE_IDENTITY_MISMATCH"
    assert lab.patch_calls == 0


@pytest.mark.parametrize("state", [STATE_DISPATCHING, STATE_UNKNOWN, STATE_NOT_DISPATCHED])
def test_fenced_effect_key_is_never_dispatched_again(unit, state):
    lab, binding, receipts, _, build = unit
    spec, key = _spec(binding, lab), str(uuid4())
    receipts.insert(
        effect_key=key, payload_digest=spec.payload_digest(), attempt_id=str(uuid4()), state=state,
        service_uid=lab.uid, from_selector="{}", to_selector="{}", pre_resource_version="635",
        detail="fenced by an earlier attempt", created_at="2026-10-19T00:00:00+00:00",
    )
    outcome = _run(build(), spec, key)
    assert outcome.category is Outcome.UNKNOWN_OUTCOME
    assert lab.patch_calls == 0


@pytest.mark.parametrize("mode", ["timeout", "raise"])
def test_ambiguous_dispatch_is_unknown_and_never_retried(unit, mode):
    lab, binding, receipts, _, build = unit
    outcome = _run(build(FaultyPort(lab, patch=mode)), _spec(binding, lab))
    assert outcome.category is Outcome.UNKNOWN_OUTCOME
    assert lab.patch_calls == 1  # exactly one attempt, no retry
    (record,) = receipts.records()
    assert record.state == STATE_UNKNOWN and record.receipt is None


@pytest.mark.parametrize("mode", ["mismatch", "fail"])
def test_unconfirmed_effect_after_dispatch_is_unknown(unit, mode):
    lab, binding, receipts, _, build = unit
    outcome = _run(build(FaultyPort(lab, post_read=mode)), _spec(binding, lab))
    assert outcome.category is Outcome.UNKNOWN_OUTCOME
    (record,) = receipts.records()
    assert record.state == STATE_UNKNOWN


def test_unverifiable_precondition_is_refused_before_dispatch(unit):
    lab, binding, receipts, _, build = unit
    outcome = _run(build(FaultyPort(lab, read="fail")), _spec(binding, lab))
    assert outcome.no_effect_reason == "INPUT_REJECTED"
    assert lab.patch_calls == 0


def test_deadline_before_dispatch_records_not_dispatched(unit):
    lab, binding, receipts, _, build = unit
    outcome = _run(build(), _spec(binding, lab), budget=0.5)
    assert outcome.category is Outcome.UNKNOWN_OUTCOME
    assert lab.patch_calls == 0


def test_evidence_from_another_environment_is_refused(unit):
    lab, binding, receipts, anchors, build = unit
    snap = lab.snapshot()
    anchors[DIGEST] = EvidenceAnchor(DIGEST, SOURCE_LIVE, snap.uid, snap.resource_version, "faulty", True)
    outcome = _run(build(), _spec(binding, lab))
    assert outcome.no_effect_reason == "INPUT_REJECTED"
    assert "execution environment" in receipts.records()[0].detail
    assert lab.patch_calls == 0


# --- through the gateway: concurrency, timeout and restart ---------------------------


def _authorized_action(gov, lab, header):
    from trafficfuse.infrastructure.governance import StagedProposalProvider  # noqa: F401

    plan = {
        "goal": "Route live traffic to the healthy version",
        "target": "trafficfuse-live",
        "steps": [{"tool_name": "reroute_service_traffic", "tool_version": "1", "arguments": _arguments(lab)}],
        "risk_class": "SIDE_EFFECT",
    }

    async def flow():
        _, intent = gov.provider.stage(json.dumps(plan), "test")
        run = await gov.runtime.run_service.create(intent)
        run = gov.runtime.run_service.decide(run.run_id, ApprovalDecisionRequest(ApprovalChoice.APPROVE, 4))
        principal = gov.runtime.verifier.verify(header)
        action = gov.runtime.gateway.admit(AdmissionCommand(run.run_id, 5, principal.subject_id, str(uuid4())), principal).action
        action = gov.runtime.gateway.authorize(ActionCommand(action.action_id, 1, str(uuid4())), principal).action
        return principal, action

    return asyncio.run(flow())


@pytest.fixture()
def gate(tmp_path):
    directory = tmp_path / "profile"
    provision_profile(directory, "simulation")
    lab = SimulatedLab(SOURCE_FIXTURE, artifacts_root=tmp_path / "a")
    snap = lab.snapshot()
    anchors = {DIGEST: EvidenceAnchor(DIGEST, SOURCE_FIXTURE, snap.uid, snap.resource_version, "faulty", True)}
    header = "Bearer " + (directory / CAPABILITY_FILE).read_text("ascii")
    return directory, lab, anchors, header


def test_concurrent_executions_dispatch_exactly_once(gate):
    directory, lab, anchors, header = gate
    with open_governance(directory, "simulation", mutation=lab, evidence_lookup=anchors.get) as gov:
        principal, action = _authorized_action(gov, lab, header)

        async def race():
            commands = [ActionCommand(action.action_id, 2, str(uuid4())) for _ in range(4)]
            return await asyncio.gather(
                *(gov.runtime.gateway.execute(c, principal) for c in commands), return_exceptions=True
            )

        results = asyncio.run(race())
    succeeded = [r for r in results if not isinstance(r, Exception)]
    assert len(succeeded) == 1 and succeeded[0].action.state.value == "Succeeded"
    assert all(getattr(r, "code", None) is not None for r in results if isinstance(r, Exception))
    assert lab.patch_calls == 1


def test_adapter_budget_overrun_is_terminal_unknown(gate):
    directory, lab, anchors, header = gate
    port = FaultyPort(lab, patch="hang")
    with open_governance(directory, "simulation", mutation=port, evidence_lookup=anchors.get) as gov:
        principal, action = _authorized_action(gov, lab, header)
        result = asyncio.run(gov.runtime.gateway.execute(ActionCommand(action.action_id, 2, str(uuid4())), principal))
        assert result.action.state.value == "Unknown"
        again = gov.runtime.actions.get(action.action_id)
    assert again.state.value == "Unknown"
    assert lab.patch_calls == 1  # one attempt; the gate never retries Unknown


def test_restart_after_durable_claim_recovers_to_unknown_without_effect(gate):
    directory, lab, anchors, header = gate
    with open_governance(directory, "simulation", mutation=lab, evidence_lookup=anchors.get) as gov:
        principal, action = _authorized_action(gov, lab, header)
        # The process "dies" right after the durable claim, before any dispatch.
        gov.runtime.actions.claim(ActionCommand(action.action_id, 2, str(uuid4())), principal, gov.runtime.gateway.policy)
        assert gov.runtime.actions.get(action.action_id).state.value == "Executing"
    with open_governance(directory, "simulation", mutation=lab, evidence_lookup=anchors.get) as gov:
        recovered = gov.runtime.actions.get(action.action_id)
        principal = gov.runtime.verifier.verify(header)
        with pytest.raises(Exception) as caught:
            asyncio.run(gov.runtime.gateway.execute(ActionCommand(action.action_id, 3, str(uuid4())), principal))
    assert recovered.state.value == "Unknown"
    assert getattr(caught.value, "code", None) is not None
    assert lab.patch_calls == 0  # recovery never replays the effect
    with closing(sqlite3.connect(directory / RECEIPTS_FILE)) as db:
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0
