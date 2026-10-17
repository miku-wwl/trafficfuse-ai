"""Read-only live-facts collection for the trafficfuse-lab cluster.

Phase 1A.2 (review finding P1): caller-supplied facts (a hand-written
``cluster-facts.json``) can never be the sole proof of cluster ownership. This
module implements the *trusted* side of the ownership trust model — a narrow,
read-only collector whose facts are obtained directly from the target
environment through explicitly scoped commands:

1. every command carries the dedicated kubeconfig and the exact context —
   never the ambient selection;
2. every command is checked against a read-only verb allowlist **before** the
   executor runs (a mutating verb is refused even when an executor would run
   it);
3. any command failure, missing field or inconsistent observation fails
   closed — partial facts are never returned;
4. the collector never creates, repairs, recreates or migrates identity
   evidence, and never infers ownership from a cluster name.

This module **does not contact any cluster by itself**: the default executor
uses ``subprocess`` and is only invoked by operator-run Phase 1B commands;
tests inject mocked executors. Cluster creation and deletion remain disabled
and live in ``lifecycle.py``.

Trust model separation (all four stay distinct):

1. creation-time identity recording  — ``lifecycle`` creation record (v2);
2. read-only live-facts collection   — this module;
3. identity verification             — ``lifecycle.verify_ownership_evidence``;
4. destructive-operation authorization — ``lifecycle.plan_cluster_destroy``,
   which requires *collector-sourced* facts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from trafficfuse.lab.lifecycle import (
    EXPECTED_CONTEXT,
    MANAGED_BY_LABEL_KEY,
    NAMESPACE,
    OWNERSHIP_LABEL_KEY,
    CALLER_SOURCE,
    COLLECTOR_SOURCE,
    ClusterFacts,
    KubeconfigIdentity,
    OwnershipVerification,
    SafetyViolation,
    load_kubeconfig_document,
    load_ownership_receipt,
    validate_kubeconfig_path,
    verify_kubeconfig_document,
    verify_ownership_evidence,
)

MAX_COMMAND_TIMEOUT_SECONDS = 10.0

# Read-only verbs the collector may request. Anything else — create, apply,
# delete, patch, edit, scale, rollout, drain, taint, label, annotate,
# replace, exec, cp, debug, ... — is refused before execution.
_READ_ONLY_KUBECTL_VERBS = frozenset(
    {"get", "describe", "config", "version", "cluster-info", "api-resources", "api-versions"}
)
_READ_ONLY_DOCKER_VERBS = frozenset({"inspect", "version", "ps"})
_ALLOWED_BINARIES = {"kubectl", "docker"}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def text(self) -> str:
        """Strict UTF-8 decode of stdout; invalid bytes are a hard error."""
        try:
            return self.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SafetyViolation(
                f"command {' '.join(self.argv)} produced non-UTF-8 stdout: {exc}"
            ) from exc


CommandExecutor = Callable[[Sequence[str]], CommandResult]


# Flags that consume the following token as their value. The verb scanner must
# skip these values, otherwise a value like the kubeconfig path would be
# mistaken for the command verb. Boolean flags (e.g. --raw, --minify) need no
# entry: they are simply ignored while scanning for the verb.
_VALUE_FLAGS = frozenset(
    {
        "--kubeconfig",
        "--context",
        "--cluster",
        "--user",
        "--namespace",
        "-n",
        "-o",
        "--output",
        "-f",
        "--filename",
        "-l",
        "--selector",
        "--field-selector",
        "--format",
        "--type",
    }
)


def _command_verb(argv: Sequence[str]) -> str | None:
    """Return the command verb, or None when no positional verb exists.

    Walks the tokens after the binary; a token that is the value of a
    value-taking flag is skipped so it can never be mistaken for the verb.
    """
    skip_next = False
    for token in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if "=" not in token and token in _VALUE_FLAGS:
                skip_next = True
            continue
        return token
    return None


def assert_read_only_command(argv: Sequence[str]) -> None:
    """Refuse anything that is not an explicitly read-only inspection command."""
    if not argv:
        raise SafetyViolation("empty command refused by the read-only collector")
    binary = Path(argv[0]).name
    if binary not in _ALLOWED_BINARIES:
        raise SafetyViolation(
            f"read-only collector only runs {sorted(_ALLOWED_BINARIES)} commands, "
            f"got {argv[0]!r}"
        )
    verb = _command_verb(argv)
    if verb is None:
        raise SafetyViolation(f"cannot determine the verb of command {' '.join(argv)}")
    allowed = (
        _READ_ONLY_KUBECTL_VERBS if binary == "kubectl" else _READ_ONLY_DOCKER_VERBS
    )
    if verb not in allowed:
        raise SafetyViolation(
            f"refusing non-read-only {binary} command: {' '.join(argv)} "
            f"(allowed verbs: {', '.join(sorted(allowed))})"
        )


def subprocess_executor(argv: Sequence[str]) -> CommandResult:
    """Default executor: runs the verified read-only command, capturing bytes."""
    assert_read_only_command(argv)
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        timeout=MAX_COMMAND_TIMEOUT_SECONDS,
    )
    return CommandResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class LiveFacts:
    """Trusted live cluster facts, collected from the target environment."""

    api_server: str
    ca_fingerprint_sha256: str
    node_names: tuple[str, ...]
    namespace_labels: Mapping[str, str] = field(default_factory=dict)
    docker_node_identities: tuple[str, ...] = ()
    source: str = COLLECTOR_SOURCE

    def as_cluster_facts(self) -> ClusterFacts:
        # Provenance must survive the conversion: collector-collected facts stay
        # collector-sourced, so destructive authorization can trust them while
        # caller-supplied JSON still cannot.
        return ClusterFacts(
            api_server=self.api_server,
            ca_fingerprint_sha256=self.ca_fingerprint_sha256,
            node_names=self.node_names,
            namespace_labels=self.namespace_labels,
            source=self.source,
        )


def _kubectl(kubeconfig: str, context: str, *args: str) -> list[str]:
    return ["kubectl", "--kubeconfig", kubeconfig, "--context", context, *args]


def _require_ok(result: CommandResult, what: str) -> str:
    if not result.ok:
        raise SafetyViolation(
            f"live facts collection failed ({what}): command "
            f"'{' '.join(result.argv)}' exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:400]}"
        )
    return result.text().strip()


def collect_live_facts(
    *,
    kubeconfig_path: Path | str,
    record_path: Path | str,
    executor: CommandExecutor = subprocess_executor,
) -> LiveFacts:
    """Collect trusted live facts with explicitly scoped read-only commands.

    Every command is allowlisted (read-only) *before* the executor is invoked,
    carries the dedicated kubeconfig and exact context, and any failure or
    missing field fails closed. The creation record supplies the expected
    node identities to cross-check against Docker.
    """
    kubeconfig = validate_kubeconfig_path(kubeconfig_path)
    record = load_ownership_receipt(record_path)
    kubeconfig_arg = str(kubeconfig)

    view_argv = _kubectl(
        kubeconfig_arg,
        EXPECTED_CONTEXT,
        "config",
        "view",
        "--raw",
        "--minify",
        "-o",
        "json",
    )
    assert_read_only_command(view_argv)
    view_document = json.loads(_require_ok(executor(view_argv), "cluster identity"))
    try:
        cluster_entry = view_document["clusters"][0]["cluster"]
        api_server = cluster_entry["server"]
        ca_data = cluster_entry["certificate-authority-data"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SafetyViolation(
            "live facts collection failed (cluster identity): the minified cluster "
            "view lacks server or certificate-authority-data"
        ) from exc
    if not isinstance(api_server, str) or not api_server:
        raise SafetyViolation(
            "live facts collection failed (api server): server is not a non-empty string"
        )
    if not isinstance(ca_data, str) or not ca_data:
        raise SafetyViolation(
            "live facts collection failed (CA): certificate-authority-data is empty"
        )
    try:
        ca_fingerprint = hashlib.sha256(base64.b64decode(ca_data, validate=True)).hexdigest()
    except Exception as exc:  # binascii.Error and friends
        raise SafetyViolation(
            f"live facts collection failed (CA): invalid base64 CA data: {exc}"
        ) from exc

    nodes_argv = _kubectl(kubeconfig_arg, EXPECTED_CONTEXT, "get", "nodes", "-o", "json")
    assert_read_only_command(nodes_argv)
    nodes_document = json.loads(_require_ok(executor(nodes_argv), "kind nodes"))
    items = nodes_document.get("items") if isinstance(nodes_document, dict) else None
    if not isinstance(items, list) or not items:
        raise SafetyViolation(
            "live facts collection failed (kind nodes): no nodes reported for the cluster"
        )
    node_names = tuple(
        item["metadata"]["name"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("metadata", {}).get("name"), str)
    )
    if len(node_names) != len(items):
        raise SafetyViolation(
            "live facts collection failed (kind nodes): every node must expose a name"
        )

    namespace_argv = _kubectl(kubeconfig_arg, EXPECTED_CONTEXT, "get", "namespace", NAMESPACE, "-o", "json")
    assert_read_only_command(namespace_argv)
    namespace_document = json.loads(_require_ok(executor(namespace_argv), "namespace ownership"))
    labels = {}
    if isinstance(namespace_document, dict):
        labels = namespace_document.get("metadata", {}).get("labels", {}) or {}
    if not isinstance(labels, dict):
        raise SafetyViolation(
            "live facts collection failed (namespace ownership): labels are not an object"
        )

    docker_identities: list[str] = []
    for node_name in record["kind_node_names"]:
        docker_argv = [
            "docker",
            "inspect",
            "--format",
            "{{.Name}}",
            "--type",
            "container",
            node_name,
        ]
        assert_read_only_command(docker_argv)
        identity = _require_ok(executor(docker_argv), f"docker node identity for {node_name}")
        if not identity:
            raise SafetyViolation(
                f"live facts collection failed (docker node identity): empty identity for {node_name}"
            )
        docker_identities.append(identity)

    return LiveFacts(
        api_server=api_server,
        ca_fingerprint_sha256=ca_fingerprint,
        node_names=node_names,
        namespace_labels=labels,
        docker_node_identities=tuple(docker_identities),
    )


def verify_live_ownership(
    *,
    kubeconfig_path: Path | str,
    record_path: Path | str,
    executor: CommandExecutor = subprocess_executor,
) -> tuple[KubeconfigIdentity, OwnershipVerification]:
    """Full trusted ownership pipeline: kubeconfig document, creation record,
    collector-sourced live facts, and evidence cross-check. No mutation, no
    cluster contact beyond the read-only commands the collector allows."""
    document = load_kubeconfig_document(kubeconfig_path)
    identity = verify_kubeconfig_document(document)
    record = load_ownership_receipt(record_path)
    if identity.api_server != str(record.get("api_server")):
        raise SafetyViolation(
            "kubeconfig API server does not match the creation record; "
            "refusing to operate on a mismatched cluster identity"
        )
    if identity.ca_fingerprint_sha256 != str(record.get("ca_fingerprint_sha256")):
        raise SafetyViolation(
            "kubeconfig CA fingerprint does not match the creation record; "
            "refusing to operate on a mismatched cluster identity"
        )
    facts = collect_live_facts(
        kubeconfig_path=kubeconfig_path, record_path=record_path, executor=executor
    )
    verification = verify_ownership_evidence(record, facts.as_cluster_facts())
    if not verification.verified:
        raise SafetyViolation(
            "cluster ownership could not be verified: " + "; ".join(verification.failures)
        )
    if OWNERSHIP_LABEL_KEY not in facts.namespace_labels:
        raise SafetyViolation("namespace ownership labels absent from live facts")
    if MANAGED_BY_LABEL_KEY not in facts.namespace_labels:
        raise SafetyViolation("managed-by label absent from live facts")
    expected_docker = {name.lstrip("/") for name in facts.docker_node_identities}
    if expected_docker != set(record["kind_node_names"]):
        raise SafetyViolation(
            "docker node identities do not match the creation record exactly: "
            f"observed {sorted(expected_docker)}, expected {sorted(record['kind_node_names'])}"
        )
    return identity, verification
