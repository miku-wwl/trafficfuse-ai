"""Offline lifecycle CLI tests (Phase 1A.1): preflight, verify, refusals.

No cluster is contacted; all inputs are synthetic fixtures.
"""

import base64
import hashlib
import json
from pathlib import Path

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


def _kubeconfig(tmp_path: Path) -> Path:
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
    path = tmp_path / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml"
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


def test_cli_verify_reports_verified_ownership(tmp_path: Path, capsys) -> None:
    exit_code = lifecycle_cli_main(
        [
            "verify",
            "--kubeconfig",
            str(_kubeconfig(tmp_path)),
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


def test_cli_verify_refuses_unverified_ownership(tmp_path: Path, capsys) -> None:
    exit_code = lifecycle_cli_main(
        [
            "verify",
            "--kubeconfig",
            str(_kubeconfig(tmp_path)),
            "--receipt",
            str(_record(tmp_path)),
            "--facts",
            str(_facts(tmp_path, labels={})),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "REFUSED" in captured.err


def test_cli_create_and_destroy_still_refuse(tmp_path: Path, capsys) -> None:
    assert (
        lifecycle_cli_main(
            ["create", "--kubeconfig", str(_kubeconfig(tmp_path)), "--authorized"]
        )
        == 2
    )
    assert (
        lifecycle_cli_main(
            [
                "destroy",
                "--kubeconfig",
                str(_kubeconfig(tmp_path)),
                "--receipt",
                str(_record(tmp_path)),
                "--authorized",
            ]
        )
        == 2
    )
    assert (
        lifecycle_cli_main(
            [
                "destroy",
                "--kubeconfig",
                str(_kubeconfig(tmp_path)),
                "--receipt",
                str(_record(tmp_path)),
                "--facts",
                str(_facts(tmp_path)),
                "--authorized",
            ]
        )
        == 2
    )
    err = capsys.readouterr().err
    assert "not enabled in Phase 1A" in err


def test_cli_preflight_prints_non_mutating_plan(capsys) -> None:
    exit_code = lifecycle_cli_main(["preflight", "--kubeconfig", DEDICATED_KUBECONFIG])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["mutating"] is False
    assert payload["cluster"] == CLUSTER_NAME
