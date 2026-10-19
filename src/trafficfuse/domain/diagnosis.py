"""Evidence-grounded incident diagnosis: prompt, output contract, validation.

Division of responsibility (two independent layers, both real):

1. **Grounding (TrafficFuse, here).** The model output must be a closed JSON
   object; every cited evidence id must exist in the bundle the model was
   shown; an action recommendation must cite at least one measured probe;
   text is bounded. Invented ids, extra keys (e.g. an attempt to supply a
   kubeconfig, scope, budget or approval) and malformed output are rejected
   *before* anything reaches the gate.
2. **Authority (Agentic Intent Gate).** The model's chosen service, namespace
   and versions are copied verbatim into the Plan; only the gate's strict
   tool schema and semantic rule decide whether they are acceptable. TrafficFuse
   deliberately does not pre-filter them, so an unsafe model proposal is
   refused — and recorded — by the gate itself.

Trusted host code adds only observed facts to the Plan (cluster name, the
Service UID and resourceVersion from evidence, the evidence bundle digest),
never identities, credentials, scopes, budgets or grant decisions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from trafficfuse.domain.evidence import EvidenceBundle

DIAGNOSIS_SCHEMA_VERSION = "trafficfuse-diagnosis-v1"
PROMPT_VERSION = "trafficfuse-diagnosis-prompt-v1"

ACTIONS = ("reroute_service_traffic", "no_action", "escalate_to_human")
CONFIDENCE = ("low", "medium", "high")
TEXT_LIMITS = {"incident_summary": 600, "likely_cause": 600, "uncertainty": 400}
PROPOSAL_KEYS = ("service", "namespace", "from_version", "to_version", "goal")
TOP_KEYS = (
    "incident_summary",
    "likely_cause",
    "evidence_refs",
    "confidence",
    "uncertainty",
    "recommended_action",
    "proposal",
)
_REF = re.compile(r"^EV-[1-9][0-9]?$")

# Ollama structured-output schema: shapes only. String *values* are left free
# on purpose — safety is enforced by validation and by the gate, not by
# trusting a decoding grammar.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "incident_summary": {"type": "string"},
        "likely_cause": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "uncertainty": {"type": "string"},
        "recommended_action": {"type": "string", "enum": list(ACTIONS)},
        "proposal": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in PROPOSAL_KEYS},
            "required": list(PROPOSAL_KEYS),
        },
    },
    "required": list(TOP_KEYS),
}

SYSTEM_PROMPT = (
    "You are TrafficFuse, an SRE incident analyst for a Kubernetes release. "
    "Reason ONLY from the numbered evidence items. Cite them by id (EV-n); never "
    "invent ids, numbers or resources. You cannot execute anything: you may only "
    "PROPOSE one remediation, which a human must approve and a separate operator "
    "must authorize. The single available remediation is reroute_service_traffic: "
    "switch the live Service selector from one version label to another. Prefer the "
    "smallest reversible action that the evidence supports. If evidence is "
    "insufficient, choose escalate_to_human. Be concise: each text field under 40 "
    "words. Output only the JSON object."
)


def render_prompt(bundle: EvidenceBundle) -> str:
    lines = [
        f"EVIDENCE BUNDLE {bundle.bundle_id} (source {bundle.source}; the lab workload "
        "is synthetic, measurements are real HTTP responses):",
    ]
    for item in bundle.items:
        lines.append(f"{item.evidence_id} [{item.kind}] {item.title}: {item.summary}")
    lines += [
        "",
        "REMEDIATION CATALOG: reroute_service_traffic(service, namespace, from_version, "
        "to_version) in cluster trafficfuse-lab. Live Service: trafficfuse-live in "
        "namespace trafficfuse-system. Version labels: faulty, healthy.",
        "",
        "Return JSON with keys: incident_summary, likely_cause, evidence_refs (ids you "
        "relied on), confidence (low|medium|high), uncertainty (what you are not sure "
        "about), recommended_action (reroute_service_traffic|no_action|escalate_to_human), "
        "proposal {service, namespace, from_version, to_version, goal}. If you do not "
        "recommend rerouting, still fill proposal with your best description.",
    ]
    return "\n".join(lines)


class DiagnosisRejected(ValueError):
    """The model output failed grounding/shape validation (fail closed)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class Diagnosis:
    incident_summary: str
    likely_cause: str
    evidence_refs: tuple[str, ...]
    confidence: str
    uncertainty: str
    recommended_action: str
    proposal: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_summary": self.incident_summary,
            "likely_cause": self.likely_cause,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "recommended_action": self.recommended_action,
            "proposal": dict(self.proposal),
        }


def _object_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DiagnosisRejected("DUPLICATE_KEY", f"duplicate key {key!r}")
        value[key] = item
    return value


def parse_diagnosis(raw: str, bundle: EvidenceBundle) -> Diagnosis:
    """Strictly parse and ground the model's JSON output against ``bundle``."""
    if not isinstance(raw, str) or not raw.strip():
        raise DiagnosisRejected("EMPTY_OUTPUT", "the model returned no content")
    if len(raw.encode("utf-8")) > 16 * 1024:
        raise DiagnosisRejected("OUTPUT_TOO_LARGE", "model output exceeds 16 KiB")
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs)
    except DiagnosisRejected:
        raise
    except ValueError as exc:
        raise DiagnosisRejected("MALFORMED_JSON", str(exc)[:200]) from None
    if not isinstance(value, dict):
        raise DiagnosisRejected("NOT_AN_OBJECT", "model output must be a JSON object")
    extra = sorted(set(value) - set(TOP_KEYS))
    missing = sorted(set(TOP_KEYS) - set(value))
    if extra:
        raise DiagnosisRejected("UNEXPECTED_FIELDS", f"model supplied fields outside the contract: {extra}")
    if missing:
        raise DiagnosisRejected("MISSING_FIELDS", f"missing {missing}")
    for key, limit in TEXT_LIMITS.items():
        text = value[key]
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise DiagnosisRejected("INVALID_TEXT", f"{key} must be non-empty text <= {limit} chars")
    if value["confidence"] not in CONFIDENCE:
        raise DiagnosisRejected("INVALID_CONFIDENCE", repr(value["confidence"])[:60])
    if value["recommended_action"] not in ACTIONS:
        raise DiagnosisRejected("UNSUPPORTED_ACTION", repr(value["recommended_action"])[:60])
    refs = value["evidence_refs"]
    if (
        not isinstance(refs, list)
        or not 1 <= len(refs) <= 12
        or any(not isinstance(r, str) or not _REF.fullmatch(r) for r in refs)
        or len(set(refs)) != len(refs)
    ):
        raise DiagnosisRejected("INVALID_REFERENCES", "evidence_refs must be 1-12 unique EV-n ids")
    unknown = [ref for ref in refs if bundle.item(ref) is None]
    if unknown:
        raise DiagnosisRejected(
            "UNGROUNDED_REFERENCE", f"cited evidence that does not exist in the bundle: {unknown}"
        )
    if value["recommended_action"] == "reroute_service_traffic" and not any(
        bundle.item(ref).kind == "probe" for ref in refs
    ):
        raise DiagnosisRejected(
            "UNGROUNDED_ACTION", "an action recommendation must cite measured probe evidence"
        )
    proposal = value["proposal"]
    if not isinstance(proposal, dict) or set(proposal) != set(PROPOSAL_KEYS):
        raise DiagnosisRejected("INVALID_PROPOSAL", "proposal must have exactly the contract keys")
    for key in PROPOSAL_KEYS:
        limit = 300 if key == "goal" else 64
        item = proposal[key]
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise DiagnosisRejected("INVALID_PROPOSAL", f"proposal.{key} must be text <= {limit} chars")
    return Diagnosis(
        incident_summary=value["incident_summary"].strip(),
        likely_cause=value["likely_cause"].strip(),
        evidence_refs=tuple(refs),
        confidence=value["confidence"],
        uncertainty=value["uncertainty"].strip(),
        recommended_action=value["recommended_action"],
        proposal={key: proposal[key].strip() for key in PROPOSAL_KEYS},
    )


# Plan fields the model chose vs. facts trusted host code bound from evidence.
MODEL_FIELDS = ("goal", "target", "service", "namespace", "from_version", "to_version")
HOST_FIELDS = ("cluster", "service_uid", "expected_resource_version", "evidence_digest")


def compose_plan(diagnosis: Diagnosis, bundle: EvidenceBundle) -> dict[str, Any]:
    """Build the AIG Plan proposal: model choices verbatim + observed facts."""
    service = bundle.service()
    if service is None:
        raise DiagnosisRejected("NO_SERVICE_EVIDENCE", "bundle has no live Service snapshot")
    proposal = diagnosis.proposal
    return {
        "goal": proposal["goal"][:512],
        "target": proposal["service"],
        "steps": [
            {
                "tool_name": "reroute_service_traffic",
                "tool_version": "1",
                "arguments": {
                    "cluster": bundle.cluster.get("cluster", ""),
                    "namespace": proposal["namespace"],
                    "service": proposal["service"],
                    "service_uid": service.uid,
                    "expected_resource_version": service.resource_version,
                    "from_version": proposal["from_version"],
                    "to_version": proposal["to_version"],
                    "evidence_digest": bundle.digest,
                },
            }
        ],
        "risk_class": "SIDE_EFFECT",
    }
