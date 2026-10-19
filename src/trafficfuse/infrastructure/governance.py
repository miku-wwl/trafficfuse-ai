"""Trusted Agentic Intent Gate composition for TrafficFuse (host code only).

Mirrors the reference consumer (``examples/consumer/app.py`` at v0.2.0): one
explicitly registered SIDE_EFFECT tool, a private capability token, a frozen
``BoundCapabilityAuthorization`` policy and the **default five-second**
execution budget (``execution_timeout_seconds`` is deliberately omitted).

Each *profile* (``live`` / ``simulation``) is a separate private directory with
its own fresh UUIDv4 execution scope, adapter identity, gate ledger, capability
token and receipt store, so simulation receipts can never authorize or be
confused with live effects. Model output and HTTP input can reach none of the
values configured here.

Provisioning is an explicit operator step (``provision_profile``); opening
never creates, migrates or resets anything.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence
from uuid import uuid4

from agentic_intent_gate.runtime import (
    BoundCapabilityAuthorization,
    CapabilityVerifier,
    ExecutableRegistry,
    Intent,
    ToolBinding,
    ToolCapability,
    open_runtime,
    provision_capability,
)

from trafficfuse.infrastructure.reroute import (
    ADAPTER_LIVE,
    ADAPTER_SIMULATION,
    ARGUMENT_SCHEMA,
    BINDING_REVISION,
    POLICY_REVISION,
    SUBJECT_ID,
    TARGET,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    TOOL_VERSION,
    EvidenceLookup,
    ReceiptStore,
    RerouteExecutor,
    ServiceMutationPort,
    binding_for,
    semantic_rule,
)
from trafficfuse.lab.lifecycle import SafetyViolation

PROFILE_ADAPTERS = {"live": ADAPTER_LIVE, "simulation": ADAPTER_SIMULATION}
OPERATIONS = ("read", "admit", "authorize", "execute")
IDENTITY_FILE = "identity.json"
CAPABILITY_FILE = "capability"
RECEIPTS_FILE = "receipts.sqlite3"
GATE_FILE = "gate.sqlite3"

INTENT_PREFIX = "TrafficFuse proposal "
_INTENT = re.compile(r"^TrafficFuse proposal ([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}):")


def provision_profile(directory: Path, profile: str) -> ToolBinding:
    """Explicit one-time provisioning of a new, empty private profile directory."""
    if profile not in PROFILE_ADAPTERS:
        raise SafetyViolation(f"unknown governance profile {profile!r}")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise SafetyViolation("provision only a new, empty private profile directory")
    binding = binding_for(PROFILE_ADAPTERS[profile], str(uuid4()))
    provision_capability(directory / CAPABILITY_FILE)
    (directory / IDENTITY_FILE).write_text(json.dumps(vars(binding), sort_keys=True), encoding="utf-8")
    ReceiptStore.provision(directory / RECEIPTS_FILE, binding)
    return binding


def load_binding(directory: Path, profile: str) -> ToolBinding:
    """Load the retained binding and refuse anything but the reviewed identity."""
    raw = json.loads((Path(directory) / IDENTITY_FILE).read_text("utf-8"))
    binding = ToolBinding(**raw)
    expected = (TOOL_NAME, TOOL_VERSION, PROFILE_ADAPTERS.get(profile), BINDING_REVISION, TARGET)
    actual = (
        binding.tool_name,
        binding.tool_version,
        binding.adapter_id,
        binding.binding_revision,
        binding.target,
    )
    if actual != expected:
        raise SafetyViolation("retained binding does not match the reviewed reroute identity")
    return binding


def build_capability(binding: ToolBinding) -> ToolCapability:
    return ToolCapability(
        binding,
        TOOL_DESCRIPTION,
        ARGUMENT_SCHEMA,
        target_argument="service",
        semantic_rule=semantic_rule,
    )


class StagedProposalProvider:
    """Consumer ``ModelProvider``: hands AIG the model-derived Plan text.

    The LLM diagnosis runs *before* the AIG Run (it can take tens of seconds
    on a CPU-only local model, beyond the gate's planning deadline). The Plan
    text composed from that diagnosis is staged under a one-time proposal id;
    ``generate_plan`` returns exactly that text, once, for the Intent naming
    the id. AIG then validates it like any other provider output. An unknown
    or reused id raises, which the gate records as a rejected planning
    attempt — never a guessed Plan.
    """

    def __init__(self) -> None:
        self._staged: dict[str, str] = {}

    def stage(self, raw_plan: str, description: str) -> tuple[str, Intent]:
        proposal_id = str(uuid4())
        self._staged[proposal_id] = raw_plan
        text = f"{INTENT_PREFIX}{proposal_id}: {description}"
        return proposal_id, Intent(text[:4000])

    def discard(self, proposal_id: str) -> None:
        self._staged.pop(proposal_id, None)

    async def generate_plan(self, intent, *, tools, plan_schema, timeout_seconds) -> str:
        match = _INTENT.match(intent.text)
        if match is None or match.group(1) not in self._staged:
            raise LookupError("no staged TrafficFuse proposal for this Intent")
        return self._staged.pop(match.group(1))


@dataclass(frozen=True)
class Governance:
    """The open composition, held for the whole application lifetime."""

    profile: str
    runtime: object
    binding: ToolBinding
    capability: ToolCapability
    executor: RerouteExecutor
    receipts: ReceiptStore
    provider: StagedProposalProvider

    def identity(self) -> Mapping[str, object]:
        b = self.binding
        return {
            "profile": self.profile,
            "tool_name": b.tool_name,
            "tool_version": b.tool_version,
            "target": b.target,
            "adapter_id": b.adapter_id,
            "binding_revision": b.binding_revision,
            "execution_scope": b.execution_scope,
            "policy_revision": POLICY_REVISION,
            "subject_id": SUBJECT_ID,
            "operations": list(OPERATIONS),
            "execution_budget_seconds": 5,
        }


@contextmanager
def open_governance(
    directory: Path,
    profile: str,
    *,
    mutation: ServiceMutationPort,
    evidence_lookup: EvidenceLookup,
    operations: Sequence[str] = OPERATIONS,
) -> Iterator[Governance]:
    directory = Path(directory).resolve()
    binding = load_binding(directory, profile)
    if mutation.source not in ("LIVE_KIND", "OFFLINE_SIMULATION", "TEST_FIXTURE"):
        raise SafetyViolation("unknown mutation port source")
    if (profile == "live") != (mutation.source == "LIVE_KIND"):
        raise SafetyViolation("the live profile requires the live mutation port, and only it")
    capability = build_capability(binding)
    receipts = ReceiptStore(directory / RECEIPTS_FILE, binding)
    executor = RerouteExecutor(binding, receipts, mutation, evidence_lookup)
    verifier = CapabilityVerifier(
        (directory / CAPABILITY_FILE).read_text("ascii"),
        subject_id=SUBJECT_ID,
        binding=binding,
        operations=tuple(operations),
    )
    policy = BoundCapabilityAuthorization(verifier, policy_revision=POLICY_REVISION)
    registry = ExecutableRegistry(executor, binding=binding)
    provider = StagedProposalProvider()
    with open_runtime(
        gate_path=directory / GATE_FILE,
        provider=provider,
        capability=capability,
        registry=registry,
        verifier=verifier,
        policy=policy,
        predecessor_stopped=True,
    ) as runtime:
        yield Governance(profile, runtime, binding, capability, executor, receipts, provider)
