"""Read-only live-facts collector tests (Phase 1A.2, review finding P1).

All tests use a mocked executor: no Kubernetes cluster, Docker daemon or
subprocess is contacted. The collector must fail closed on every deviation and
caller-supplied facts must never authorize destruction.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from trafficfuse.lab import lifecycle
from trafficfuse.lab.cluster_facts import (
    COLLECTOR_SOURCE,
    CommandResult,
    assert_read_only_command,
    collect_live_facts,
    subprocess_executor,
    verify_live_ownership,
    _command_verb,
)
from trafficfuse.lab.lifecycle import (
    CLUSTER_NAME,
    DEDICATED_KUBECONFIG,
    EXPECTED_CONTEXT,
    NAMESPACE,
    RECORD_SCHEMA,
    ClusterFacts,
    SafetyViolation,
    plan_cluster_destroy,
)

API_SERVER = "https://127.0.0.1:6443"
KUBE_CA = b"collector-test-ca"
CA_FINGERPRINT = hashlib.sha256(KUBE_CA).hexdigest()
NODES = ("trafficfuse-lab-control-plane",)
OWNED_LABELS = {
    "trafficfuse.io/owner": CLUSTER_NAME,
    "app.kubernetes.io/managed-by": CLUSTER_NAME,
}


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _write_kubeconfig(repo_root: Path) -> Path:
    import yaml

    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": EXPECTED_CONTEXT,
        "clusters": [
            {
                "name": CLUSTER_NAME,
                "cluster": {
                    "server": API_SERVER,
                    "certificate-authority-data": base64.b64encode(KUBE_CA).decode("ascii"),
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


def _write_record(path: Path, **overrides) -> Path:
    record = {
        "receipt_schema": RECORD_SCHEMA,
        "cluster": CLUSTER_NAME,
        "context": EXPECTED_CONTEXT,
        "namespace": NAMESPACE,
        "api_server": API_SERVER,
        "ca_fingerprint_sha256": CA_FINGERPRINT,
        "kind_node_names": list(NODES),
        "created_at": "2026-10-18T10:00:00+00:00",
        "created_by": "trafficfuse",
        "kubeconfig": DEDICATED_KUBECONFIG,
    }
    record.update(overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _make_executor(
    *,
    api_server: str = API_SERVER,
    ca_bytes: bytes = KUBE_CA,
    node_names=("trafficfuse-lab-control-plane",),
    labels=OWNED_LABELS,
    docker_name: str = "/trafficfuse-lab-control-plane",
    docker_ok: bool = True,
    view_fails: bool = False,
):
    calls: list[list[str]] = []

    def executor(argv):
        argv = list(argv)
        calls.append(argv)
        joined = " ".join(argv)
        if "config" in argv and "view" in argv:
            if view_fails:
                return CommandResult(tuple(argv), 1, b"", b"connection refused")
            document = {
                "clusters": [
                    {
                        "name": CLUSTER_NAME,
                        "cluster": {
                            "server": api_server,
                            "certificate-authority-data": base64.b64encode(ca_bytes).decode("ascii"),
                        },
                    }
                ]
            }
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        # Token-based matching: every collector command is prefixed with
        # `--kubeconfig <path> --context <ctx>`, so the verb is never argv[1].
        if "get" in argv and "nodes" in argv:
            document = {"items": [{"metadata": {"name": name}} for name in node_names]}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if "get" in argv and "namespace" in argv:
            document = {"metadata": {"name": NAMESPACE, "labels": labels}}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if argv[0] == "docker":
            if not docker_ok:
                return CommandResult(tuple(argv), 1, b"", b"No such object")
            return CommandResult(tuple(argv), 0, docker_name.encode(), b"")
        raise AssertionError(f"unexpected collector command: {joined}")

    executor.calls = calls  # type: ignore[attr-defined]
    return executor


# --- happy path and command scoping -------------------------------------------------


def test_verify_live_ownership_succeeds_with_trusted_facts(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    executor = _make_executor()
    identity, verification = verify_live_ownership(
        kubeconfig_path=kubeconfig, record_path=record, executor=executor
    )
    assert identity.cluster == CLUSTER_NAME
    assert verification.verified
    assert verification.trusted is True
    assert verification.failures == ()


def test_collector_marks_facts_as_collector_sourced(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    facts = collect_live_facts(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    assert facts.source == COLLECTOR_SOURCE
    assert facts.as_cluster_facts().source == COLLECTOR_SOURCE
    assert facts.api_server == API_SERVER
    assert facts.ca_fingerprint_sha256 == CA_FINGERPRINT
    assert facts.node_names == NODES
    assert facts.docker_node_identities == ("/trafficfuse-lab-control-plane",)
    assert facts.namespace_labels == OWNED_LABELS


def test_every_collector_command_is_scoped_and_read_only(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    executor = _make_executor()
    collect_live_facts(kubeconfig_path=kubeconfig, record_path=record, executor=executor)
    assert executor.calls, "collector issued no commands"
    for argv in executor.calls:
        assert_read_only_command(argv)
        for verb in ("create", "apply", "delete", "patch", "edit", "scale", "replace"):
            assert verb not in argv, argv
        if argv[0] == "docker":
            # Docker talks to the local daemon: no kubeconfig flags exist.
            # Identity comes from the inspected container name, cross-checked
            # against the creation record in verify_live_ownership().
            assert "--kubeconfig" not in argv and "--context" not in argv, argv
            continue
        # Cluster commands: explicit dedicated kubeconfig and exact context,
        # never the ambient selection.
        assert "--kubeconfig" in argv and "--context" in argv, argv
        assert argv[argv.index("--context") + 1] == EXPECTED_CONTEXT, argv
        assert argv[argv.index("--kubeconfig") + 1] == str(kubeconfig), argv


def test_collector_refuses_mutation_verbs_before_execution() -> None:
    for argv in (
        ["kubectl", "delete", "namespace", "trafficfuse-system"],
        ["kubectl", "apply", "-f", "deploy/lab"],
        ["kubectl", "patch", "service", "trafficfuse-live"],
        ["docker", "rm", "-f", "trafficfuse-lab-control-plane"],
    ):
        with pytest.raises(SafetyViolation, match="read-only"):
            assert_read_only_command(argv)
        with pytest.raises(SafetyViolation, match="read-only"):
            subprocess_executor(argv)  # refuses before any subprocess runs


def test_collector_rejects_unknown_binary() -> None:
    with pytest.raises(SafetyViolation, match="only runs"):
        assert_read_only_command(["sh", "-c", "kubectl delete all"])


def test_flag_values_are_never_mistaken_for_the_verb() -> None:
    # Regression: a value-taking flag's value (e.g. the kubeconfig path, or a
    # path containing a mutating-looking token) must not be scanned as the verb.
    scoped = [
        "kubectl",
        "--kubeconfig",
        "/repo/.runtime/kubeconfig/trafficfuse-lab.yaml",
        "--context",
        "kind-trafficfuse-lab",
        "get",
        "nodes",
        "-o",
        "json",
    ]
    assert_read_only_command(scoped)  # must pass
    verb = _command_verb(scoped)
    assert verb == "get"
    # A flag value that looks like a mutating verb is still not the verb.
    assert _command_verb(["kubectl", "--kubeconfig", "delete", "get", "nodes"]) == "get"
    # A command whose only positional is consumed as a flag value has no verb.
    assert _command_verb(["kubectl", "--kubeconfig", "delete"]) is None
    with pytest.raises(SafetyViolation, match="cannot determine the verb"):
        assert_read_only_command(["kubectl", "--kubeconfig", "delete"])


# --- fail-closed on inconsistent live evidence ----------------------------------------


def test_wrong_api_server_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    # The kubeconfig file matches the record, but the live cluster reports a
    # different API server: the evidence cross-check must refuse it.
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(api_server="https://127.0.0.1:7777"),
        )


def test_wrong_ca_fingerprint_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    # Same shape, different CA: the live CA fingerprint cross-check must refuse.
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(ca_bytes=b"a-different-ca"),
        )


def test_unexpected_node_identities_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(node_names=("incident-lab-control-plane",)),
        )


def test_missing_docker_node_identity_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="docker node identity"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(docker_ok=False),
        )


def test_conflicting_docker_node_identity_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="docker node identities do not match"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(docker_name="/some-other-container"),
        )


def test_missing_namespace_ownership_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(labels={"app.kubernetes.io/name": "trafficfuse"}),
        )


def test_view_command_failure_fails_closed(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="live facts collection failed"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(view_fails=True),
        )


def test_missing_creation_record_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    with pytest.raises(SafetyViolation, match="ownership receipt"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=tmp_path / "missing.json",
            executor=_make_executor(),
        )


def test_inconsistent_post_creation_record_refused(repo_root: Path, tmp_path: Path) -> None:
    # The record names two nodes (control plane + worker), but the live cluster
    # reports only one: inconsistent post-creation evidence, refused.
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(
        tmp_path / "record.json",
        kind_node_names=["trafficfuse-lab-control-plane", "trafficfuse-lab-worker"],
    )
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(),
        )


def test_wrong_context_in_kubeconfig_document_refused(repo_root: Path, tmp_path: Path) -> None:
    import yaml

    document = yaml.safe_load(_write_kubeconfig(repo_root).read_text(encoding="utf-8"))
    document["contexts"].append(
        {"name": "kind-playground-crd", "context": {"cluster": "playground-crd", "user": "x"}}
    )
    kubeconfig = repo_root / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    kubeconfig.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(SafetyViolation, match="foreign context"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=_write_record(tmp_path / "record.json"),
            executor=_make_executor(),
        )


def test_foreign_kubeconfig_path_refused(tmp_path: Path) -> None:
    foreign = tmp_path / "not_this_project" / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
    with pytest.raises(SafetyViolation, match="repository-owned"):
        verify_live_ownership(
            kubeconfig_path=foreign,
            record_path=tmp_path / "record.json",
            executor=_make_executor(),
        )


# --- trust boundary -------------------------------------------------------------------


def test_caller_supplied_facts_never_authorize_destruction(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    forged = ClusterFacts(
        api_server=API_SERVER,
        ca_fingerprint_sha256=CA_FINGERPRINT,
        node_names=NODES,
        namespace_labels=OWNED_LABELS,
        source="caller",
    )
    with pytest.raises(SafetyViolation, match="caller-supplied facts are never sole proof"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record,
            authorized=True,
            cluster_facts=forged,
        )


def test_collector_sourced_facts_reach_the_phase_1a_refusal(tmp_path: Path) -> None:
    # With collector-sourced, verified facts and authorization, Phase 1A still
    # refuses to mutate — but it reaches the final phase gate, proving the
    # trust chain itself is sound.
    record = _write_record(tmp_path / "record.json")
    trusted = ClusterFacts(
        api_server=API_SERVER,
        ca_fingerprint_sha256=CA_FINGERPRINT,
        node_names=NODES,
        namespace_labels=OWNED_LABELS,
        source=COLLECTOR_SOURCE,
    )
    with pytest.raises(SafetyViolation, match="not enabled in Phase 1A"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record,
            authorized=True,
            cluster_facts=trusted,
        )


def test_unauthorized_destroy_attempt_refused(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    trusted = ClusterFacts(
        api_server=API_SERVER,
        ca_fingerprint_sha256=CA_FINGERPRINT,
        node_names=NODES,
        namespace_labels=OWNED_LABELS,
        source=COLLECTOR_SOURCE,
    )
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record,
            authorized=False,
            cluster_facts=trusted,
        )


def test_caller_facts_verification_is_marked_untrusted(tmp_path: Path) -> None:
    from trafficfuse.lab.lifecycle import verify_ownership_evidence

    record = json.loads(_write_record(tmp_path / "record.json").read_text(encoding="utf-8"))
    verification = verify_ownership_evidence(
        record,
        ClusterFacts(
            api_server=API_SERVER,
            ca_fingerprint_sha256=CA_FINGERPRINT,
            node_names=NODES,
            namespace_labels=OWNED_LABELS,
        ),
    )
    assert verification.verified is True
    assert verification.trusted is False
