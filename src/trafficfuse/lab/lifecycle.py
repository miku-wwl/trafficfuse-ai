"""Safety-guarded lifecycle planning for the trafficfuse-lab cluster.

Phase 1A implements validation and planning only. No function in this module
creates, deletes or mutates any cluster, context or Kubernetes resource —
mutating paths refuse even with authorization, until Phase 1B is approved
(docs/PHASE1-PREFLIGHT.md).

Isolation guarantees enforced offline:

* a dedicated, git-ignored kubeconfig is required (never the global one);
* the exact expected context (``kind-trafficfuse-lab``) is required — the
  ambient current context is never read or trusted;
* the cluster must be exactly ``trafficfuse-lab`` — foreign clusters
  (``incident-lab``, ``pcp-dev``, ``playground-crd``, ``vmware-k8s``, ...)
  are refused by name;
* destructive operations additionally require a verifiable TrafficFuse
  ownership receipt written by TrafficFuse's own tooling;
* a matching cluster *name* alone is never treated as deletion authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CLUSTER_NAME = "trafficfuse-lab"
EXPECTED_CONTEXT = "kind-trafficfuse-lab"
NAMESPACE = "trafficfuse-system"
OWNERSHIP_LABEL_KEY = "trafficfuse.io/owner"
OWNERSHIP_LABEL_VALUE = CLUSTER_NAME
MANAGED_BY_LABEL_KEY = "app.kubernetes.io/managed-by"

RECEIPT_DIRECTORY = ".runtime/state"
RECEIPT_FILENAME = "cluster-trafficfuse-lab.json"
DEDICATED_KUBECONFIG = ".runtime/kubeconfig/trafficfuse-lab.yaml"

# Foreign clusters observed on the development machine: never valid targets.
KNOWN_FOREIGN_CLUSTERS = ("incident-lab", "pcp-dev", "playground-crd", "vmware-k8s")

_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


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
            f"{EXPECTED_CONTEXT!r}; the ambient current context is never trusted"
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


def validate_kubeconfig_path(path: Path | str) -> Path:
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
    return candidate


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


def load_ownership_receipt(path: Path | str) -> dict[str, Any]:
    """Load and verify the receipt proving TrafficFuse created a cluster."""
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
    if data.get("cluster") != CLUSTER_NAME or data.get("context") != EXPECTED_CONTEXT:
        raise SafetyViolation(
            "ownership receipt does not match the TrafficFuse cluster/context identity"
        )
    if data.get("namespace") != NAMESPACE:
        raise SafetyViolation("ownership receipt namespace mismatch")
    return data


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
        f"kubectl --kubeconfig {kubeconfig} --context {context} create namespace {namespace}",
        f"kubectl --kubeconfig {kubeconfig} --context {context} apply -k deploy/lab",
        "write ownership receipt to .runtime/state/cluster-trafficfuse-lab.json",
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
) -> LifecyclePlan:
    """Guarded destruction plan. Requires authorization AND a verifiable
    ownership receipt; never mutates in Phase 1A."""
    kubeconfig = validate_kubeconfig_path(kubeconfig_path)
    load_ownership_receipt(receipt_path)
    validate_context(EXPECTED_CONTEXT)
    validate_authorization("cluster destruction", authorized=authorized)
    raise SafetyViolation(
        "cluster destruction is not enabled in Phase 1A (offline phase); it "
        "becomes available only after Phase 1B authorization"
    )
