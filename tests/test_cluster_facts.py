"""Read-only live-facts collector tests (Phase 1A.2 + Phase 1B entry review).

All tests use a mocked executor: no Kubernetes cluster, Docker daemon or
subprocess is contacted. The collector must fail closed on every deviation,
trust is established by execution (never by a caller-settable string), and
caller-supplied facts must never authorize destruction.
"""

import base64
import hashlib
import json
from dataclasses import fields
from pathlib import Path

import pytest

from trafficfuse.lab import lifecycle
from trafficfuse.lab.cluster_facts import (
    CommandResult,
    LiveFacts,
    assert_read_only_command,
    collect_live_facts,
    is_collector_executed,
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


def _write_kubeconfig(repo_root: Path, *, server: str = API_SERVER, ca_bytes: bytes = KUBE_CA) -> Path:
    import yaml

    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": EXPECTED_CONTEXT,
        "clusters": [
            {
                "name": CLUSTER_NAME,
                "cluster": {
                    "server": server,
                    "certificate-authority-data": base64.b64encode(ca_bytes).decode("ascii"),
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
    node_names=("trafficfuse-lab-control-plane",),
    labels=OWNED_LABELS,
    docker_name: str = "/trafficfuse-lab-control-plane",
    docker_ok: bool = True,
    nodes_fail: bool = False,
):
    calls: list[list[str]] = []

    def executor(argv):
        argv = list(argv)
        calls.append(argv)
        joined = " ".join(argv)
        if "get" in argv and "nodes" in argv:
            if nodes_fail:
                return CommandResult(tuple(argv), 1, b"", b"connection refused")
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
    outcome = verify_live_ownership(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    assert outcome.identity.cluster == CLUSTER_NAME
    assert outcome.verification.verified
    assert outcome.verification.trusted is True
    assert outcome.verification.failures == ()
    # The trusted operation performed the collection itself: an auditable,
    # read-only command trace exists and matches the issued commands.
    assert outcome.commands, "trusted verification executed no collector commands"
    assert outcome.facts.execution is not None
    assert tuple(outcome.facts.execution.commands) == tuple(outcome.commands)


def test_collected_facts_carry_a_collector_execution_receipt(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    facts = collect_live_facts(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    assert facts.execution is not None
    assert facts.execution.nonce  # unguessable per-run nonce
    assert facts.execution.commands  # exact executed trace
    assert is_collector_executed(facts) is True
    # Identity is read from the trusted local parser, not from a raw view.
    assert facts.api_server == API_SERVER
    assert facts.ca_fingerprint_sha256 == CA_FINGERPRINT
    assert facts.node_names == NODES
    assert facts.docker_node_identities == ("/trafficfuse-lab-control-plane",)
    assert facts.namespace_labels == OWNED_LABELS


def test_execution_nonce_is_unique_per_collection(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    first = collect_live_facts(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    second = collect_live_facts(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    assert first.execution is not None and second.execution is not None
    assert first.execution.nonce != second.execution.nonce


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


def test_collector_never_issues_a_raw_kubeconfig_view(repo_root: Path, tmp_path: Path) -> None:
    # Credential disclosure fix: the collector must never run a command that
    # prints the raw kubeconfig (client key material); identity comes from
    # the trusted local parser instead.
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    executor = _make_executor()
    collect_live_facts(kubeconfig_path=kubeconfig, record_path=record, executor=executor)
    for argv in executor.calls:
        assert "config" not in argv and "view" not in argv, argv
        assert "--raw" not in argv and "--minify" not in argv, argv


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


def test_kubeconfig_api_server_mismatched_with_record_refused(repo_root: Path, tmp_path: Path) -> None:
    # The dedicated kubeconfig document points at a different API server than
    # the creation record: identity cross-check must refuse before collection.
    kubeconfig = _write_kubeconfig(repo_root, server="https://127.0.0.1:7777")
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="does not match the creation record"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(),
        )


def test_kubeconfig_ca_mismatched_with_record_refused(repo_root: Path, tmp_path: Path) -> None:
    # Same shape, different CA in the kubeconfig document: the CA fingerprint
    # cross-check against the record must refuse.
    kubeconfig = _write_kubeconfig(repo_root, ca_bytes=b"a-different-ca")
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="does not match the creation record"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(),
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


def test_live_command_failure_fails_closed(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="live facts collection failed"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(nodes_fail=True),
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


# --- trust boundary: trust is established by execution, not declaration -----------------


def test_cluster_facts_has_no_caller_settable_provenance_string() -> None:
    # Phase 1B entry review finding P2-a: a provenance string is not a
    # guarantee. ClusterFacts (the caller-facing inspection type) has no
    # source field at all — trust cannot be declared, only collected.
    field_names = {f.name for f in fields(ClusterFacts)}
    assert "source" not in field_names
    assert field_names == {"api_server", "ca_fingerprint_sha256", "node_names", "namespace_labels"}
    with pytest.raises(TypeError):
        ClusterFacts(  # type: ignore[call-arg]
            api_server=API_SERVER,
            ca_fingerprint_sha256=CA_FINGERPRINT,
            node_names=NODES,
            namespace_labels=OWNED_LABELS,
            source="collector",
        )


def test_directly_constructed_live_facts_are_never_trusted() -> None:
    # A forged LiveFacts without the collector execution receipt fails the
    # trust decision everywhere.
    forged = LiveFacts(
        api_server=API_SERVER,
        ca_fingerprint_sha256=CA_FINGERPRINT,
        node_names=NODES,
        namespace_labels=OWNED_LABELS,
        docker_node_identities=("/trafficfuse-lab-control-plane",),
    )
    assert is_collector_executed(forged) is False
    verification = lifecycle.verify_ownership_evidence(
        {
            "api_server": API_SERVER,
            "ca_fingerprint_sha256": CA_FINGERPRINT,
            "kind_node_names": list(NODES),
        },
        forged,
    )
    assert verification.verified is True  # content matches...
    assert verification.trusted is False  # ...but it is not trusted evidence


def test_skipped_collector_execution_never_authorizes_destruction(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "record.json")
    # No collector execution was performed (executor omitted): the destructive
    # gate must fail closed before any trust decision.
    with pytest.raises(SafetyViolation, match="no collector execution was performed"):
        plan_cluster_destroy(
            kubeconfig_path=DEDICATED_KUBECONFIG,
            receipt_path=record,
            authorized=True,
        )


def test_valid_mocked_collection_reaches_the_deletion_scope_refusal(
    repo_root: Path, tmp_path: Path
) -> None:
    # With authorization and a valid mocked collector execution, the guard
    # chain reaches the final scope refusal — proving the trust chain itself
    # is sound (collection actually ran and verified).
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="outside the Phase 1B authorization scope"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=True,
            executor=_make_executor(),
        )


def test_failed_collection_never_authorizes_destruction(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="live facts collection failed"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=True,
            executor=_make_executor(nodes_fail=True),
        )


def test_mismatched_collection_never_authorizes_destruction(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="ownership could not be verified"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=True,
            executor=_make_executor(node_names=("incident-lab-control-plane",)),
        )


def test_unauthorized_destroy_attempt_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=False,
            executor=_make_executor(),
        )


def test_caller_facts_verification_is_marked_untrusted(tmp_path: Path) -> None:
    record = json.loads(_write_record(tmp_path / "record.json").read_text(encoding="utf-8"))
    verification = lifecycle.verify_ownership_evidence(
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
