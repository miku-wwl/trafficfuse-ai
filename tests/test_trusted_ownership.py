"""Trusted-ownership provenance and credential-redaction tests (Phase 1B entry review).

Findings covered (no real cluster access anywhere in this file):

* P2-a — spoofed collector provenance: ``source="collector"`` was a
  caller-settable string. Trust is now established by execution: only facts
  carrying a collector execution receipt are trusted, and the destructive
  gate performs the collection itself.
* P2-b — Kubernetes credential disclosure: ``kubectl config view --raw``
  printed client key material. Identity now comes from the trusted local
  parser, outputs are redacted to the four identity fields, and command
  output embedded in failure messages is stripped of credential-shaped
  material.

All fixtures are synthetic; the kubeconfig deliberately contains fake
credential material so the tests can prove it is never disclosed.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from trafficfuse.lab import lifecycle
from trafficfuse.lab.cluster_facts import (
    CommandResult,
    LiveFacts,
    TrustedOwnership,
    is_collector_executed,
    redact_for_message,
    verify_live_ownership,
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
    verify_ownership_evidence,
)

API_SERVER = "https://127.0.0.1:6443"
CA_BYTES = b"trusted-ownership-test-ca"
CA_FINGERPRINT = hashlib.sha256(CA_BYTES).hexdigest()
NODES = ("trafficfuse-lab-control-plane",)
OWNED_LABELS = {
    "trafficfuse.io/owner": CLUSTER_NAME,
    "app.kubernetes.io/managed-by": CLUSTER_NAME,
}

# Fake credential material that must never appear in any output.
FAKE_CLIENT_KEY = "FAKEClientKeyData0000111122223333444455556666777788889999AAAABBBBCCCC"
FAKE_CLIENT_CERT = "FAKEClientCertData0000111122223333444455556666777788889999AAAABBBBCCCC"
FAKE_STATIC_TOKEN = "FAKEStaticBearerToken0000111122223333444455556666777788889999"
FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----"
)
SECRET_MATERIAL = (FAKE_CLIENT_KEY, FAKE_CLIENT_CERT, FAKE_STATIC_TOKEN, FAKE_PEM)


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _write_kubeconfig(repo_root: Path, *, server: str = API_SERVER) -> Path:
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
                    "certificate-authority-data": base64.b64encode(CA_BYTES).decode("ascii"),
                },
            }
        ],
        "contexts": [
            {
                "name": EXPECTED_CONTEXT,
                "context": {"cluster": CLUSTER_NAME, "user": "kind-trafficfuse-lab"},
            }
        ],
        # Synthetic credential-bearing user section: the parser may read it
        # internally, but nothing may ever print or archive it.
        "users": [
            {
                "name": "kind-trafficfuse-lab",
                "user": {
                    "client-certificate-data": FAKE_CLIENT_CERT,
                    "client-key-data": FAKE_CLIENT_KEY,
                },
            }
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
    node_names=NODES,
    labels=OWNED_LABELS,
    docker_name: str = "/trafficfuse-lab-control-plane",
    failing_stderr: bytes = b"",
):
    def executor(argv):
        argv = list(argv)
        if "get" in argv and "nodes" in argv:
            if failing_stderr:
                return CommandResult(tuple(argv), 1, b"", failing_stderr)
            document = {"items": [{"metadata": {"name": name}} for name in node_names]}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if "get" in argv and "namespace" in argv:
            document = {"metadata": {"name": NAMESPACE, "labels": dict(labels)}}
            return CommandResult(tuple(argv), 0, json.dumps(document).encode(), b"")
        if argv[0] == "docker":
            return CommandResult(tuple(argv), 0, docker_name.encode(), b"")
        raise AssertionError(f"unexpected collector command: {' '.join(argv)}")

    return executor


def _assert_no_secrets(text: str) -> None:
    for secret in SECRET_MATERIAL:
        assert secret not in text, f"credential material leaked: {secret[:24]}..."


# --- P2-a: spoofed provenance ------------------------------------------------------------------


def test_caller_cannot_set_a_collector_provenance_string() -> None:
    # The Phase 1A.2 escape hatch (a caller-settable source field) is gone.
    with pytest.raises(TypeError):
        ClusterFacts(  # type: ignore[call-arg]
            api_server=API_SERVER,
            ca_fingerprint_sha256=CA_FINGERPRINT,
            node_names=NODES,
            namespace_labels=OWNED_LABELS,
            source="collector",
        )


def test_forged_cluster_facts_object_never_authorizes_destruction(repo_root: Path, tmp_path: Path) -> None:
    # A forged, content-matching ClusterFacts object cannot be handed to the
    # destructive gate at all: the facts parameter no longer exists.
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(TypeError):
        plan_cluster_destroy(  # type: ignore[call-arg]
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=True,
            cluster_facts=ClusterFacts(
                api_server=API_SERVER,
                ca_fingerprint_sha256=CA_FINGERPRINT,
                node_names=NODES,
                namespace_labels=OWNED_LABELS,
            ),
        )
    # And a forged LiveFacts without the execution receipt is never trusted.
    forged = LiveFacts(
        api_server=API_SERVER,
        ca_fingerprint_sha256=CA_FINGERPRINT,
        node_names=NODES,
        namespace_labels=OWNED_LABELS,
        docker_node_identities=NODES,
    )
    verification = verify_ownership_evidence(
        json.loads(record.read_text(encoding="utf-8")), forged
    )
    assert verification.verified is True
    assert verification.trusted is False


def test_matching_but_unverified_receipt_never_authorizes_destruction(
    repo_root: Path, tmp_path: Path
) -> None:
    # A perfectly matching creation record proves nothing without collection:
    # the gate must still require an actual collector execution.
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="no collector execution was performed"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig, receipt_path=record, authorized=True
        )


def test_collector_execution_skipped_fails_closed(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="no collector execution was performed"):
        plan_cluster_destroy(kubeconfig_path=kubeconfig, receipt_path=record, authorized=True)


def test_failed_live_collection_fails_closed(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="live facts collection failed"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=True,
            executor=_make_executor(failing_stderr=b"connection refused"),
        )


def test_docker_and_kubernetes_identities_disagree_fails_closed(
    repo_root: Path, tmp_path: Path
) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="docker node identities do not match"):
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(docker_name="/some-other-container"),
        )


def test_valid_mocked_collector_returns_verified_trusted_facts(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    outcome = verify_live_ownership(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    assert isinstance(outcome, TrustedOwnership)
    assert outcome.verification.verified is True
    assert outcome.verification.trusted is True
    assert is_collector_executed(outcome.facts)
    assert outcome.identity.api_server == API_SERVER
    assert outcome.identity.ca_fingerprint_sha256 == CA_FINGERPRINT


def test_unauthorized_deletion_remains_refused(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    with pytest.raises(SafetyViolation, match="authorization"):
        plan_cluster_destroy(
            kubeconfig_path=kubeconfig,
            receipt_path=record,
            authorized=False,
            executor=_make_executor(),
        )


# --- P2-b: credential redaction -----------------------------------------------------------------


def test_trusted_outcome_output_contains_no_credential_material(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    outcome = verify_live_ownership(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    serialized = json.dumps(outcome.to_dict())
    _assert_no_secrets(serialized)
    # The minimal identity information stays verifiable.
    payload = outcome.to_dict()
    assert set(payload["identity"]) == {
        "context",
        "cluster",
        "api_server",
        "ca_fingerprint_sha256",
    }
    assert payload["identity"]["api_server"] == API_SERVER
    assert payload["identity"]["ca_fingerprint_sha256"] == CA_FINGERPRINT
    # No credential-bearing key exists anywhere in the payload.
    for key in ("client-key-data", "client-certificate-data", "token", "password", "users"):
        assert key not in serialized.lower()


def test_failure_messages_never_expose_credential_material(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    # Record mismatch triggers a refusal whose message must stay clean.
    record = _write_record(tmp_path / "record.json", api_server="https://127.0.0.1:7777")
    with pytest.raises(SafetyViolation) as excinfo:
        verify_live_ownership(
            kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
        )
    _assert_no_secrets(str(excinfo.value))
    # The message names the mismatch class only; the kubeconfig user section
    # is never echoed.
    assert "client" not in str(excinfo.value).lower()
    assert "does not match the creation record" in str(excinfo.value)


def test_failing_command_output_is_redacted(repo_root: Path, tmp_path: Path) -> None:
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    stderr = (
        b"Unable to connect to the server: dial tcp 127.0.0.1:6443: "
        b"connection refused\n"
        b"debug: using client key -----BEGIN RSA PRIVATE KEY-----\n"
        b"MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefakefakefakefake\n"
        b"-----END RSA PRIVATE KEY-----\n"
        b"token: QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3g=\n"
    )
    with pytest.raises(SafetyViolation) as excinfo:
        verify_live_ownership(
            kubeconfig_path=kubeconfig,
            record_path=record,
            executor=_make_executor(failing_stderr=stderr),
        )
    message = str(excinfo.value)
    assert "[REDACTED]" in message  # credential-shaped material was stripped
    _assert_no_secrets(message)
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3g" not in message


def test_redact_for_message_unit() -> None:
    assert redact_for_message("all good") == "all good"
    redacted = redact_for_message(f"key {FAKE_CLIENT_KEY} end")
    assert FAKE_CLIENT_KEY not in redacted
    assert "[REDACTED]" in redacted
    pem = redact_for_message(FAKE_PEM)
    assert "fakefake" not in pem
    assert len(redact_for_message("x" * 5000)) <= 400


def test_cli_verify_collect_output_is_redacted(repo_root: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    from trafficfuse.lab import cluster_facts
    from trafficfuse.lab.lifecycle_cli import main as lifecycle_cli_main

    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    # The CLI's --collect path must run the (mocked) trusted pipeline and
    # print only the redacted identity + verification payload.
    monkeypatch.setattr(cluster_facts, "subprocess_executor", _make_executor())
    exit_code = lifecycle_cli_main(
        [
            "verify",
            "--kubeconfig",
            str(kubeconfig),
            "--receipt",
            str(record),
            "--collect",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    _assert_no_secrets(captured.out)
    _assert_no_secrets(captured.err)
    assert payload["identity"]["api_server"] == API_SERVER
    assert payload["identity"]["ca_fingerprint_sha256"] == CA_FINGERPRINT
    assert payload["verification"]["verified"] is True
    assert payload["verification"]["trusted"] is True
    # The executed read-only command trace is printed for audit.
    assert payload["collector_commands"]
    for command in payload["collector_commands"]:
        assert command[0] in {"kubectl", "docker"}
        assert "--raw" not in command and "view" not in command
    for key in ("client-key-data", "client-certificate-data", "token", "password", "users"):
        assert key not in captured.out.lower()


def test_evidence_artifacts_exclude_credential_material(repo_root: Path, tmp_path: Path) -> None:
    # The verification payload never becomes an evidence-artifact schema with
    # credential fields: audit data is identity + verification only.
    kubeconfig = _write_kubeconfig(repo_root)
    record = _write_record(tmp_path / "record.json")
    outcome = verify_live_ownership(
        kubeconfig_path=kubeconfig, record_path=record, executor=_make_executor()
    )
    payload = outcome.to_dict()
    serialized = json.dumps(payload).lower()
    for key in ("client-key-data", "client-certificate-data", "token", "password", "secret", "credential"):
        assert key not in serialized
    # Node identity remains verifiable in the audit data.
    assert payload["live_facts"]["node_names"] == list(NODES)
