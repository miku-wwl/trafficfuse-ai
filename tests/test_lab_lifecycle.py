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
from trafficfuse.lab.cluster_facts import CommandResult
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    EXPECTED_CONTEXT,
    NAMESPACE,
    RECORD_SCHEMA,
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
FIXTURE_CA_BYTES = b"synthetic-ca-fixture"
OWNED_LABELS = {
    "trafficfuse.io/owner": CLUSTER_NAME,
    "app.kubernetes.io/managed-by": CLUSTER_NAME,
}


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Point the trusted repository root at a synthetic repository layout."""
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _write_kubeconfig(repo_root: Path) -> Path:
    import base64
    import yaml

    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": EXPECTED_CONTEXT,
        "clusters": [
            {
                "name": CLUSTER_NAME,
                "cluster": {
                    "server": FIXTURE_API_SERVER,
                    "certificate-authority-data": base64.b64encode(FIXTURE_CA_BYTES).decode("ascii"),
                },
            }
        ],
        "contexts": [
            {
                "name": EXPECTED_CONTEXT,
                "context": {"cluster": CLUSTER_NAME, "user": "kind-trafficfuse-lab"},
            }
        ],
        "users": [
            {"name": "kind-trafficfuse-lab", "user": {"client-certificate-data": "c", "client-key-data": "k"}}
        ],
    }
    path = repo_root / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _make_executor(*, node_names=FIXTURE_NODES, labels=OWNED_LABELS, nodes_fail: bool = False):
    def executor(argv):
        argv = list(argv)
        if "get" in argv and "nodes" in argv:
            if nodes_fail:
                return CommandResult(tuple(argv), 1, b"", b"connection refused")
            document = {"items": [{"metadata": {"name": name}} for name in node_names]}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if "get" in argv and "namespace" in argv:
            document = {"metadata": {"name": NAMESPACE, "labels": dict(labels)}}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if argv[0] == "docker":
            return CommandResult(tuple(argv), 0, b"/trafficfuse-lab-control-plane", b"")
        raise AssertionError(f"unexpected collector command: {' '.join(argv)}")

    return executor


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


def test_destroy_requires_receipt_then_authorization_then_collection_then_refuses(
    repo_root: Path, tmp_path: Path
) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    missing = tmp_path / "cluster-trafficfuse-lab.json"
    with pytest.raises(SafetyViolation, match="ownership receipt"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig, receipt_path=missing, authorized=False
        )
    receipt = _write_receipt(
        tmp_path / "cluster-trafficfuse-lab.json", cluster=CLUSTER_NAME, context=EXPECTED_CONTEXT
    )
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig, receipt_path=receipt, authorized=False
        )
    # Authorized but without a collector execution: still refused — there is
    # no way to hand facts in.
    with pytest.raises(SafetyViolation, match="no collector execution was performed"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig, receipt_path=receipt, authorized=True
        )
    # Authorized with a valid trusted collection: the deletion-scope refusal
    # is reached, proving the trust chain itself is sound.
    with pytest.raises(SafetyViolation, match="outside the Phase 1B authorization scope"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=receipt,
            authorized=True,
            executor=_make_executor(),
        )


def test_destroy_refuses_when_ownership_evidence_mismatches(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    receipt = _write_receipt(
        tmp_path / "cluster-trafficfuse-lab.json", cluster=CLUSTER_NAME, context=EXPECTED_CONTEXT
    )
    # Collectible, executor-produced facts whose node identities disagree
    # with the creation record: the verification gate must refuse them.
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=receipt,
            authorized=True,
            executor=_make_executor(node_names=("incident-lab-control-plane",)),
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
    steps = payload["steps"]
    assert steps
    assert any("kind create cluster" in step for step in steps)
    assert any("--context kind-trafficfuse-lab" in step for step in steps)
    # Corrected ordering: image load after creation; namespace bootstrap as an
    # explicitly selected manifest (never the whole kustomization, which would
    # create Deployments before ownership verification); workloads applied
    # only after ownership verification; deletion out of scope.
    joined = "\n".join(steps)
    create_at = next(i for i, s in enumerate(steps) if "kind create cluster" in s)
    load_at = next(i for i, s in enumerate(steps) if "kind load docker-image" in s)
    namespace_at = next(i for i, s in enumerate(steps) if "deploy/lab/namespace.yaml" in s)
    verify_at = next(i for i, s in enumerate(steps) if "trusted read-only" in s)
    workloads_at = next(i for i, s in enumerate(steps) if "healthy-deployment.yaml" in s)
    assert create_at < load_at < namespace_at < verify_at < workloads_at
    assert "apply -k deploy/lab" not in joined
    assert "separate explicit human authorization" in joined


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
