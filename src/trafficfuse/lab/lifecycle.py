"""Safety-guarded lifecycle planning and ownership verification for the
trafficfuse-lab cluster.

Phase 1A.1 (review finding P1) strengthens the ownership model. No function in
this module creates, deletes or mutates any cluster, context or Kubernetes
resource — mutating paths refuse even with authorization, until Phase 1B is
approved (docs/PHASE1-PREFLIGHT.md).

Isolation guarantees enforced offline:

* a dedicated, git-ignored kubeconfig is required (never the global one); the
  path must resolve to the **exact canonical repository-owned path**
  ``.runtime/kubeconfig/trafficfuse-lab.yaml`` under the trusted repository
  root (derived from this module's location), with symlink escapes, traversal
  and foreign repositories carrying an identical directory shape all refused;
* the exact expected context (``kind-trafficfuse-lab``) is required — the
  ambient context is never read or trusted;
* the kubeconfig *document* itself is verified: exact context and cluster
  entries, a local HTTPS API server, no embedded bearer tokens or foreign
  contexts, and a CA certificate fingerprint used as the stable identity
  anchor;
* the cluster must be exactly ``trafficfuse-lab`` — foreign clusters
  (``incident-lab``, ``pcp-dev``, ``playground-crd``, ``vmware-k8s``, ...)
  are refused by name;
* destructive operations additionally require a complete TrafficFuse
  **creation record** (written by TrafficFuse tooling at creation time) and a
  successful ownership-evidence verification. The record is *metadata, not
  cryptographic proof*: ownership is established by cross-checking **trusted
  live facts** (API server, CA fingerprint, exact kind node identities,
  ownership labels) against the record. Any missing or mismatched evidence
  refuses the operation;
* a matching cluster *name* alone is never treated as deletion authority;
* caller-supplied facts (a hand-written JSON file) can be *checked* but never
  authorize destruction. Trust is established by **execution**, not by
  declaration: the destructive gate performs the trusted read-only collection
  itself (``trafficfuse.lab.cluster_facts``) in one controlled operation and
  cross-checks the collected facts against the creation record. The Phase
  1A.2 provenance *string* (``source="collector"``) was removed in the Phase
  1B entry review: a caller-controlled string is not a provenance guarantee
  (docs/PHASE1B-ENTRY-REVIEW.md, finding P2-a).

Ownership facts are verified against facts **collected from the target
environment** through explicitly scoped read-only commands (see
``trafficfuse.lab.cluster_facts`` and docs/PHASE1B-RUNBOOK.md for the exact
Phase 1B protocol). This module never contacts a cluster.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

CLUSTER_NAME = "trafficfuse-lab"
EXPECTED_CONTEXT = "kind-trafficfuse-lab"
NAMESPACE = "trafficfuse-system"
OWNERSHIP_LABEL_KEY = "trafficfuse.io/owner"
OWNERSHIP_LABEL_VALUE = CLUSTER_NAME
MANAGED_BY_LABEL_KEY = "app.kubernetes.io/managed-by"

RECEIPT_DIRECTORY = ".runtime/state"
RECEIPT_FILENAME = "cluster-trafficfuse-lab.json"
DEDICATED_KUBECONFIG = ".runtime/kubeconfig/trafficfuse-lab.yaml"
RECORD_SCHEMA = "trafficfuse-ownership-record-v2"
RECORD_FIELDS = (
    "cluster",
    "context",
    "namespace",
    "api_server",
    "ca_fingerprint_sha256",
    "kind_node_names",
    "created_at",
    "created_by",
    "kubeconfig",
)

# Foreign clusters observed on the development machine: never valid targets.
KNOWN_FOREIGN_CLUSTERS = ("incident-lab", "pcp-dev", "playground-crd", "vmware-k8s")
KNOWN_FOREIGN_CONTEXTS = tuple(
    dict.fromkeys(
        (
            *(f"kind-{name}" for name in KNOWN_FOREIGN_CLUSTERS),
            "vmware-k8s",
        )
    )
)

# Exact kind node identities TrafficFuse owns. kind names the control-plane
# node "<cluster>-control-plane" by default; additional worker nodes must be
# listed explicitly in the creation record and verified exactly.
EXPECTED_NODE_NAMES = (f"{CLUSTER_NAME}-control-plane",)

# The dedicated kubeconfig must only ever point at a local API server: a
# disposable kind cluster on this workstation. Remote hosts are refused.
ALLOWED_API_HOSTS = ("127.0.0.1", "localhost", "::1")

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class SafetyViolation(Exception):
    """Raised when a lifecycle request violates a TrafficFuse safety boundary."""


def _check_dns1123(value: str, kind: str) -> None:
    if not _DNS1123_LABEL.fullmatch(value) or len(value) > 63:
        raise SafetyViolation(f"{kind} {value!r} is not a valid DNS-1123 label")


def validate_cluster_name(name: str) -> str:
    _check_dns1123(name, "cluster name")
    if name != CLUSTER_NAME:
        raise SafetyViolation(
            f"cluster {name!r} is not the TrafficFuse cluster {CLUSTER_NAME!r}; "
            "refusing to touch foreign or unknown clusters "
            f"(known foreign: {', '.join(KNOWN_FOREIGN_CLUSTERS)})"
        )
    return name


def validate_context(context: str) -> str:
    if context != EXPECTED_CONTEXT:
        raise SafetyViolation(
            f"context {context!r} is not the exact expected context "
            f"{EXPECTED_CONTEXT!r}; the ambient context is never trusted"
        )
    return context


def validate_namespace(name: str) -> str:
    _check_dns1123(name, "namespace")
    if name != NAMESPACE:
        raise SafetyViolation(
            f"namespace {name!r} is not the TrafficFuse namespace {NAMESPACE!r}"
        )
    return name


def is_global_kubeconfig(path: Path | str) -> bool:
    """True for the global/default kubeconfig locations that must never be used."""
    candidate = Path(path).expanduser()
    home_config = Path.home() / ".kube" / "config"
    etc_admin = Path("/etc/kubernetes/admin.conf")
    return candidate.resolve() in (home_config.resolve(), etc_admin.resolve())


def canonical_kubeconfig_path() -> Path:
    """The one canonical TrafficFuse-owned kubeconfig path.

    Derived from the installed module's own location (``__file__`` — a trusted
    application location resolved by the interpreter), never from caller input.
    A directory *shape* such as ``.runtime/kubeconfig/trafficfuse-lab.yaml`` is
    not sufficient: any other repository or directory would produce exactly
    that shape (Phase 1A.2 review finding P1).
    """
    return (PROJECT_ROOT / DEDICATED_KUBECONFIG).resolve()


def validate_kubeconfig_path(path: Path | str) -> Path:
    """Validate and normalize the dedicated kubeconfig path.

    Refuses: the global kubeconfig, a wrong file name, any location outside a
    ``.runtime/kubeconfig`` directory, paths that escape that directory
    through symlinks or traversal (the resolved parent must still be
    ``.runtime/kubeconfig``), and — finally — any path that does not resolve to
    the **exact canonical repository-owned path**
    (``canonical_kubeconfig_path``). Returns the resolved absolute path.
    """
    candidate = Path(path)
    if is_global_kubeconfig(candidate):
        raise SafetyViolation(
            "refusing the global kubeconfig; TrafficFuse requires the dedicated "
            f"kubeconfig {DEDICATED_KUBECONFIG!r}"
        )
    if candidate.name != "trafficfuse-lab.yaml":
        raise SafetyViolation(
            f"kubeconfig must be named trafficfuse-lab.yaml, got {candidate.name!r}"
        )
    if "kubeconfig" not in candidate.parts or ".runtime" not in candidate.parts:
        raise SafetyViolation(
            "kubeconfig must live under the dedicated .runtime/kubeconfig directory"
        )
    resolved = candidate.expanduser().resolve()
    if resolved.name != "trafficfuse-lab.yaml":
        raise SafetyViolation(
            f"resolved kubeconfig name {resolved.name!r} is not trafficfuse-lab.yaml "
            "(possible symlink or traversal attack)"
        )
    parent = resolved.parent
    if parent.name != "kubeconfig" or parent.parent.name != ".runtime":
        raise SafetyViolation(
            "resolved kubeconfig escaped the dedicated .runtime/kubeconfig directory "
            f"(resolved to {resolved}); refusing symlink or traversal substitution"
        )
    canonical = canonical_kubeconfig_path()
    if resolved != canonical:
        raise SafetyViolation(
            f"kubeconfig {resolved} is not the TrafficFuse repository-owned path "
            f"{canonical}; refusing any path outside the verified repository root"
        )
    return resolved


def is_dedicated_kubeconfig(path: Path | str) -> bool:
    """True only for the repository's exact dedicated kubeconfig path."""
    try:
        resolved = validate_kubeconfig_path(path)
    except SafetyViolation:
        return False
    return resolved == canonical_kubeconfig_path()


def validate_ownership_labels(labels: Mapping[str, str]) -> None:
    """Refuse resources without verifiable TrafficFuse ownership metadata."""
    if labels.get(OWNERSHIP_LABEL_KEY) != OWNERSHIP_LABEL_VALUE:
        raise SafetyViolation(
            f"missing TrafficFuse ownership label {OWNERSHIP_LABEL_KEY}="
            f"{OWNERSHIP_LABEL_VALUE!r}; cleanup is limited to resources created "
            "by TrafficFuse"
        )
    if labels.get(MANAGED_BY_LABEL_KEY) != CLUSTER_NAME:
        raise SafetyViolation(
            f"missing {MANAGED_BY_LABEL_KEY}={CLUSTER_NAME!r} ownership marker"
        )


def validate_authorization(action: str, *, authorized: bool) -> None:
    if not authorized:
        raise SafetyViolation(
            f"{action} requires explicit, task-specific user authorization "
            "(Phase 1B approval); refusing to proceed"
        )


# --- kubeconfig document identity --------------------------------------------------


def ca_fingerprint_sha256(certificate_authority_data: str) -> str:
    """Stable identity anchor: SHA-256 of the decoded CA certificate bytes."""
    try:
        decoded = base64.b64decode(certificate_authority_data, validate=True)
    except Exception as exc:  # binascii.Error and friends
        raise SafetyViolation(
            f"cluster CA data is not valid base64: {exc}"
        ) from exc
    return hashlib.sha256(decoded).hexdigest()


def load_kubeconfig_document(path: Path | str) -> dict[str, Any]:
    """Load the dedicated kubeconfig YAML document (offline; no cluster contact)."""
    kubeconfig_path = validate_kubeconfig_path(path)
    if not kubeconfig_path.is_file():
        raise SafetyViolation(
            f"dedicated kubeconfig {kubeconfig_path} does not exist; refusing to "
            "plan against a cluster without verifiable identity"
        )
    try:
        import yaml  # local import: the module stays dependency-light

        document = yaml.safe_load(kubeconfig_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SafetyViolation(f"unreadable kubeconfig {kubeconfig_path}: {exc}") from exc
    if not isinstance(document, dict) or "clusters" not in document or "contexts" not in document:
        raise SafetyViolation(
            f"{kubeconfig_path} is not a valid kubeconfig document "
            "(missing clusters/contexts)"
        )
    return document


def _entry(collection: Any, name: str, kind: str) -> dict[str, Any]:
    if not isinstance(collection, list):
        raise SafetyViolation(f"kubeconfig {kind} must be a list")
    for item in collection:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise SafetyViolation(f"kubeconfig has no {kind} named {name!r}")


def _api_server_identity(server: str) -> tuple[str, int]:
    parts = urlsplit(server)
    if parts.scheme != "https" or not parts.hostname:
        raise SafetyViolation(
            f"API server {server!r} must be an https URL; refusing non-TLS or "
            "remote cluster references"
        )
    if parts.hostname not in ALLOWED_API_HOSTS:
        raise SafetyViolation(
            f"API server host {parts.hostname!r} is not a local kind host "
            f"({', '.join(ALLOWED_API_HOSTS)}); refusing to reference remote clusters"
        )
    try:
        port = parts.port
    except ValueError as exc:
        raise SafetyViolation(f"API server {server!r} has an invalid port: {exc}") from exc
    if port is None or not 1 <= port <= 65535:
        raise SafetyViolation(f"API server {server!r} must specify a valid port")
    return parts.hostname, port


@dataclass(frozen=True)
class KubeconfigIdentity:
    """Verified identity facts extracted from the dedicated kubeconfig."""

    context: str
    cluster: str
    api_server: str
    ca_fingerprint_sha256: str


def verify_kubeconfig_document(
    document: Mapping[str, Any],
    *,
    expected_context: str = EXPECTED_CONTEXT,
    expected_cluster: str = CLUSTER_NAME,
) -> KubeconfigIdentity:
    """Verify the kubeconfig document's identity without contacting a cluster.

    Requires the exact context and cluster entries, a local HTTPS API server,
    a CA certificate (fingerprint anchor), no foreign contexts, and no embedded
    bearer-token or password credentials.
    """
    contexts = document.get("contexts") or []
    for item in contexts:
        if isinstance(item, dict) and item.get("name") in KNOWN_FOREIGN_CONTEXTS:
            raise SafetyViolation(
                f"kubeconfig contains foreign context {item.get('name')!r}; the "
                "dedicated kubeconfig must reference only TrafficFuse identities"
            )
    context_entry = _entry(contexts, expected_context, "context")
    context_body = context_entry.get("context") or {}
    # Kind writes the kubeconfig entry as "kind-<name>". Retain support for
    # the earlier logical-name fixtures, with both aliases pinned explicitly.
    cluster_entry_name = context_body.get("cluster")
    if cluster_entry_name not in (expected_cluster, f"kind-{expected_cluster}"):
        raise SafetyViolation(
            f"context {expected_context!r} points at cluster "
            f"{context_body.get('cluster')!r}, expected {expected_cluster!r}"
        )
    current = document.get("current-context")
    if current is not None and current != expected_context:
        raise SafetyViolation(
            f"kubeconfig current-context is {current!r}, expected {expected_context!r}; "
            "the dedicated file must not select another context"
        )

    cluster_entry = _entry(document.get("clusters") or [], cluster_entry_name, "cluster")
    cluster_body = cluster_entry.get("cluster") or {}
    server = cluster_body.get("server")
    if not isinstance(server, str):
        raise SafetyViolation("kubeconfig cluster entry has no API server URL")
    _api_server_identity(server)
    ca_data = cluster_body.get("certificate-authority-data")
    if not ca_data:
        raise SafetyViolation(
            "kubeconfig cluster entry has no certificate-authority-data; "
            "refusing to trust an unverifiable cluster identity"
        )
    fingerprint = ca_fingerprint_sha256(ca_data)

    users = document.get("users") or []
    for item in users:
        if not isinstance(item, dict):
            continue
        body = item.get("user") or {}
        if body.get("token") or body.get("password") or body.get("username"):
            raise SafetyViolation(
                f"kubeconfig user {item.get('name')!r} embeds static credentials; "
                "refusing to trust credential-bearing kubeconfig content"
            )

    return KubeconfigIdentity(
        context=expected_context,
        cluster=expected_cluster,
        api_server=server,
        ca_fingerprint_sha256=fingerprint,
    )


# --- creation record and ownership evidence ------------------------------------------


def load_ownership_receipt(path: Path | str) -> dict[str, Any]:
    """Load and verify the TrafficFuse creation record.

    The record is **metadata written by TrafficFuse tooling at cluster creation
    time — not cryptographic proof of ownership**. It is trusted only together
    with live ownership evidence (see ``verify_ownership_evidence``): the API
    server, CA fingerprint and kind node identities in the record must match
    the cluster that is actually about to be operated on.
    """
    receipt_path = Path(path)
    if not receipt_path.is_file():
        raise SafetyViolation(
            f"no ownership receipt at {receipt_path}; refusing destructive "
            "operations without verifiable TrafficFuse ownership"
        )
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyViolation(f"unreadable ownership receipt {receipt_path}: {exc}") from exc
    return validate_ownership_record(data)


def validate_ownership_record(data: Any) -> dict[str, Any]:
    """Validate creation metadata; this does not establish ownership or consent."""
    if not isinstance(data, dict):
        raise SafetyViolation("ownership receipt is not a JSON object")
    if data.get("cluster") != CLUSTER_NAME or data.get("context") != EXPECTED_CONTEXT:
        raise SafetyViolation(
            "ownership receipt does not match the TrafficFuse cluster/context identity"
        )
    if data.get("namespace") != NAMESPACE:
        raise SafetyViolation("ownership receipt namespace mismatch")
    if data.get("receipt_schema") != RECORD_SCHEMA:
        raise SafetyViolation(
            f"ownership receipt schema must be {RECORD_SCHEMA!r}, got "
            f"{data.get('receipt_schema')!r}"
        )
    missing = [name for name in RECORD_FIELDS if name not in data]
    if missing:
        raise SafetyViolation(
            f"ownership receipt is missing required fields: {', '.join(missing)}"
        )
    if not isinstance(data["api_server"], str):
        raise SafetyViolation("ownership receipt api_server must be a string")
    _api_server_identity(data["api_server"])
    if not isinstance(data["ca_fingerprint_sha256"], str) or not _SHA256_HEX.fullmatch(
        data["ca_fingerprint_sha256"]
    ):
        raise SafetyViolation("ownership receipt ca_fingerprint_sha256 is not a SHA-256 hex digest")
    nodes = data["kind_node_names"]
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, str) for n in nodes):
        raise SafetyViolation("ownership receipt kind_node_names must be a non-empty list of names")
    if len(set(nodes)) != len(nodes):
        raise SafetyViolation("ownership receipt kind_node_names must be unique")
    for node in nodes:
        _check_dns1123(node, "ownership receipt node name")
        if not node.startswith(f"{CLUSTER_NAME}-"):
            raise SafetyViolation("ownership receipt kind_node_names contains a foreign node")
    if not set(EXPECTED_NODE_NAMES).issubset(nodes):
        raise SafetyViolation("ownership receipt kind_node_names lacks the expected control-plane")
    timestamp = data["created_at"]
    try:
        if not isinstance(timestamp, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", timestamp
        ):
            raise ValueError
        created_at = datetime.fromisoformat(timestamp)
        if created_at.utcoffset() != timedelta(0):
            raise ValueError
    except ValueError as exc:
        raise SafetyViolation(
            "ownership receipt created_at must be a timezone-aware UTC ISO-8601 timestamp"
        ) from exc
    if data.get("created_by") != "trafficfuse":
        raise SafetyViolation("ownership receipt created_by must be 'trafficfuse'")
    if data.get("kubeconfig") != DEDICATED_KUBECONFIG:
        raise SafetyViolation(
            f"ownership receipt kubeconfig must reference {DEDICATED_KUBECONFIG!r}"
        )
    return data


@dataclass(frozen=True)
class ClusterFacts:
    """Live cluster facts captured by read-only operator commands (Phase 1B).

    This module never queries a cluster itself: trusted facts are produced by
    ``trafficfuse.lab.cluster_facts`` (the read-only collector, executed by an
    operator with explicit, context-pinned commands). Collector markers are
    forgeable Python objects, not proof of origin. Caller facts are never
    accepted by the destruction path, which performs its own collection and
    still refuses deletion. There is no provenance *string* on this type: a
    caller-controlled string is not a guarantee (Phase 1B entry review P2-a).
    Tests supply synthetic fixtures.
    """

    api_server: str
    ca_fingerprint_sha256: str
    node_names: tuple[str, ...]
    namespace_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OwnershipVerification:
    """Per-check ownership verification outcome."""

    checks: tuple[tuple[str, bool, str], ...]
    trusted: bool = False

    @property
    def verified(self) -> bool:
        return all(passed for _, passed, _ in self.checks)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            f"{name}: {detail}" for name, passed, detail in self.checks if not passed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "trusted": self.trusted,
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in self.checks
            ],
        }


def verify_ownership_evidence(
    record: Mapping[str, Any], facts: ClusterFacts
) -> OwnershipVerification:
    """Cross-check live cluster facts against the TrafficFuse creation record.

    Every trusted identity property must match exactly: API server, CA
    fingerprint, kind node identities and cluster ownership labels. Labels are
    required but never sufficient on their own. The legacy ``trusted`` field
    reports only presence of a forgeable collector marker. It is diagnostic,
    not authentication or authorization. Caller facts can be checked here;
    the security boundary is ``verify_live_ownership`` collecting independently
    inside trusted host control flow. Deletion remains disabled.
    """
    # Deferred import: cluster_facts imports this module; the trust helper
    # lives with the collector that defines the receipt type.
    from trafficfuse.lab.cluster_facts import is_collector_executed

    checks: list[tuple[str, bool, str]] = []

    record_server = str(record.get("api_server", ""))
    checks.append(
        (
            "api_server",
            facts.api_server == record_server,
            f"expected {record_server!r}, observed {facts.api_server!r}",
        )
    )
    record_ca = str(record.get("ca_fingerprint_sha256", ""))
    checks.append(
        (
            "ca_fingerprint",
            facts.ca_fingerprint_sha256 == record_ca,
            "CA fingerprint must match the creation record exactly",
        )
    )
    record_nodes = tuple(record.get("kind_node_names", ()))
    checks.append(
        (
            "kind_node_names",
            tuple(facts.node_names) == record_nodes,
            f"expected {record_nodes!r}, observed {tuple(facts.node_names)!r}",
        )
    )
    expected_nodes = set(EXPECTED_NODE_NAMES)
    checks.append(
        (
            "owned_node_identities",
            expected_nodes.issubset(set(facts.node_names)),
            f"cluster must contain TrafficFuse node identities {sorted(expected_nodes)}",
        )
    )
    labels = facts.namespace_labels or {}
    label_ok = (
        labels.get(OWNERSHIP_LABEL_KEY) == OWNERSHIP_LABEL_VALUE
        and labels.get(MANAGED_BY_LABEL_KEY) == CLUSTER_NAME
    )
    checks.append(
        (
            "ownership_labels",
            label_ok,
            f"namespace must carry {OWNERSHIP_LABEL_KEY}={OWNERSHIP_LABEL_VALUE!r} and "
            f"{MANAGED_BY_LABEL_KEY}={CLUSTER_NAME!r}",
        )
    )
    return OwnershipVerification(
        checks=tuple(checks), trusted=is_collector_executed(facts)
    )


def require_verified_ownership(
    record: Mapping[str, Any], facts: ClusterFacts
) -> OwnershipVerification:
    verification = verify_ownership_evidence(record, facts)
    if not verification.verified:
        raise SafetyViolation(
            "cluster ownership could not be verified: "
            + "; ".join(verification.failures)
        )
    return verification


def verify_cluster_ownership(
    *,
    kubeconfig_path: Path | str,
    record_path: Path | str,
    facts: ClusterFacts,
) -> tuple[KubeconfigIdentity, OwnershipVerification]:
    """Full offline ownership verification pipeline (Phase 1B companion).

    1. kubeconfig path, document and identity verification;
    2. creation record verification;
    3. live-facts cross-check against the record.

    No cluster is contacted; ``facts`` must come from the operator's read-only,
    explicitly context-pinned commands.
    """
    document = load_kubeconfig_document(kubeconfig_path)
    identity = verify_kubeconfig_document(document)
    record = load_ownership_receipt(record_path)
    if identity.api_server != str(record.get("api_server")):
        raise SafetyViolation(
            "kubeconfig API server does not match the creation record; "
            "refusing to operate on a mismatched cluster identity"
        )
    if identity.ca_fingerprint_sha256 != str(record.get("ca_fingerprint_sha256")):
        raise SafetyViolation(
            "kubeconfig CA fingerprint does not match the creation record; "
            "refusing to operate on a mismatched cluster identity"
        )
    verification = require_verified_ownership(record, facts)
    return identity, verification


def kubectl_prefix() -> list[str]:
    """Every future kubectl invocation must carry the dedicated kubeconfig and
    the exact context — never the ambient selection."""
    return ["--kubeconfig", DEDICATED_KUBECONFIG, "--context", EXPECTED_CONTEXT]


# --- planning --------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecyclePlan:
    action: str
    kubeconfig: Path
    cluster: str
    context: str
    namespace: str
    mutating: bool
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "kubeconfig": str(self.kubeconfig),
            "cluster": self.cluster,
            "context": self.context,
            "namespace": self.namespace,
            "mutating": self.mutating,
            "steps": list(self.steps),
        }


def build_preflight_plan(
    *,
    kubeconfig_path: Path | str,
    cluster: str = CLUSTER_NAME,
    context: str = EXPECTED_CONTEXT,
    namespace: str = NAMESPACE,
) -> LifecyclePlan:
    """Validate a future create request and return a non-mutating plan."""
    kubeconfig = validate_kubeconfig_path(kubeconfig_path)
    validate_cluster_name(cluster)
    validate_context(context)
    validate_namespace(namespace)
    steps = (
        f"kind create cluster --name {cluster} --kubeconfig {kubeconfig}",
        "record actual post-creation identity with lifecycle_cli record-creation "
        "(validated local metadata only; not ownership proof)",
        f"kind load docker-image trafficfuse-lab-service:phase1a --name {cluster}",
        f"kubectl --kubeconfig {kubeconfig} --context {context} apply "
        f"-f deploy/lab/namespace.yaml -n {namespace}",
        "verify namespace ownership metadata (read-only: labels "
        "trafficfuse.io/owner and app.kubernetes.io/managed-by)",
        "verify cluster ownership evidence with the trusted read-only "
        "collector (trafficfuse.lab.cluster_facts verify_live_ownership; the "
        "collector executes the scoped commands itself, caller-supplied "
        "facts are never accepted)",
        f"kubectl --kubeconfig {kubeconfig} --context {context} apply "
        f"-f deploy/lab/healthy-deployment.yaml -f deploy/lab/faulty-deployment.yaml "
        f"-f deploy/lab/service-live.yaml -f deploy/lab/service-versions.yaml -n {namespace}",
        "wait for rollout readiness and confirm trafficfuse-live selects "
        "version=faulty before any probe",
        "run the healthy baseline probe Job, then the faulty live-Service "
        "probe Job; wait for both, export and validate both evidence documents "
        "(raw logs preserved byte-for-byte)",
        "preserve the cluster for inspection: deletion is outside this plan "
        "and requires separate explicit human authorization",
    )
    return LifecyclePlan(
        action="preflight",
        kubeconfig=kubeconfig,
        cluster=cluster,
        context=context,
        namespace=namespace,
        mutating=False,
        steps=steps,
    )


def plan_cluster_create(*, kubeconfig_path: Path | str, authorized: bool) -> LifecyclePlan:
    """Guarded creation plan. Refuses without authorization; never mutates in
    Phase 1A even with authorization."""
    plan = build_preflight_plan(kubeconfig_path=kubeconfig_path)
    validate_authorization("cluster creation", authorized=authorized)
    raise SafetyViolation(
        "cluster creation is not enabled in Phase 1A (offline phase); it becomes "
        "available only after Phase 1B authorization (docs/PHASE1-PREFLIGHT.md)"
    )


def plan_cluster_destroy(
    *,
    kubeconfig_path: Path | str,
    receipt_path: Path | str,
    authorized: bool,
    executor: Callable[[Sequence[str]], Any] | None = None,
) -> LifecyclePlan:
    """Guarded destruction plan (trust by execution; no caller facts accepted).

    Trust is established by **execution**, not declaration (Phase 1B entry
    review finding P2-a): this operation performs the trusted read-only
    collection itself, inside one controlled operation
    (``cluster_facts.verify_live_ownership``), and cross-checks the collected
    facts against the creation record. There is deliberately **no parameter
    through which a caller can supply facts**: the removed
    ``cluster_facts``/``source`` parameters let any caller mint matching facts
    and set ``source="collector"``, which is not a provenance guarantee.

    Guards, in order: dedicated kubeconfig path, creation record, exact
    context, explicit task-specific authorization, trusted live collection +
    ownership verification (any missing, mismatched or uncollectable evidence
    fails closed). Deletion is additionally outside the Phase 1B
    authorization scope: this function never mutates, and the final refusal
    stays in place until a separate, explicitly authorized cleanup workflow.
    """
    kubeconfig = validate_kubeconfig_path(kubeconfig_path)
    record = load_ownership_receipt(receipt_path)
    validate_context(EXPECTED_CONTEXT)
    validate_authorization("cluster destruction", authorized=authorized)
    if executor is None:
        raise SafetyViolation(
            "cluster destruction requires trusted live collection through the "
            "read-only collector (trafficfuse.lab.cluster_facts): no collector "
            "execution was performed, so ownership cannot be established"
        )
    # Deferred import: cluster_facts imports this module.
    from trafficfuse.lab.cluster_facts import verify_live_ownership

    ownership = verify_live_ownership(
        kubeconfig_path=kubeconfig_path, record_path=receipt_path, executor=executor
    )
    if not ownership.verification.verified:
        raise SafetyViolation(
            "cluster ownership could not be verified: "
            + "; ".join(ownership.verification.failures)
        )
    if not ownership.verification.trusted:
        raise SafetyViolation(
            "collector execution receipt missing; facts were not collected "
            "through the trusted collector execution path"
        )
    raise SafetyViolation(
        "cluster deletion is outside the Phase 1B authorization scope and "
        "remains disabled: Phase 1B covers creation and scoped deployment "
        "only. Cleanup requires separate explicit human authorization"
    )
