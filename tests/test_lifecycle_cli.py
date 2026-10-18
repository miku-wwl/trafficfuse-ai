"""Offline lifecycle CLI tests (Phase 1A.1): preflight, verify, refusals.

No cluster is contacted; all inputs are synthetic fixtures.
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
)
from trafficfuse.lab.lifecycle_cli import main as lifecycle_cli_main

FIXTURE_API_SERVER = "https://127.0.0.1:6443"
FIXTURE_CA = b"synthetic-cli-ca"
FIXTURE_CA_FINGERPRINT = hashlib.sha256(FIXTURE_CA).hexdigest()
FIXTURE_NODES = ["trafficfuse-lab-control-plane"]


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """Point the trusted repository root at a synthetic repository layout."""
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _kubeconfig(repo_root: Path, tmp_path: Path) -> Path:
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
                    "certificate-authority-data": base64.b64encode(FIXTURE_CA).decode("ascii"),
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


def _record(tmp_path: Path) -> Path:
    path = tmp_path / "cluster-trafficfuse-lab.json"
    path.write_text(
        json.dumps(
            {
                "receipt_schema": RECORD_SCHEMA,
                "cluster": CLUSTER_NAME,
                "context": EXPECTED_CONTEXT,
                "namespace": NAMESPACE,
                "api_server": FIXTURE_API_SERVER,
                "ca_fingerprint_sha256": FIXTURE_CA_FINGERPRINT,
                "kind_node_names": FIXTURE_NODES,
                "created_at": "2026-10-18T10:00:00+00:00",
                "created_by": "trafficfuse",
                "kubeconfig": DEDICATED_KUBECONFIG,
            }
        ),
        encoding="utf-8",
    )
    return path


def _facts(tmp_path: Path, *, ca: str | None = None, labels: dict | None = None) -> Path:
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps(
            {
                "api_server": FIXTURE_API_SERVER,
                "ca_fingerprint_sha256": ca or FIXTURE_CA_FINGERPRINT,
                "node_names": FIXTURE_NODES,
                "namespace_labels": labels
                if labels is not None
                else {
                    "trafficfuse.io/owner": CLUSTER_NAME,
                    "app.kubernetes.io/managed-by": CLUSTER_NAME,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cli_verify_reports_verified_ownership(repo_root: Path, tmp_path: Path, capsys) -> None:
    exit_code = lifecycle_cli_main(
        [
            "verify",
            "--kubeconfig",
            str(_kubeconfig(repo_root, tmp_path)),
            "--receipt",
            str(_record(tmp_path)),
            "--facts",
            str(_facts(tmp_path)),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["verified"] is True
    assert payload["identity"]["cluster"] == CLUSTER_NAME


def test_cli_verify_refuses_unverified_ownership(repo_root: Path, tmp_path: Path, capsys) -> None:
    exit_code = lifecycle_cli_main(
        [
            "verify",
            "--kubeconfig",
            str(_kubeconfig(repo_root, tmp_path)),
            "--receipt",
            str(_record(tmp_path)),
            "--facts",
            str(_facts(tmp_path, labels={})),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "REFUSED" in captured.err


def _make_executor(*, nodes_fail: bool = False):
    """Read-only collector mock: never contacts a cluster or Docker."""

    def executor(argv):
        argv = list(argv)
        if "get" in argv and "nodes" in argv:
            if nodes_fail:
                from trafficfuse.lab.cluster_facts import CommandResult

                return CommandResult(tuple(argv), 1, b"", b"connection refused")
            document = {"items": [{"metadata": {"name": "trafficfuse-lab-control-plane"}}]}
            return _ok(argv, json.dumps(document))
        if "get" in argv and "namespace" in argv:
            document = {
                "metadata": {
                    "name": NAMESPACE,
                    "labels": {
                        "trafficfuse.io/owner": CLUSTER_NAME,
                        "app.kubernetes.io/managed-by": CLUSTER_NAME,
                    },
                }
            }
            return _ok(argv, json.dumps(document))
        if argv[0] == "docker":
            return _ok(argv, "/trafficfuse-lab-control-plane")
        raise AssertionError(f"unexpected collector command: {' '.join(argv)}")

    return executor


def _ok(argv, text):
    from trafficfuse.lab.cluster_facts import CommandResult

    return CommandResult(tuple(argv), 0, text.encode(), b"")


def test_cli_create_and_destroy_still_refuse(repo_root: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    kubeconfig = str(_kubeconfig(repo_root, tmp_path))
    assert (
        lifecycle_cli_main(
            ["create", "--kubeconfig", kubeconfig, "--authorized"]
        )
        == 2
    )
    # destroy accepts no caller-supplied facts: without a collector execution
    # it refuses at the collection gate.
    assert (
        lifecycle_cli_main(
            [
                "destroy",
                "--kubeconfig",
                kubeconfig,
                "--receipt",
                str(_record(tmp_path)),
                "--authorized",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "no collector execution was performed" in captured.err
    # With --collect the guard chain runs the (mocked) trusted collection and
    # then reaches the deletion-scope refusal — no real subprocess, no cluster.
    monkeypatch.setattr(
        "trafficfuse.lab.cluster_facts.subprocess_executor", _make_executor()
    )
    assert (
        lifecycle_cli_main(
            [
                "destroy",
                "--kubeconfig",
                kubeconfig,
                "--receipt",
                str(_record(tmp_path)),
                "--collect",
                "--authorized",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "outside the Phase 1B authorization scope" in captured.err


def test_cli_destroy_rejects_removed_facts_flag(repo_root: Path, tmp_path: Path, capsys) -> None:
    # Regression: the Phase 1A.2 `destroy --facts` channel (caller-supplied
    # JSON) no longer exists. argparse must reject it (exit code 2).
    with pytest.raises(SystemExit) as excinfo:
        lifecycle_cli_main(
            [
                "destroy",
                "--kubeconfig",
                str(_kubeconfig(repo_root, tmp_path)),
                "--receipt",
                str(_record(tmp_path)),
                "--facts",
                str(_facts(tmp_path)),
                "--authorized",
            ]
        )
    assert excinfo.value.code == 2


def test_cli_preflight_prints_non_mutating_plan(capsys) -> None:
    exit_code = lifecycle_cli_main(["preflight", "--kubeconfig", DEDICATED_KUBECONFIG])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["mutating"] is False
    assert payload["cluster"] == CLUSTER_NAME
