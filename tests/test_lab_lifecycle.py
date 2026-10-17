"""Offline lifecycle safety-guard tests (no Kubernetes operations).

Each guard is exercised positively and negatively. No test creates, deletes or
modifies any cluster, context or resource; mutating plan functions refuse even
when authorized, because Phase 1A is an offline phase.
"""

import hashlib
import json
from pathlib import Path

import pytest

from trafficfuse.lab import lifecycle
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    EXPECTED_CONTEXT,
    NAMESPACE,
    RECORD_SCHEMA,
    ClusterFacts,
    SafetyViolation,
    build_preflight_plan,
    is_global_kubeconfig,
    load_ownership_receipt,
    plan_cluster_create,
    plan_cluster_destroy,
    validate_authorization,
    validate_cluster_name,
    validate_context,
    validate_kubeconfig_path,
    validate_namespace,
    validate_ownership_labels,
)

DEDICATED_KUBECONFIG = ".runtime/kubeconfig/trafficfuse-lab.yaml"

# Synthetic creation-record fixture values (never a real cluster identity).
FIXTURE_API_SERVER = "https://127.0.0.1:6443"
FIXTURE_CA_FINGERPRINT = hashlib.sha256(b"synthetic-ca-fixture").hexdigest()
FIXTURE_NODES = ("trafficfuse-lab-control-plane",)


def test_standard_identity_accepted() -> None:
    assert validate_cluster_name("trafficfuse-lab") == CLUSTER_NAME
    assert validate_context("kind-trafficfuse-lab") == EXPECTED_CONTEXT
    assert validate_namespace("trafficfuse-system") == NAMESPACE
    assert validate_kubeconfig_path(DEDICATED_KUBECONFIG).name == "trafficfuse-lab.yaml"


@pytest.mark.parametrize(
    "name",
    ["incident-lab", "pcp-dev", "playground-crd", "vmware-k8s", "other-lab", "", "TrafficFuse-Lab", "-bad"],
)
def test_foreign_or_invalid_cluster_refused(name: str) -> None:
    with pytest.raises(SafetyViolation):
        validate_cluster_name(name)


@pytest.mark.parametrize(
    "context",
    ["kind-playground-crd", "kind-incident-lab", "kind-pcp-dev", "vmware-k8s", "", "trafficfuse-lab"],
)
def test_wrong_or_ambiguous_context_refused(context: str) -> None:
    with pytest.raises(SafetyViolation):
        validate_context(context)


@pytest.mark.parametrize("namespace", ["default", "kube-system", "trafficfuse-local", "Bad_NS", ""])
def test_unexpected_namespace_refused(namespace: str) -> None:
    with pytest.raises(SafetyViolation):
        validate_namespace(namespace)


def test_global_kubeconfig_detected_and_refused() -> None:
    home_config = Path.home() / ".kube" / "config"
    assert is_global_kubeconfig(home_config) is True
    with pytest.raises(SafetyViolation, match="global kubeconfig"):
        validate_kubeconfig_path(home_config)
    with pytest.raises(SafetyViolation, match="global kubeconfig"):
        validate_kubeconfig_path("/etc/kubernetes/admin.conf")


def test_wrong_kubeconfig_name_refused(tmp_path: Path) -> None:
    with pytest.raises(SafetyViolation, match="must be named"):
        validate_kubeconfig_path(tmp_path / ".runtime" / "kubeconfig" / "other-lab.yaml")


def test_kubeconfig_outside_dedicated_directory_refused(tmp_path: Path) -> None:
    with pytest.raises(SafetyViolation, match="dedicated .runtime/kubeconfig"):
        validate_kubeconfig_path(tmp_path / ".runtime" / "trafficfuse-lab.yaml")
    with pytest.raises(SafetyViolation, match="dedicated .runtime/kubeconfig"):
        validate_kubeconfig_path(tmp_path / "kubeconfigs" / "trafficfuse-lab.yaml")


def test_ownership_labels_required() -> None:
    valid = {
        "trafficfuse.io/owner": "trafficfuse-lab",
        "app.kubernetes.io/managed-by": "trafficfuse-lab",
    }
    validate_ownership_labels(valid)
    with pytest.raises(SafetyViolation, match="ownership label"):
        validate_ownership_labels({})
    with pytest.raises(SafetyViolation, match="ownership label"):
        validate_ownership_labels(
            {"trafficfuse.io/owner": "incident-lab", "app.kubernetes.io/managed-by": "trafficfuse-lab"}
        )


def test_authorization_guard() -> None:
    with pytest.raises(SafetyViolation, match="authorization"):
        validate_authorization("creation", authorized=False)
    # Passing the guard grants nothing by itself; mutations still never happen
    # implicitly (all mutating entry points are explicit functions).
    validate_authorization("creation", authorized=True)


def test_create_requires_authorization_then_refuses_in_phase_1a() -> None:
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_create(kubeconfig_path=DEDICATED_KUBECONFIG, authorized=False)
    with pytest.raises(SafetyViolation, match="not enabled in Phase 1A"):
        plan_cluster_create(kubeconfig_path=DEDICATED_KUBECONFIG, authorized=True)


def _write_receipt(path: Path, *, cluster: str, context: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "receipt_schema": RECORD_SCHEMA,
                "cluster": cluster,
                "context": context,
                "namespace": NAMESPACE,
                "api_server": FIXTURE_API_SERVER,
                "ca_fingerprint_sha256": FIXTURE_CA_FINGERPRINT,
                "kind_node_names": list(FIXTURE_NODES),
                "created_at": "2026-10-18T10:00:00+00:00",
                "created_by": "trafficfuse",
                "kubeconfig": DEDICATED_KUBECONFIG,
            }
        ),
        encoding="utf-8",
    )
    return path


def _verified_facts() -> ClusterFacts:
    return ClusterFacts(
        api_server=FIXTURE_API_SERVER,
        ca_fingerprint_sha256=FIXTURE_CA_FINGERPRINT,
        node_names=FIXTURE_NODES,
        namespace_labels={
            "trafficfuse.io/owner": CLUSTER_NAME,
            "app.kubernetes.io/managed-by": CLUSTER_NAME,
        },
    )


def test_destroy_requires_receipt_then_authorization_then_refuses(tmp_path: Path) -> None:
    missing = tmp_path / "cluster-trafficfuse-lab.json"
    with pytest.raises(SafetyViolation, match="ownership receipt"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG, receipt_path=missing, authorized=False
        )
    receipt = _write_receipt(tmp_path / "cluster-trafficfuse-lab.json", cluster=CLUSTER_NAME, context=EXPECTED_CONTEXT)
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG, receipt_path=receipt, authorized=False
        )
    # Authorized but without verified ownership evidence: still refused.
    with pytest.raises(SafetyViolation, match="verified ownership evidence"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG, receipt_path=receipt, authorized=True
        )
    # Authorized with verified evidence: Phase 1A still refuses to mutate.
    with pytest.raises(SafetyViolation, match="not enabled in Phase 1A"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=receipt,
            authorized=True,
            cluster_facts=_verified_facts(),
        )


def test_destroy_refuses_when_ownership_evidence_mismatches(tmp_path: Path) -> None:
    receipt = _write_receipt(tmp_path / "cluster-trafficfuse-lab.json", cluster=CLUSTER_NAME, context=EXPECTED_CONTEXT)
    tampered = ClusterFacts(
        api_server="https://127.0.0.1:7777",  # different API server
        ca_fingerprint_sha256=FIXTURE_CA_FINGERPRINT,
        node_names=FIXTURE_NODES,
        namespace_labels={},
    )
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=receipt,
            authorized=True,
            cluster_facts=tampered,
        )


def test_tampered_receipt_refused(tmp_path: Path) -> None:
    receipt = _write_receipt(
        tmp_path / "cluster-trafficfuse-lab.json", cluster="incident-lab", context="kind-incident-lab"
    )
    with pytest.raises(SafetyViolation, match="receipt"):
        load_ownership_receipt(receipt)


def test_preflight_plan_is_non_mutating_and_json_serializable() -> None:
    plan = build_preflight_plan(kubeconfig_path=DEDICATED_KUBECONFIG)
    assert plan.mutating is False
    assert plan.cluster == CLUSTER_NAME
    assert plan.context == EXPECTED_CONTEXT
    assert plan.namespace == NAMESPACE
    payload = json.loads(json.dumps(plan.to_dict()))
    assert payload["steps"]
    assert any("kind create cluster" in step for step in payload["steps"])
    assert any("--context kind-trafficfuse-lab" in step for step in payload["steps"])
    assert any("apply -k deploy/lab" in step for step in payload["steps"])


def test_preflight_plan_refuses_wrong_identity() -> None:
    with pytest.raises(SafetyViolation):
        build_preflight_plan(kubeconfig_path=DEDICATED_KUBECONFIG, cluster="playground-crd")


def test_no_ambient_context_dependency_in_module_source() -> None:
    # Structural guarantee: the lifecycle module never reads the ambient
    # environment, never introspects or switches the ambient Kubernetes
    # context, and never shells out to kubectl. Every future invocation must
    # carry the dedicated kubeconfig and exact context explicitly
    # (kubectl_prefix()). The dedicated kubeconfig *file*'s own current-context
    # field is inspected offline, which is why the checks target ambient
    # context operations rather than the substring alone.
    source = Path(lifecycle.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "get_contexts" not in source
    assert "kubectl config" not in source
    assert "kubectl current" not in source
    assert "use-context" not in source
    assert "subprocess" not in source
