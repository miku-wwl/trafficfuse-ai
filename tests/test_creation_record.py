"""Post-creation record tests: synthetic kubeconfig + mocked scoped reads only."""

import base64
import hashlib
import json
import subprocess

import pytest
import yaml

from trafficfuse.lab import cluster_facts, lifecycle
from trafficfuse.lab.cluster_facts import CommandResult, verify_live_ownership
from trafficfuse.lab.creation_record import record_cluster_creation
from trafficfuse.lab.lifecycle import SafetyViolation, load_ownership_receipt
from trafficfuse.lab.lifecycle_cli import main

NODE = "trafficfuse-lab-control-plane"
TIMESTAMP = "2026-10-19T01:02:03Z"
CA = b"synthetic-creation-ca"
FAKE_SECRET = "SyntheticPrivateMaterial" * 4


@pytest.fixture
def creation(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    kubeconfig = tmp_path / lifecycle.DEDICATED_KUBECONFIG
    kubeconfig.parent.mkdir(parents=True)
    kubeconfig.write_text(yaml.safe_dump({
        "clusters": [{"name": lifecycle.EXPECTED_CONTEXT, "cluster": {
            "server": "https://127.0.0.1:46123",
            "certificate-authority-data": base64.b64encode(CA).decode(),
        }}],
        "contexts": [{"name": lifecycle.EXPECTED_CONTEXT, "context": {
            "cluster": lifecycle.EXPECTED_CONTEXT,
        }}],
        "current-context": lifecycle.EXPECTED_CONTEXT,
        "users": [{"name": lifecycle.EXPECTED_CONTEXT, "user": {"client-key-data": FAKE_SECRET}}],
    }), encoding="utf-8")
    return kubeconfig, tmp_path / lifecycle.RECEIPT_DIRECTORY / lifecycle.RECEIPT_FILENAME


def executor_for(*, nodes=None, container=None, calls=None):
    if nodes is None:
        nodes = {"items": [{"metadata": {"name": NODE, "creationTimestamp": TIMESTAMP}}]}
    if container is None:
        container = {"name": f"/{NODE}", "cluster": lifecycle.CLUSTER_NAME, "role": "control-plane"}

    def executor(argv):
        if calls is not None:
            calls.append(list(argv))
        payload = nodes if argv[0] == "kubectl" else container
        return CommandResult(tuple(argv), 0, json.dumps(payload).encode(), b"")

    return executor


def generate(creation, **kwargs):
    kubeconfig, receipt = creation
    return record_cluster_creation(
        kubeconfig_path=kubeconfig, receipt_path=receipt, authorized=True,
        executor=kwargs.pop("executor", executor_for()), **kwargs,
    )


def test_generated_record_passes_real_loader_and_creates_state_directory(creation):
    kubeconfig, receipt = creation
    assert not receipt.parent.exists()
    calls = []
    generated = generate(creation, executor=executor_for(calls=calls))
    assert generated == load_ownership_receipt(receipt)
    assert generated == {
        "receipt_schema": lifecycle.RECORD_SCHEMA,
        "cluster": lifecycle.CLUSTER_NAME,
        "context": lifecycle.EXPECTED_CONTEXT,
        "namespace": lifecycle.NAMESPACE,
        "api_server": "https://127.0.0.1:46123",
        "ca_fingerprint_sha256": hashlib.sha256(CA).hexdigest(),
        "kind_node_names": [NODE], "created_at": TIMESTAMP,
        "created_by": "trafficfuse", "kubeconfig": lifecycle.DEDICATED_KUBECONFIG,
    }
    assert calls[0] == ["kubectl", "--kubeconfig", str(kubeconfig), "--context", lifecycle.EXPECTED_CONTEXT, "get", "nodes", "-o", "json"]
    assert calls[1][0:2] == ["docker", "inspect"]
    assert calls[1][-3:] == ["--type", "container", NODE]
    # The pre-namespace recorder cannot stand in for full ownership collection.
    with pytest.raises(SafetyViolation):
        verify_live_ownership(kubeconfig_path=kubeconfig, record_path=receipt, executor=executor_for())


def test_generation_is_deterministic_for_identical_observations(creation):
    generate(creation)
    original = creation[1].read_bytes()
    creation[1].unlink()  # synthetic fixture only
    generate(creation)
    assert creation[1].read_bytes() == original


@pytest.mark.parametrize("missing", ["receipt_schema", *lifecycle.RECORD_FIELDS])
def test_generated_record_missing_any_required_field_is_rejected(creation, missing):
    record = generate(creation)
    del record[missing]
    creation[1].write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SafetyViolation):
        load_ownership_receipt(creation[1])


@pytest.mark.parametrize("timestamp", [None, "", "2026-10-19", "2026-10-19T00:00:00", "2026-10-19T00:00:00+13:00", "2026-02-30T00:00:00Z"])
def test_invalid_observed_timestamps_rejected_before_write(creation, timestamp):
    nodes = {"items": [{"metadata": {"name": NODE, "creationTimestamp": timestamp}}]}
    with pytest.raises(SafetyViolation, match="created_at"):
        generate(creation, executor=executor_for(nodes=nodes))
    assert not creation[1].exists()


@pytest.mark.parametrize("nodes", [{}, {"items": []}, {"items": [{"metadata": {"name": NODE}}]}, {"items": [{"metadata": {"name": "incident-lab-control-plane", "creationTimestamp": TIMESTAMP}}]}])
def test_incomplete_or_foreign_nodes_rejected(creation, nodes):
    with pytest.raises(SafetyViolation, match="post-creation nodes"):
        generate(creation, executor=executor_for(nodes=nodes))
    assert not creation[1].exists()


@pytest.mark.parametrize("field,value", [("name", "/foreign-node"), ("cluster", "incident-lab"), ("role", "worker")])
def test_docker_identity_must_agree(creation, field, value):
    container = {"name": f"/{NODE}", "cluster": lifecycle.CLUSTER_NAME, "role": "control-plane"}
    container[field] = value
    with pytest.raises(SafetyViolation, match="Docker name/Kind labels"):
        generate(creation, executor=executor_for(container=container))
    assert not creation[1].exists()


def test_no_authorization_no_reads_or_record(creation):
    calls = []
    with pytest.raises(SafetyViolation, match="authorization"):
        record_cluster_creation(kubeconfig_path=creation[0], receipt_path=creation[1], authorized=False, executor=executor_for(calls=calls))
    assert calls == []
    assert not creation[1].exists()


def test_existing_record_and_foreign_state_path_refused_without_reads(creation):
    generate(creation)
    before = creation[1].read_bytes()
    calls = []
    with pytest.raises(SafetyViolation, match="already exists"):
        generate(creation, executor=executor_for(calls=calls))
    with pytest.raises(SafetyViolation, match="canonical"):
        record_cluster_creation(kubeconfig_path=creation[0], receipt_path=creation[1].with_name("foreign.json"), authorized=True, executor=executor_for(calls=calls))
    assert not calls
    assert creation[1].read_bytes() == before


def test_cli_generator_prints_only_record_metadata(creation, monkeypatch, capsys):
    monkeypatch.setattr(cluster_facts, "subprocess_executor", executor_for())
    assert main(["record-creation", "--kubeconfig", str(creation[0]), "--receipt", str(creation[1]), "--authorized"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == load_ownership_receipt(creation[1])
    assert FAKE_SECRET not in output.out + output.err
    assert "client-key-data" not in output.out


def test_collection_failure_is_redacted_and_writes_nothing(creation):
    def failed(argv):
        return CommandResult(tuple(argv), 1, b"", FAKE_SECRET.encode())
    with pytest.raises(SafetyViolation) as failure:
        generate(creation, executor=failed)
    assert FAKE_SECRET not in str(failure.value)
    assert not creation[1].exists()


@pytest.mark.parametrize("payload", [b"{", b"\xff", b'\xff\xfe{\x00}\x00'])
def test_invalid_collection_json_or_encoding_fails_without_record(creation, payload):
    def invalid(argv):
        return CommandResult(tuple(argv), 0, payload, b"")
    with pytest.raises(SafetyViolation, match="UTF-8 JSON"):
        generate(creation, executor=invalid)
    assert not creation[1].exists()


@pytest.mark.parametrize("failure", [OSError("synthetic"), subprocess.TimeoutExpired("synthetic", 10)])
def test_command_launch_and_timeout_failures_fail_closed(creation, failure):
    def failed(argv):
        raise failure
    with pytest.raises(SafetyViolation, match="could not complete"):
        generate(creation, executor=failed)
    assert not creation[1].exists()


@pytest.mark.parametrize("nodes", [[NODE, NODE], ["incident-lab-control-plane"], ["trafficfuse-lab-worker"]])
def test_inconsistent_record_nodes_are_rejected_by_real_loader(creation, nodes):
    record = generate(creation)
    record["kind_node_names"] = nodes
    creation[1].write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SafetyViolation, match="kind_node_names"):
        load_ownership_receipt(creation[1])
