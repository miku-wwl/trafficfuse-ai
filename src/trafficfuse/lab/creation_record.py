"""Post-creation identity recording, before namespace bootstrap.

Operator-run only after an authorized, successful Kind creation. This command
performs scoped reads and writes local metadata; it does not establish historical
ownership or authorize any infrastructure action. Operators must not use it to
adopt a pre-existing cluster; the recorder cannot independently prove origin.
The separate verify --collect pipeline must still run after namespace bootstrap.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from trafficfuse.lab import lifecycle
from trafficfuse.lab.cluster_facts import (
    CommandExecutor,
    assert_read_only_command,
    redact_for_message,
    subprocess_executor,
)
from trafficfuse.lab.lifecycle import SafetyViolation


def record_cluster_creation(
    *,
    kubeconfig_path: Path | str,
    receipt_path: Path | str,
    authorized: bool,
    executor: CommandExecutor = subprocess_executor,
) -> dict:
    """Record observed identity, refusing missing facts and existing records.

    authorized is an operator attestation for this creation workflow, never
    proof of consent from the model. created_at is the control-plane Node's
    observed creationTimestamp, not a guessed cluster creation time.
    """
    lifecycle.validate_authorization("post-creation identity recording", authorized=authorized)
    kubeconfig = lifecycle.validate_kubeconfig_path(kubeconfig_path)
    receipt = Path(receipt_path).resolve()
    expected = lifecycle.PROJECT_ROOT.resolve() / lifecycle.RECEIPT_DIRECTORY / lifecycle.RECEIPT_FILENAME
    if receipt != expected or expected.resolve() != expected:
        raise SafetyViolation("creation record must use the canonical repository-owned state path")
    if receipt.exists():
        raise SafetyViolation("creation record already exists; refusing overwrite or cluster adoption")
    identity = lifecycle.verify_kubeconfig_document(lifecycle.load_kubeconfig_document(kubeconfig))

    def read_json(argv: list[str]) -> object:
        assert_read_only_command(argv)
        try:
            result = executor(argv)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SafetyViolation("post-creation identity command could not complete") from exc
        if not result.ok:
            raise SafetyViolation(
                "post-creation identity collection failed: "
                + redact_for_message(result.stderr.decode("utf-8", "replace"))
            )
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SafetyViolation("post-creation identity response is not valid UTF-8 JSON") from exc

    nodes = read_json([
        "kubectl", "--kubeconfig", str(kubeconfig), "--context", lifecycle.EXPECTED_CONTEXT,
        "get", "nodes", "-o", "json",
    ])
    try:
        items = nodes["items"]
        names = [item["metadata"]["name"] for item in items]
        if names != list(lifecycle.EXPECTED_NODE_NAMES):
            raise ValueError
        created_at = items[0]["metadata"]["creationTimestamp"]
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise SafetyViolation("post-creation nodes must be exactly the expected single control-plane with a creationTimestamp") from exc

    # Inspect only name + Kind labels, never the full Docker container config.
    node = names[0]
    # A single JSON object keeps the observation bounded and easy to validate.
    container = read_json([
        "docker", "inspect", "--format",
        '{"name":{{json .Name}},"cluster":{{json (index .Config.Labels "io.x-k8s.kind.cluster")}},'
        '"role":{{json (index .Config.Labels "io.x-k8s.kind.role")}}}',
        "--type", "container", node,
    ])
    if container != {"name": f"/{node}", "cluster": lifecycle.CLUSTER_NAME, "role": "control-plane"}:
        raise SafetyViolation("post-creation Docker name/Kind labels disagree with the expected cluster")

    record = lifecycle.validate_ownership_record({
        "receipt_schema": lifecycle.RECORD_SCHEMA,
        "cluster": lifecycle.CLUSTER_NAME,
        "context": lifecycle.EXPECTED_CONTEXT,
        "namespace": lifecycle.NAMESPACE,
        "api_server": identity.api_server,
        "ca_fingerprint_sha256": identity.ca_fingerprint_sha256,
        "kind_node_names": names,
        "created_at": created_at,
        "created_by": "trafficfuse",
        "kubeconfig": lifecycle.DEDICATED_KUBECONFIG,
    })
    receipt.parent.mkdir(parents=True, exist_ok=True)
    try:
        with receipt.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SafetyViolation("creation record already exists; refusing overwrite") from exc
    return lifecycle.load_ownership_receipt(receipt)
