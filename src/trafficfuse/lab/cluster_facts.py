"""Read-only live-facts collection for the trafficfuse-lab cluster.

Phase 1A.2 established the trusted side of the ownership model: a narrow,
read-only collector whose facts come from the target environment through
explicitly scoped commands. Phase 1B entry review found that the provenance
was still expressed as a caller-settable string (``source="collector"``): any
caller could construct matching facts and set that field, so the string was
not a provenance guarantee.

This module now establishes trust by **execution**, not declaration:

1. ``collect_live_facts()`` is the only code path that can produce
   ``LiveFacts``, and it mints a private ``CollectorExecution`` receipt
   (nonce + exact executed command trace) that caller-constructed objects
   cannot carry. ``is_collector_executed()`` fails closed when the receipt is
   absent — a forged or mismatched object is never trusted.
2. ``verify_live_ownership()`` is the single trusted entry point: it performs
   kubeconfig-document verification, creation-record verification, **the
   collection itself** and the evidence cross-check inside one controlled
   operation. It deliberately has **no facts parameter**: there is nothing a
   caller can pass in to be trusted.
3. Cluster identity (API server, CA fingerprint) is extracted from the
   dedicated kubeconfig **file** through the trusted local parser
   (``lifecycle.load_kubeconfig_document`` / ``verify_kubeconfig_document``).
   The previous ``kubectl config view --raw --minify`` step is removed: that
   command prints the complete kubeconfig — including client key material —
   to the terminal, which is credential disclosure. ``kubectl config view``
   also reads the local file, not the cluster, so live connectivity is proven
   by the scoped ``get nodes`` / ``get namespace`` commands instead.
4. Command output placed in operator-facing messages passes through
   ``redact_for_message()`` (credential-shaped material stripped before any
   message or exception text).

Fail-closed rules preserved from Phase 1A.2: every command is checked against
the read-only verb allowlist **before** execution, carries the dedicated
kubeconfig and exact context (never the ambient selection), and any failure,
missing field or inconsistent observation refuses the operation.

This module **does not contact any cluster by itself**: the default executor
uses ``subprocess`` and is only invoked by operator-run Phase 1B commands;
tests inject mocked executors. Cluster creation and deletion remain disabled
and live in ``lifecycle.py``.

Trust model separation (all four stay distinct):

1. creation-time identity recording  — ``lifecycle`` creation record (v2);
2. read-only live-facts collection   — this module (execution-receipt bound);
3. identity verification             — ``lifecycle.verify_ownership_evidence``;
4. destructive-operation authorization — ``lifecycle.plan_cluster_destroy``,
   which performs the trusted collection itself and accepts no caller facts.

Documented limitation (see docs/PHASE1B-ENTRY-REVIEW.md): the execution
receipt is fail-closed defense-in-depth, **not** cryptographic authenticity.
The real guarantees are the control-flow design (the destructive path
collects itself), the read-only allowlist and the record cross-check.
Complete trust guarantees require integration with a live Kind cluster.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from trafficfuse.lab.lifecycle import (
    EXPECTED_CONTEXT,
    MANAGED_BY_LABEL_KEY,
    NAMESPACE,
    OWNERSHIP_LABEL_KEY,
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


# --- credential redaction (Phase 1B entry review, finding P2-b) ----------------------


_PEM_BLOCK = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def redact_for_message(text: str) -> str:
    """Strip credential-shaped material from command output before it is
    placed in an operator-facing message, log or exception.

    Defense-in-depth: the collector's own API surface never returns raw
    kubeconfig content (identity comes from the trusted local parser, and
    only the four identity fields are ever printed), but a failing command's
    stderr is passed through here before it can reach a human. PEM blocks and
    long base64 runs (client keys, certificates, tokens) become ``[REDACTED]``
    and the result is truncated.
    """
    redacted = _PEM_BLOCK.sub("[REDACTED]", text)
    redacted = _BASE64_RUN.sub("[REDACTED]", redacted)
    return redacted.strip()[:400]


# --- trusted collector output ---------------------------------------------------------


@dataclass(frozen=True)
class CollectorExecution:
    """Private receipt proving ``LiveFacts`` were produced by an actual
    collector execution rather than by a caller constructing an object.

    Minted only inside ``collect_live_facts()``. This is fail-closed
    defense-in-depth and **not** cryptographic authenticity (see the module
    docstring limitation note); its purpose is that forged, mismatched or
    detached objects never satisfy a trust decision.
    """

    nonce: str
    commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class LiveFacts:
    """Trusted live cluster facts, collected from the target environment.

    Only ``collect_live_facts()`` can produce facts that carry a
    ``CollectorExecution`` receipt; directly constructed instances have
    ``execution=None`` and are never trusted anywhere.
    """

    api_server: str
    ca_fingerprint_sha256: str
    node_names: tuple[str, ...]
    namespace_labels: Mapping[str, str] = field(default_factory=dict)
    docker_node_identities: tuple[str, ...] = ()
    execution: CollectorExecution | None = None


def is_collector_executed(facts: object) -> bool:
    """True only for facts produced by an actual ``collect_live_facts`` run."""
    return isinstance(facts, LiveFacts) and facts.execution is not None


@dataclass(frozen=True)
class TrustedOwnership:
    """Outcome of the single trusted ownership verification operation.

    Contains only non-sensitive identity and verification data; it never
    carries kubeconfig credential material. ``commands`` is the exact
    read-only command trace the collector executed, so an operator can audit
    that collection actually happened (and only ever read).
    """

    identity: KubeconfigIdentity
    verification: OwnershipVerification
    facts: LiveFacts
    commands: tuple[tuple[str, ...], ...]

    @property
    def verified(self) -> bool:
        return self.verification.verified

    def to_dict(self) -> dict[str, Any]:
        """Redacted, minimal representation: identity + verification + the
        executed read-only command trace. No credential fields exist here."""
        return {
            "identity": {
                "context": self.identity.context,
                "cluster": self.identity.cluster,
                "api_server": self.identity.api_server,
                "ca_fingerprint_sha256": self.identity.ca_fingerprint_sha256,
            },
            "verification": self.verification.to_dict(),
            "live_facts": {
                "node_names": list(self.facts.node_names),
                "namespace_labels": dict(self.facts.namespace_labels),
                "docker_node_identities": list(self.facts.docker_node_identities),
            },
            "collector_commands": [list(command) for command in self.commands],
        }


def _kubectl(kubeconfig: str, context: str, *args: str) -> list[str]:
    return ["kubectl", "--kubeconfig", kubeconfig, "--context", context, *args]


def _require_ok(result: CommandResult, what: str) -> str:
    if not result.ok:
        raise SafetyViolation(
            f"live facts collection failed ({what}): command "
            f"'{' '.join(result.argv)}' exited {result.returncode}: "
            f"{redact_for_message(result.stderr.decode('utf-8', 'replace'))}"
        )
    return result.text().strip()


def collect_live_facts(
    *,
    kubeconfig_path: Path | str,
    record_path: Path | str,
    executor: CommandExecutor = subprocess_executor,
) -> LiveFacts:
    """Collect trusted live facts with explicitly scoped read-only commands.

    Identity (API server, CA fingerprint) is read from the dedicated
    kubeconfig **file** through the trusted local parser — the raw kubeconfig
    is never printed (credential-disclosure fix). Live connectivity and
    ownership evidence come from scoped read-only commands: ``get nodes``,
    ``get namespace <ns>`` and ``docker inspect`` per recorded node. Every
    command is allowlisted and context-pinned before the executor runs, and
    any failure or missing field fails closed. The returned facts carry a
    ``CollectorExecution`` receipt; direct construction cannot forge one.
    """
    kubeconfig = validate_kubeconfig_path(kubeconfig_path)
    record = load_ownership_receipt(record_path)
    document = load_kubeconfig_document(kubeconfig)
    identity = verify_kubeconfig_document(document)
    kubeconfig_arg = str(kubeconfig)
    commands: list[tuple[str, ...]] = []

    nodes_argv = _kubectl(kubeconfig_arg, EXPECTED_CONTEXT, "get", "nodes", "-o", "json")
    assert_read_only_command(nodes_argv)
    nodes_document = json.loads(_require_ok(executor(nodes_argv), "kind nodes"))
    commands.append(nodes_argv)
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

    namespace_argv = _kubectl(
        kubeconfig_arg, EXPECTED_CONTEXT, "get", "namespace", NAMESPACE, "-o", "json"
    )
    assert_read_only_command(namespace_argv)
    namespace_document = json.loads(_require_ok(executor(namespace_argv), "namespace ownership"))
    commands.append(namespace_argv)
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
        docker_identity = _require_ok(
            executor(docker_argv), f"docker node identity for {node_name}"
        )
        commands.append(docker_argv)
        if not docker_identity:
            raise SafetyViolation(
                f"live facts collection failed (docker node identity): empty identity for {node_name}"
            )
        docker_identities.append(docker_identity)

    return LiveFacts(
        api_server=identity.api_server,
        ca_fingerprint_sha256=identity.ca_fingerprint_sha256,
        node_names=node_names,
        namespace_labels=labels,
        docker_node_identities=tuple(docker_identities),
        execution=CollectorExecution(nonce=uuid.uuid4().hex, commands=tuple(commands)),
    )


def verify_live_ownership(
    *,
    kubeconfig_path: Path | str,
    record_path: Path | str,
    executor: CommandExecutor = subprocess_executor,
) -> TrustedOwnership:
    """The single trusted ownership pipeline — it collects, it does not receive.

    1. dedicated kubeconfig path, document and offline identity verification
       (exact context/cluster binding, local HTTPS API host, CA fingerprint
       anchor, no foreign contexts, no embedded credentials) — via the trusted
       local parser, never a command that prints the raw kubeconfig;
    2. creation-record verification and identity cross-check;
    3. **the collection itself**, through the read-only collector with an
       explicitly scoped command set (this function has no facts parameter:
       there is nothing a caller can pass in to be trusted);
    4. evidence cross-check against the record (API server, CA fingerprint,
       exact node identities, ownership labels, Docker node identity).

    Any missing, mismatched or unverifiable evidence fails closed. The result
    is a ``TrustedOwnership`` outcome whose facts carry the collector
    execution receipt.
    """
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
    verification = verify_ownership_evidence(record, facts)
    if not verification.verified:
        raise SafetyViolation(
            "cluster ownership could not be verified: " + "; ".join(verification.failures)
        )
    if not verification.trusted:
        raise SafetyViolation(
            "collector execution receipt missing; facts were not collected "
            "through the trusted collector execution path"
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
    return TrustedOwnership(
        identity=identity,
        verification=verification,
        facts=facts,
        commands=facts.execution.commands,
    )
