"""Mandatory ownership negative tests (Phase 1A.1, review finding P1).

Every refusal mandated by the hardening brief is exercised with synthetic
fixtures and mocks only: no Kubernetes cluster is contacted, created, modified
or deleted by any test in this file.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from trafficfuse.lab import lifecycle
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    DEDICATED_KUBECONFIG,
    EXPECTED_CONTEXT,
    NAMESPACE,
    RECORD_SCHEMA,
    ClusterFacts,
    SafetyViolation,
    ca_fingerprint_sha256,
    canonical_kubeconfig_path,
    is_dedicated_kubeconfig,
    kubectl_prefix,
    load_kubeconfig_document,
    load_ownership_receipt,
    plan_cluster_destroy,
    require_verified_ownership,
    validate_cluster_name,
    validate_kubeconfig_path,
    verify_cluster_ownership,
    verify_kubeconfig_document,
    verify_ownership_evidence,
)

FIXTURE_API_SERVER = "https://127.0.0.1:6443"
FIXTURE_CA_FINGERPRINT = hashlib.sha256(b"synthetic-ca-fixture").hexdigest()
FIXTURE_NODES = ("trafficfuse-lab-control-plane",)
OWNED_LABELS = {
    "trafficfuse.io/owner": CLUSTER_NAME,
    "app.kubernetes.io/managed-by": CLUSTER_NAME,
}


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Point the trusted repository root at a synthetic repository layout.

    Production always derives the root from the installed module location;
    tests must never write into the real repository's .runtime directory.
    """
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _ca_data() -> str:
    return base64.b64encode(b"synthetic-ca-certificate").decode("ascii")


def _kubeconfig_document(
    *,
    context: str = EXPECTED_CONTEXT,
    cluster: str = CLUSTER_NAME,
    server: str = FIXTURE_API_SERVER,
    ca_data: str | None = None,
    current_context: str | None = EXPECTED_CONTEXT,
    with_ca: bool = True,
    token_user: bool = False,
) -> dict:
    cluster_body: dict = {"server": server}
    if with_ca:
        cluster_body["certificate-authority-data"] = ca_data or _ca_data()
    user_body: dict = {"client-certificate-data": "cert", "client-key-data": "key"}
    if token_user:
        user_body = {"token": "static-bearer-token"}
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": current_context,
        "clusters": [{"name": cluster, "cluster": cluster_body}],
        "contexts": [{"name": context, "context": {"cluster": cluster, "user": "kind-trafficfuse-lab"}}],
        "users": [{"name": "kind-trafficfuse-lab", "user": user_body}],
    }


def _write_kubeconfig(path: Path, document: dict) -> Path:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _write_record(path: Path, **overrides) -> Path:
    record = {
        "receipt_schema": RECORD_SCHEMA,
        "cluster": CLUSTER_NAME,
        "context": EXPECTED_CONTEXT,
        "namespace": NAMESPACE,
        "api_server": FIXTURE_API_SERVER,
        "ca_fingerprint_sha256": FIXTURE_CA_FINGERPRINT,
        "kind_node_names": list(FIXTURE_NODES),
        "created_at": "2026-10-18T10:00:00+00:00",
        "created_by": "trafficfuse",
        "kubeconfig": DEDICATED_KUBECONFIG,
    }
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _facts(**overrides) -> ClusterFacts:
    values = {
        "api_server": FIXTURE_API_SERVER,
        "ca_fingerprint_sha256": FIXTURE_CA_FINGERPRINT,
        "node_names": FIXTURE_NODES,
        "namespace_labels": dict(OWNED_LABELS),
    }
    values.update(overrides)
    return ClusterFacts(**values)


# --- kubeconfig path refusals -------------------------------------------------------


def test_missing_dedicated_kubeconfig_refused() -> None:
    # The canonical repository-owned path is the only accepted shape. No
    # cluster exists yet, so the file is absent: loading must refuse.
    canonical = canonical_kubeconfig_path()
    assert validate_kubeconfig_path(DEDICATED_KUBECONFIG) == canonical
    with pytest.raises(SafetyViolation, match="does not exist"):
        load_kubeconfig_document(DEDICATED_KUBECONFIG)


def test_unexpected_kubeconfig_path_refused(tmp_path: Path) -> None:
    with pytest.raises(SafetyViolation, match="dedicated .runtime/kubeconfig"):
        validate_kubeconfig_path(tmp_path / "somewhere" / "trafficfuse-lab.yaml")
    with pytest.raises(SafetyViolation, match="must be named"):
        validate_kubeconfig_path(tmp_path / ".runtime" / "kubeconfig" / "incident-lab.yaml")


def test_symlink_escape_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "trafficfuse-lab.yaml"
    outside.parent.mkdir(parents=True)
    outside.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    link = tmp_path / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError:  # pragma: no cover - platform without symlink privilege
        pytest.skip("symlink creation is not permitted on this platform")
    if not link.is_symlink():  # pragma: no cover - restricted sandbox
        pytest.skip("platform did not materialize a real symlink; traversal covered separately")
    with pytest.raises(SafetyViolation, match="escaped the dedicated"):
        validate_kubeconfig_path(link)


def test_path_traversal_escape_refused(tmp_path: Path) -> None:
    # No symlink required: a traversal that lands outside the dedicated
    # directory must be refused after resolution.
    escaped = tmp_path / ".runtime" / "kubeconfig" / ".." / ".." / "outside" / "trafficfuse-lab.yaml"
    with pytest.raises(SafetyViolation, match="escaped the dedicated"):
        validate_kubeconfig_path(escaped)


def test_foreign_repository_with_identical_layout_refused(tmp_path: Path) -> None:
    # Phase 1A.2 review reproduction: a path such as
    # /tmp/not_this_project/.runtime/kubeconfig/trafficfuse-lab.yaml matches
    # the expected directory *shape* but belongs to no TrafficFuse
    # repository. Shape is not ownership; only the exact canonical path is.
    foreign = tmp_path / "not_this_project" / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")
    with pytest.raises(SafetyViolation, match="repository-owned"):
        validate_kubeconfig_path(foreign)
    assert is_dedicated_kubeconfig(foreign) is False


def test_mocked_symlink_escape_via_path_resolution(tmp_path: Path, monkeypatch) -> None:
    # Real symlink creation requires elevated privileges on this Windows
    # workstation (the native test above is skipped here), so this test MOCKS
    # the path-resolution step only: the candidate has the canonical shape,
    # but resolution lands outside the .runtime/kubeconfig directory. It
    # exercises the same rejection logic; native symlink behavior is NOT
    # claimed.
    candidate = tmp_path / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    escaped_to = tmp_path / "outside" / "trafficfuse-lab.yaml"
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self == candidate:
            return escaped_to
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(SafetyViolation, match="escaped the dedicated"):
        validate_kubeconfig_path(candidate)


def test_mocked_symlink_escape_to_foreign_repository(tmp_path: Path, monkeypatch) -> None:
    # Mocked resolution landing in a foreign repository with the identical
    # directory shape: refused by the exact canonical-path equality check.
    foreign = tmp_path / "not_this_project" / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    foreign.parent.mkdir(parents=True)
    candidate = tmp_path / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self == candidate:
            return foreign
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(SafetyViolation, match="repository-owned"):
        validate_kubeconfig_path(candidate)


def test_cli_and_preflight_entry_points_enforce_canonical_path(tmp_path: Path, capsys) -> None:
    # CLI entry points and the preflight planning entry point must apply the
    # exact same validation (single implementation, no duplicates).
    from trafficfuse.lab.lifecycle import build_preflight_plan
    from trafficfuse.lab.lifecycle_cli import main as lifecycle_cli_main

    foreign = tmp_path / "not_this_project" / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    with pytest.raises(SafetyViolation, match="repository-owned"):
        build_preflight_plan(kubeconfig_path=foreign)
    assert (
        lifecycle_cli_main(["preflight", "--kubeconfig", str(foreign)]) == 2
    )
    assert "REFUSED" in capsys.readouterr().err


def test_global_kubeconfig_fallback_refused() -> None:
    for path in (Path.home() / ".kube" / "config", Path("/etc/kubernetes/admin.conf")):
        with pytest.raises(SafetyViolation, match="global kubeconfig"):
            validate_kubeconfig_path(path)


def test_is_dedicated_kubeconfig_is_exact() -> None:
    assert is_dedicated_kubeconfig(DEDICATED_KUBECONFIG) is True
    assert is_dedicated_kubeconfig("/tmp/.runtime/kubeconfig/trafficfuse-lab.yaml") is False
    assert is_dedicated_kubeconfig(Path.home() / ".kube" / "config") is False


# --- kubeconfig document refusals ------------------------------------------------------


def test_wrong_context_in_document_refused() -> None:
    # A known foreign context in the dedicated file is refused immediately.
    document = _kubeconfig_document(context="kind-playground-crd")
    with pytest.raises(SafetyViolation, match="foreign context"):
        verify_kubeconfig_document(document)
    # The exact context present but pointing at another cluster: refused.
    document = _kubeconfig_document()
    document["contexts"][0]["context"]["cluster"] = "playground-crd"
    with pytest.raises(SafetyViolation, match="points at cluster"):
        verify_kubeconfig_document(document)


def test_wrong_api_server_refused() -> None:
    with pytest.raises(SafetyViolation, match="not a local kind host"):
        verify_kubeconfig_document(_kubeconfig_document(server="https://k8s.example.com:6443"))
    with pytest.raises(SafetyViolation, match="must be an https URL"):
        verify_kubeconfig_document(_kubeconfig_document(server="http://127.0.0.1:6443"))


def test_wrong_current_context_in_dedicated_file_refused() -> None:
    document = _kubeconfig_document(current_context="kind-playground-crd")
    with pytest.raises(SafetyViolation, match="current-context"):
        verify_kubeconfig_document(document)


def test_foreign_context_in_dedicated_file_refused() -> None:
    document = _kubeconfig_document()
    document["contexts"].append(
        {"name": "kind-playground-crd", "context": {"cluster": "playground-crd", "user": "x"}}
    )
    with pytest.raises(SafetyViolation, match="foreign context"):
        verify_kubeconfig_document(document)


def test_missing_ca_data_refused() -> None:
    with pytest.raises(SafetyViolation, match="certificate-authority-data"):
        verify_kubeconfig_document(_kubeconfig_document(with_ca=False))


def test_embedded_credentials_in_kubeconfig_refused() -> None:
    with pytest.raises(SafetyViolation, match="static credentials"):
        verify_kubeconfig_document(_kubeconfig_document(token_user=True))


def test_ca_fingerprint_helper() -> None:
    fingerprint = ca_fingerprint_sha256(_ca_data())
    assert fingerprint == hashlib.sha256(b"synthetic-ca-certificate").hexdigest()
    with pytest.raises(SafetyViolation, match="base64"):
        ca_fingerprint_sha256("!!!not-base64!!!")


def test_valid_kubeconfig_document_verifies(repo_root: Path) -> None:
    path = _write_kubeconfig(
        repo_root / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml", _kubeconfig_document()
    )
    identity = verify_kubeconfig_document(load_kubeconfig_document(path))
    assert identity.context == EXPECTED_CONTEXT
    assert identity.cluster == CLUSTER_NAME
    assert identity.api_server == FIXTURE_API_SERVER
    assert identity.ca_fingerprint_sha256 == hashlib.sha256(b"synthetic-ca-certificate").hexdigest()


# --- creation record refusals ------------------------------------------------------------


def test_missing_creation_record_refused(tmp_path: Path) -> None:
    with pytest.raises(SafetyViolation, match="ownership receipt"):
        load_ownership_receipt(tmp_path / "cluster-trafficfuse-lab.json")


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"cluster": "incident-lab"}, "receipt"),
        ({"context": "kind-incident-lab"}, "receipt"),
        ({"namespace": "default"}, "receipt"),
        ({"receipt_schema": "trafficfuse-ownership-record-v1"}, "receipt"),
        ({"created_by": "someone-else"}, "created_by"),
        ({"kubeconfig": "~/.kube/config"}, "receipt"),
        ({"api_server": "https://k8s.example.com:6443"}, "not a local kind host"),
        ({"ca_fingerprint_sha256": "not-a-hash"}, "SHA-256"),
        ({"kind_node_names": []}, "kind_node_names"),
    ],
)
def test_invalid_or_conflicting_record_refused(tmp_path: Path, overrides: dict, match: str) -> None:
    record = _write_record(tmp_path / "record.json", **overrides)
    with pytest.raises(SafetyViolation, match=match):
        load_ownership_receipt(record)


def test_record_missing_required_fields_refused(tmp_path: Path) -> None:
    record = {
        "receipt_schema": RECORD_SCHEMA,
        "cluster": CLUSTER_NAME,
        "context": EXPECTED_CONTEXT,
        "namespace": NAMESPACE,
    }
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SafetyViolation, match="missing required fields"):
        load_ownership_receipt(path)


# --- live-facts ownership evidence -------------------------------------------------------


def test_pre_existing_unowned_cluster_refused(tmp_path: Path) -> None:
    # A cluster that merely carries the right *name* is not owned: no record.
    record_path = tmp_path / "missing-record.json"
    with pytest.raises(SafetyViolation, match="ownership receipt"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record_path,
            authorized=True,
            cluster_facts=_facts(),
        )


def test_unowned_labels_refused(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        require_verified_ownership(json.loads(record.read_text(encoding="utf-8")), _facts(namespace_labels={}))


def test_wrong_ca_identity_refused(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        require_verified_ownership(
            json.loads(record.read_text(encoding="utf-8")),
            _facts(ca_fingerprint_sha256=hashlib.sha256(b"other-ca").hexdigest()),
        )


def test_wrong_node_identities_refused(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        require_verified_ownership(
            json.loads(record.read_text(encoding="utf-8")),
            _facts(node_names=("incident-lab-control-plane",)),
        )


@pytest.mark.parametrize("name", ["incident-lab", "pcp-dev", "playground-crd", "vmware-k8s", "trafficfuse"])
def test_foreign_or_unowned_cluster_names_refused(name: str) -> None:
    with pytest.raises(SafetyViolation):
        validate_cluster_name(name)


def test_destroy_without_verified_ownership_refused(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    # Mismatched API server in collector-sourced (trusted transport) facts:
    # the verification gate must refuse, not the caller-facts gate.
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record,
            authorized=True,
            cluster_facts=_facts(api_server="https://127.0.0.1:7777", source="collector"),
        )


# --- full pipeline ------------------------------------------------------------------------


def test_full_ownership_pipeline_succeeds_with_consistent_fixtures(repo_root: Path) -> None:
    document = _kubeconfig_document()
    ca_fp = hashlib.sha256(b"synthetic-ca-certificate").hexdigest()
    kubeconfig = _write_kubeconfig(
        repo_root / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml", document
    )
    record = _write_record(
        repo_root / "record.json", ca_fingerprint_sha256=ca_fp
    )
    identity, verification = verify_cluster_ownership(
        kubeconfig_path=kubeconfig,
        record_path=record,
        facts=_facts(ca_fingerprint_sha256=ca_fp),
    )
    assert identity.api_server == FIXTURE_API_SERVER
    assert verification.verified
    assert verification.failures == ()


def test_full_ownership_pipeline_refuses_conflicting_record(repo_root: Path) -> None:
    kubeconfig = _write_kubeconfig(
        repo_root / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml", _kubeconfig_document()
    )
    record = _write_record(
        repo_root / "record.json",
        ca_fingerprint_sha256=hashlib.sha256(b"different-ca").hexdigest(),
    )
    with pytest.raises(SafetyViolation, match="does not match the creation record"):
        verify_cluster_ownership(
            kubeconfig_path=kubeconfig, record_path=record, facts=_facts()
        )


def test_verify_ownership_evidence_reports_every_check() -> None:
    verification = verify_ownership_evidence(
        {
            "api_server": FIXTURE_API_SERVER,
            "ca_fingerprint_sha256": FIXTURE_CA_FINGERPRINT,
            "kind_node_names": list(FIXTURE_NODES),
        },
        _facts(),
    )
    assert verification.verified
    payload = verification.to_dict()
    assert {check["name"] for check in payload["checks"]} == {
        "api_server",
        "ca_fingerprint",
        "kind_node_names",
        "owned_node_identities",
        "ownership_labels",
    }


def test_kubectl_prefix_always_pins_kubeconfig_and_context() -> None:
    prefix = kubectl_prefix()
    assert prefix == [
        "--kubeconfig",
        DEDICATED_KUBECONFIG,
        "--context",
        EXPECTED_CONTEXT,
    ]
    source = Path(lifecycle.__file__).read_text(encoding="utf-8")
    assert "def kubectl_prefix" in source
