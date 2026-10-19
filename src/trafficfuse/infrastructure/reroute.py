"""The one trusted SIDE_EFFECT tool: ``reroute_service_traffic`` v1.

Registered with Agentic Intent Gate at startup by trusted host code only
(``trafficfuse.infrastructure.governance``). The gate owns Plan validation,
exact Plan-Approval binding, admission, independent Authorization, the durable
claim and the Action ledger; this module owns only the consumer side:

* the closed, bounded argument schema and a pure semantic rule;
* a consumer-owned SQLite **receipt store** (separate from the gate) that
  binds the core's effect key to the full payload digest and a unique receipt,
  with a durable *dispatch fence* written before the mutation leaves the
  process;
* the executor, which re-validates complete identity and inputs, checks the
  Plan's evidence anchor, verifies live preconditions (UID, resourceVersion,
  exact selector, ownership labels), dispatches exactly one JSON patch with
  server-side ``test`` preconditions, independently re-reads the Service and
  only then persists a receipt.

Outcome classification (AIG adapter guide):

* proven no-effect before dispatch → ``CONFIRMED_FAILURE`` (``INPUT_REJECTED``,
  ``PAYLOAD_CONFLICT`` or ``STORE_IDENTITY_MISMATCH``);
* confirmed by the API server response **and** an independent re-read →
  ``SUCCESS`` with a UUID receipt;
* anything ambiguous after dispatch (timeout, kill, error, unexpected
  response, failed re-read, late receipt) → ``UNKNOWN_OUTCOME``. Never
  retried, never reconciled, never replayed. A fenced effect key that is
  not ``SUCCEEDED`` is never dispatched again.

A recorded success is *not* proof of recovery; TrafficFuse verifies service
health independently with a new live probe after the Action finishes.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID, uuid4

from agentic_intent_gate.runtime import (
    EXTERNAL_PAYLOAD_REVISION,
    Outcome,
    ToolBinding,
    ToolOutcome,
)

from trafficfuse.domain.evidence import SOURCE_LIVE, ServiceSnapshot, service_from_k8s
from trafficfuse.infrastructure import kube
from trafficfuse.infrastructure.kube import (
    LIVE_SERVICE,
    SELECTOR_APP,
    SelectorPatch,
)
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    MANAGED_BY_LABEL_KEY,
    NAMESPACE,
    OWNERSHIP_LABEL_KEY,
    OWNERSHIP_LABEL_VALUE,
    SafetyViolation,
)

TOOL_NAME = "reroute_service_traffic"
TOOL_VERSION = "1"
TARGET = LIVE_SERVICE
BINDING_REVISION = "reroute-v1"
ADAPTER_LIVE = "kind-kubectl-reroute"
ADAPTER_SIMULATION = "offline-sim-reroute"
POLICY_REVISION = "trafficfuse-policy-v1"
SUBJECT_ID = "trafficfuse-operator"
TOOL_DESCRIPTION = (
    "Switch the trafficfuse-live Service selector from the faulty version to the "
    "healthy version in the disposable trafficfuse-lab Kind cluster: exactly one "
    "bounded change, guarded by UID, resourceVersion and selector preconditions."
)

_END = "(?![\\s\\S])"  # end anchor that also refuses a trailing newline


def _exact(value: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": len(value),
        "maxLength": len(value),
        "pattern": f"^{re.escape(value)}{_END}",
    }


ARGUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "cluster",
        "namespace",
        "service",
        "service_uid",
        "expected_resource_version",
        "from_version",
        "to_version",
        "evidence_digest",
    ],
    "properties": {
        "cluster": _exact(CLUSTER_NAME),
        "namespace": _exact(NAMESPACE),
        "service": _exact(LIVE_SERVICE),
        "service_uid": {
            "type": "string",
            "minLength": 36,
            "maxLength": 36,
            "pattern": f"^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}{_END}",
        },
        "expected_resource_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 20,
            "pattern": f"^[0-9]{{1,20}}{_END}",
        },
        "from_version": _exact("faulty"),
        "to_version": _exact("healthy"),
        "evidence_digest": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": f"^[0-9a-f]{{64}}{_END}",
        },
    },
}
ARGUMENT_KEYS = frozenset(ARGUMENT_SCHEMA["properties"])

_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_RV = re.compile(r"[0-9]{1,20}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def argument_problem(arguments: Mapping[str, Any], target: str) -> str | None:
    """Pure, deterministic check shared by the AIG semantic rule and the executor."""
    if set(arguments) != ARGUMENT_KEYS or any(type(v) is not str for v in arguments.values()):
        return "ARGUMENT_SET"
    if target != TARGET or arguments["service"] != target:
        return "TARGET"
    if (arguments["cluster"], arguments["namespace"]) != (CLUSTER_NAME, NAMESPACE):
        return "SCOPE"
    if (arguments["from_version"], arguments["to_version"]) != ("faulty", "healthy"):
        return "TRANSITION"
    if not _UID.fullmatch(arguments["service_uid"]):
        return "SERVICE_UID"
    if not _RV.fullmatch(arguments["expected_resource_version"]):
        return "RESOURCE_VERSION"
    if not _DIGEST.fullmatch(arguments["evidence_digest"]):
        return "EVIDENCE_DIGEST"
    return None


def semantic_rule(arguments: Mapping[str, Any], target: str) -> str | None:
    """AIG ``semantic_rule``: ``None`` accepts; any other value rejects."""
    return argument_problem(arguments, target)


_EXPECTED_VALUES = {
    "cluster": CLUSTER_NAME,
    "namespace": NAMESPACE,
    "service": LIVE_SERVICE,
    "from_version": "faulty",
    "to_version": "healthy",
}


def explain_plan(plan: Mapping[str, Any]) -> list[str]:
    """Human-readable reasons a proposal violates the reviewed tool contract.

    Explanation only: the authoritative decision is the gate's PlanRejected Run.
    """
    reasons: list[str] = []
    if plan.get("target") != TARGET:
        reasons.append(f"target {plan.get('target')!r} is not the allowlisted Service {TARGET!r}")
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    if len(steps) != 1:
        reasons.append(f"{len(steps)} steps proposed; exactly one bounded step is allowed")
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        if (step.get("tool_name"), step.get("tool_version")) != (TOOL_NAME, TOOL_VERSION):
            reasons.append(f"step {index + 1}: tool {step.get('tool_name')!r} is not registered with the gate")
            continue
        arguments = step.get("arguments") if isinstance(step.get("arguments"), Mapping) else {}
        extra = sorted(set(arguments) - ARGUMENT_KEYS)
        if extra:
            reasons.append(
                f"step {index + 1}: unexpected argument(s) {', '.join(extra)} — a proposal can never "
                "supply scopes, credentials, budgets or other authority"
            )
        missing = sorted(ARGUMENT_KEYS - set(arguments))
        if missing:
            reasons.append(f"step {index + 1}: missing argument(s) {', '.join(missing)}")
        for key, expected in _EXPECTED_VALUES.items():
            if key in arguments and arguments[key] != expected:
                reasons.append(f"step {index + 1}: {key} {arguments[key]!r} is outside the reviewed contract (allowed: {expected!r})")
    if plan.get("risk_class") != "SIDE_EFFECT":
        reasons.append(f"risk_class {plan.get('risk_class')!r} misstates a traffic mutation (must be SIDE_EFFECT)")
    return reasons


def binding_for(adapter_id: str, execution_scope: str) -> ToolBinding:
    if adapter_id not in (ADAPTER_LIVE, ADAPTER_SIMULATION):
        raise SafetyViolation(f"unknown reroute adapter {adapter_id!r}")
    return ToolBinding(TOOL_NAME, TOOL_VERSION, adapter_id, BINDING_REVISION, TARGET, execution_scope)


def binding_identity(binding: ToolBinding) -> str:
    return json.dumps(vars(binding), sort_keys=True)


# --- evidence anchor -------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceAnchor:
    """Trusted host facts about the evidence bundle a Plan cites."""

    digest: str
    source: str
    service_uid: str
    resource_version: str
    from_version: str
    remediation_justified: bool
    stale: bool = False  # evidence older than the host freshness window


EvidenceLookup = Callable[[str], "EvidenceAnchor | None"]


# --- mutation port -----------------------------------------------------------------


@dataclass(frozen=True)
class PatchResponse:
    completed: bool  # the API server answered (exit 0) within the time allowed
    snapshot: ServiceSnapshot | None
    detail: str


class ServiceMutationPort(Protocol):
    source: str

    async def read_service(self, *, timeout: float) -> ServiceSnapshot: ...

    async def patch_selector(self, patch: SelectorPatch, *, timeout: float) -> PatchResponse: ...


# --- receipt store -------------------------------------------------------------------

STATE_DISPATCHING = "DISPATCHING"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_REJECTED = "REJECTED"
STATE_UNKNOWN = "UNKNOWN"
STATE_NOT_DISPATCHED = "NOT_DISPATCHED"

_RECEIPT_DDL = """
CREATE TABLE identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    binding TEXT NOT NULL
) STRICT;
CREATE TABLE effects (
    effect_key TEXT PRIMARY KEY NOT NULL,
    payload_digest TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN
        ('DISPATCHING','SUCCEEDED','REJECTED','UNKNOWN','NOT_DISPATCHED')),
    receipt TEXT UNIQUE,
    service_uid TEXT NOT NULL,
    from_selector TEXT NOT NULL,
    to_selector TEXT NOT NULL,
    pre_resource_version TEXT NOT NULL,
    post_resource_version TEXT,
    observed_selector TEXT,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    completed_at TEXT
) STRICT;
"""


class StoreIdentityMismatch(Exception):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EffectRecord:
    effect_key: str
    payload_digest: str
    attempt_id: str
    state: str
    receipt: str | None
    service_uid: str
    from_selector: str
    to_selector: str
    pre_resource_version: str
    post_resource_version: str | None
    observed_selector: str | None
    detail: str
    created_at: str
    dispatched_at: str | None
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


class ReceiptStore:
    """Consumer-owned durable effect/receipt store, bound to one binding."""

    def __init__(self, path: Path, binding: ToolBinding) -> None:
        self.path = Path(path)
        self.binding = binding

    @staticmethod
    def provision(path: Path, binding: ToolBinding) -> None:
        path = Path(path)
        path.touch(exist_ok=False)  # explicit exclusive creation; never reset
        with closing(sqlite3.connect(path)) as db:
            db.executescript(_RECEIPT_DDL)
            db.execute("INSERT INTO identity VALUES (1, ?)", (binding_identity(binding),))
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=1, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _check_identity(self, db: sqlite3.Connection) -> None:
        rows = db.execute("SELECT binding FROM identity WHERE singleton=1").fetchall()
        if [tuple(r) for r in rows] != [(binding_identity(self.binding),)]:
            raise StoreIdentityMismatch()

    def lookup(self, effect_key: str) -> EffectRecord | None:
        with closing(self._connect()) as db:
            self._check_identity(db)
            row = db.execute("SELECT * FROM effects WHERE effect_key=?", (effect_key,)).fetchone()
            return EffectRecord(**dict(row)) if row else None

    def records(self) -> list[EffectRecord]:
        with closing(self._connect()) as db:
            self._check_identity(db)
            rows = db.execute("SELECT * FROM effects ORDER BY created_at").fetchall()
            return [EffectRecord(**dict(row)) for row in rows]

    def insert(self, **values: Any) -> None:
        """Insert-only: an existing effect key is never overwritten."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_identity(db)
            columns = ",".join(values)
            marks = ",".join("?" for _ in values)
            db.execute(f"INSERT INTO effects({columns}) VALUES ({marks})", tuple(values.values()))
            db.execute("COMMIT")

    def transition(self, effect_key: str, from_state: str, **values: Any) -> None:
        """Compare-and-set from the fenced state; refuses any other edge."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_identity(db)
            assignments = ",".join(f"{key}=?" for key in values)
            changed = db.execute(
                f"UPDATE effects SET {assignments} WHERE effect_key=? AND state=?",
                (*values.values(), effect_key, from_state),
            ).rowcount
            if changed != 1:
                db.execute("ROLLBACK")
                raise RuntimeError("receipt store transition refused")
            db.execute("COMMIT")


# --- executor --------------------------------------------------------------------------


def _selector(version: str) -> str:
    return json.dumps({"app": SELECTOR_APP, "version": version}, sort_keys=True)


def _canonical_uuid4(value: str) -> bool:
    try:
        return str(UUID(value, version=4)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class RerouteExecutor:
    """AIG ``ToolExecutor`` for the single bounded live-Service selector switch."""

    POST_DISPATCH_RESERVE_SECONDS = 0.6

    def __init__(
        self,
        binding: ToolBinding,
        receipts: ReceiptStore,
        mutation: ServiceMutationPort,
        evidence_lookup: EvidenceLookup,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if receipts.binding != binding:
            raise SafetyViolation("receipt store is bound to a different tool binding")
        self.binding = binding
        self.receipts = receipts
        self.mutation = mutation
        self.evidence_lookup = evidence_lookup
        self._clock = clock

    def _remaining(self, context) -> float:
        now = self._clock() if self._clock else asyncio.get_running_loop().time()
        return context.deadline - now

    async def execute(self, spec, effect_key: str, context) -> ToolOutcome:
        payload = spec.payload_digest()

        def evidence(category: Outcome, *, receipt: str | None = None, reason: str | None = None):
            return ToolOutcome(
                category, effect_key, spec.execution_scope, spec.target, payload, receipt, reason
            )

        def rejected():
            return evidence(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")

        unknown = ToolOutcome(Outcome.UNKNOWN_OUTCOME)
        arguments = dict(spec.arguments)

        # 1. Complete identity and exact bounded inputs (pure; no I/O).
        if (
            not self.binding.matches(spec)
            or spec.identity_revision != EXTERNAL_PAYLOAD_REVISION
            or argument_problem(arguments, spec.target) is not None
            or not _canonical_uuid4(effect_key)
            or not _canonical_uuid4(context.attempt_id)
        ):
            return rejected()
        if context.expired():
            return unknown

        uid = arguments["service_uid"]
        expected_rv = arguments["expected_resource_version"]
        from_version, to_version = arguments["from_version"], arguments["to_version"]
        base = {
            "effect_key": effect_key,
            "payload_digest": payload,
            "attempt_id": context.attempt_id,
            "service_uid": uid,
            "from_selector": _selector(from_version),
            "to_selector": _selector(to_version),
            "pre_resource_version": expected_rv,
            "created_at": _utc(),
        }

        # 2. Receipt store identity and effect-key replay/conflict handling.
        try:
            existing = self.receipts.lookup(effect_key)
        except StoreIdentityMismatch:
            return evidence(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
        except Exception:
            return unknown
        if existing is not None:
            if existing.payload_digest != payload:
                return evidence(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
            if existing.state == STATE_SUCCEEDED and existing.receipt:
                return evidence(Outcome.SUCCESS, receipt=existing.receipt)
            if existing.state == STATE_REJECTED:
                return rejected()
            return unknown  # fenced/ambiguous: never dispatch the same effect twice

        def refuse(detail: str) -> ToolOutcome:
            try:
                self.receipts.insert(**base, state=STATE_REJECTED, detail=detail, completed_at=_utc())
            except Exception:
                return unknown
            return rejected()

        # 3. The Plan must cite a trusted evidence bundle that justifies acting.
        anchor = self.evidence_lookup(arguments["evidence_digest"])
        if anchor is None:
            return refuse("evidence digest is not a TrafficFuse evidence bundle")
        if anchor.source != self.mutation.source:
            return refuse("evidence source does not match the execution environment")
        if (anchor.service_uid, anchor.resource_version, anchor.from_version) != (
            uid,
            expected_rv,
            from_version,
        ):
            return refuse("Plan preconditions do not match the cited evidence")
        if anchor.stale:
            return refuse("cited evidence is older than the freshness window; re-detect and re-approve")
        if not anchor.remediation_justified:
            return refuse("cited evidence does not show a degraded live Service")

        # 4. Live preconditions, read before anything is dispatched.
        if self._remaining(context) <= self.POST_DISPATCH_RESERVE_SECONDS * 2:
            return unknown
        try:
            current = await self.mutation.read_service(
                timeout=min(2.0, self._remaining(context) - self.POST_DISPATCH_RESERVE_SECONDS * 2)
            )
        except SafetyViolation as exc:
            return refuse(f"precondition read refused: {exc}")
        except Exception:
            return refuse("live precondition could not be verified; nothing dispatched")
        if (current.name, current.namespace) != (LIVE_SERVICE, NAMESPACE):
            return refuse("unexpected Service identity")
        if current.labels.get(OWNERSHIP_LABEL_KEY) != OWNERSHIP_LABEL_VALUE or current.labels.get(
            MANAGED_BY_LABEL_KEY
        ) != OWNERSHIP_LABEL_VALUE:
            return refuse("Service lacks TrafficFuse ownership labels")
        if current.uid != uid:
            return refuse("stale target: Service UID changed since the Plan was approved")
        if current.resource_version != expected_rv:
            return refuse(
                "stale target: Service resourceVersion "
                f"{current.resource_version} != approved {expected_rv}"
            )
        if dict(current.selector) != {"app": SELECTOR_APP, "version": from_version}:
            return refuse("unexpected current selector; refusing to act on a changed route")

        # 5. Durable dispatch fence, committed before the mutation leaves.
        try:
            self.receipts.insert(**base, state=STATE_DISPATCHING, detail="dispatch fenced")
        except Exception:
            return unknown
        if context.expired() or self._remaining(context) <= self.POST_DISPATCH_RESERVE_SECONDS:
            self._settle(effect_key, STATE_DISPATCHING, STATE_NOT_DISPATCHED, "deadline before dispatch")
            return unknown

        # 6. Exactly one JSON patch; the API server re-checks every precondition.
        patch = SelectorPatch(uid, expected_rv, from_version, to_version)
        self._settle(effect_key, STATE_DISPATCHING, STATE_DISPATCHING, "dispatched", dispatched_at=_utc())
        try:
            response = await self.mutation.patch_selector(
                patch,
                timeout=max(0.1, self._remaining(context) - self.POST_DISPATCH_RESERVE_SECONDS),
            )
        except Exception:
            self._settle(effect_key, STATE_DISPATCHING, STATE_UNKNOWN, "dispatch raised; outcome unknown")
            return unknown
        confirmed = (
            response.completed
            and response.snapshot is not None
            and response.snapshot.uid == uid
            and dict(response.snapshot.selector) == {"app": SELECTOR_APP, "version": to_version}
            and response.snapshot.resource_version != expected_rv
        )
        if not confirmed:
            self._settle(effect_key, STATE_DISPATCHING, STATE_UNKNOWN, f"unconfirmed: {response.detail}")
            return unknown

        # 7. Independent re-read of the effect, inside the adapter budget.
        try:
            after = await self.mutation.read_service(
                timeout=max(0.1, min(2.0, self._remaining(context) - 0.1))
            )
        except Exception:
            self._settle(effect_key, STATE_DISPATCHING, STATE_UNKNOWN, "post-dispatch re-read failed")
            return unknown
        if after.uid != uid or dict(after.selector) != {"app": SELECTOR_APP, "version": to_version}:
            self._settle(effect_key, STATE_DISPATCHING, STATE_UNKNOWN, "re-read does not show the effect")
            return unknown

        # 8. Durable receipt; a late return is Unknown at the gate regardless.
        receipt = str(uuid4())
        try:
            self.receipts.transition(
                effect_key,
                STATE_DISPATCHING,
                state=STATE_SUCCEEDED,
                receipt=receipt,
                post_resource_version=after.resource_version,
                observed_selector=json.dumps(dict(after.selector), sort_keys=True),
                detail="API server confirmed; independent re-read confirmed",
                completed_at=_utc(),
            )
        except Exception:
            return unknown
        if context.expired():
            return unknown
        return evidence(Outcome.SUCCESS, receipt=receipt)

    def _settle(self, effect_key: str, from_state: str, to_state: str, detail: str, **extra: Any) -> None:
        try:
            self.receipts.transition(
                effect_key,
                from_state,
                state=to_state,
                detail=detail,
                **({"completed_at": _utc()} if to_state != STATE_DISPATCHING else {}),
                **extra,
            )
        except Exception:
            pass  # the fence row remains: the effect key stays non-dispatchable


# --- live mutation port ------------------------------------------------------------


class KubectlServiceMutation:
    """Live port: the only code path that can send the governed patch."""

    source = SOURCE_LIVE

    async def read_service(self, *, timeout: float) -> ServiceSnapshot:
        result = await kube.run_kubectl(kube.read_command(kube.GET_SERVICE), timeout=timeout)
        if not result.ok:
            raise RuntimeError("live Service read failed")
        return service_from_k8s(result.json())

    async def patch_selector(self, patch: SelectorPatch, *, timeout: float) -> PatchResponse:
        result = await kube.run_kubectl(kube.patch_command(patch), timeout=timeout)
        if not result.ok:
            return PatchResponse(False, None, kube.redacted_error(result))
        try:
            return PatchResponse(True, service_from_k8s(result.json()), "patched")
        except Exception:
            return PatchResponse(False, None, "patch response was not a Service object")
