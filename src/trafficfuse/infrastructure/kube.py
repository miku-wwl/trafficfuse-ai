"""Trusted, narrowly scoped kubectl gateway for the TrafficFuse product.

This is **not** a general Kubernetes client or shell. Every command is built by
a function in this module and re-checked by :func:`validate_product_command`
before dispatch. All commands carry the exact repository-owned kubeconfig, the
exact context ``kind-trafficfuse-lab`` and the namespace ``trafficfuse-system``;
the ambient context is never consulted. Allowed shapes:

Reads (no side effects)
    get service trafficfuse-live -o json
    get endpointslices -l kubernetes.io/service-name=trafficfuse-live -o json
    get deployments -l trafficfuse.io/owner=trafficfuse-lab -o json
    get pods -l app=trafficfuse -o json
    get job <tf-probe-name> -o json
    logs job/<tf-probe-name>

Bounded lab mutation (operator-approved evidence collection)
    create -f - -o name      stdin = a probe Job rendered here from the one
                             fixed template; HTTP GETs only, no token mount

Governed mutation (``RerouteExecutor`` only, after AIG claim)
    patch service trafficfuse-live --type=json -p <exact patch> -o json
          --field-manager=trafficfuse-reroute

The JSON patch carries ``test`` operations on the Service UID,
resourceVersion and complete selector, so the API server itself refuses a
stale or unexpected target atomically. Building that patch is only possible
through :class:`SelectorPatch`, whose fields are re-validated here.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from trafficfuse.lab.lifecycle import (
    EXPECTED_CONTEXT,
    MANAGED_BY_LABEL_KEY,
    NAMESPACE,
    OWNERSHIP_LABEL_KEY,
    OWNERSHIP_LABEL_VALUE,
    SafetyViolation,
    canonical_kubeconfig_path,
    validate_kubeconfig_path,
)

LIVE_SERVICE = "trafficfuse-live"
HEALTHY_SERVICE = "trafficfuse-healthy"
SELECTOR_APP = "trafficfuse"
ROUTABLE_VERSIONS = ("faulty", "healthy")
LAB_IMAGE = "trafficfuse-lab-service:phase1a"
FIELD_MANAGER = "trafficfuse-reroute"

READ_TIMEOUT = "--request-timeout=5s"
PATCH_TIMEOUT = "--request-timeout=3s"

# Probe scenarios: the only Job shapes the product may create.
PROBE_SCENARIOS: Mapping[str, str] = {
    "baseline": HEALTHY_SERVICE,  # known-good version Service, direct
    "live": LIVE_SERVICE,  # customer-facing Service (incident detection)
    "verify": LIVE_SERVICE,  # same live Service, after an intervention
}
PROBE_REQUESTS = 40
PROBE_CONCURRENCY = 4
PROBE_TIMEOUT_SECONDS = 5
_PROBE_JOB_NAME = re.compile(r"^tf-probe-(baseline|live|verify)-[0-9a-f]{8}$")
_UID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RESOURCE_VERSION = re.compile(r"^[0-9]{1,20}$")

GET_SERVICE = ("get", "service", LIVE_SERVICE, "-o", "json")
GET_ENDPOINTS = (
    "get", "endpointslices", "-l", f"kubernetes.io/service-name={LIVE_SERVICE}", "-o", "json",
)
GET_DEPLOYMENTS = (
    "get", "deployments", "-l", f"{OWNERSHIP_LABEL_KEY}={OWNERSHIP_LABEL_VALUE}", "-o", "json",
)
GET_PODS = ("get", "pods", "-l", f"app={SELECTOR_APP}", "-o", "json")
CREATE_STDIN = ("create", "-f", "-", "-o", "name")


@dataclass(frozen=True)
class KubeResult:
    argv: tuple[str, ...]
    returncode: int | None  # None: timed out / never completed
    stdout: bytes
    stderr: bytes
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def json(self) -> Any:
        return json.loads(self.stdout.decode("utf-8"))


def _prefix(timeout_flag: str) -> list[str]:
    return [
        "kubectl",
        "--kubeconfig",
        str(canonical_kubeconfig_path()),
        "--context",
        EXPECTED_CONTEXT,
        timeout_flag,
        "-n",
        NAMESPACE,
    ]


def probe_job_name(scenario: str) -> str:
    if scenario not in PROBE_SCENARIOS:
        raise SafetyViolation(f"unknown probe scenario {scenario!r}")
    return f"tf-probe-{scenario}-{secrets.token_hex(4)}"


def render_probe_job(scenario: str, job_name: str) -> dict[str, Any]:
    """The one fixed probe template (mirrors deploy/lab/probe-job.yaml)."""
    if scenario not in PROBE_SCENARIOS or not _PROBE_JOB_NAME.fullmatch(job_name):
        raise SafetyViolation("probe Job scenario/name outside the fixed template")
    if not job_name.startswith(f"tf-probe-{scenario}-"):
        raise SafetyViolation("probe Job name does not match its scenario")
    service = PROBE_SCENARIOS[scenario]
    labels = {
        "app.kubernetes.io/name": "trafficfuse",
        "app.kubernetes.io/part-of": "trafficfuse",
        MANAGED_BY_LABEL_KEY: OWNERSHIP_LABEL_VALUE,
        OWNERSHIP_LABEL_KEY: OWNERSHIP_LABEL_VALUE,
        "trafficfuse.io/role": "probe",
        "trafficfuse.io/scenario": scenario,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": NAMESPACE,
            "labels": labels,
            "annotations": {"trafficfuse.io/manifest-source": "trafficfuse.infrastructure.kube"},
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "trafficfuse-probe",
                        OWNERSHIP_LABEL_KEY: OWNERSHIP_LABEL_VALUE,
                        "trafficfuse.io/role": "probe",
                    }
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "probe",
                            "image": LAB_IMAGE,
                            "imagePullPolicy": "Never",
                            "command": ["python", "-m", "trafficfuse.lab.probe_cli"],
                            "args": [
                                "--url",
                                f"http://{service}.{NAMESPACE}.svc.cluster.local",
                                "--path",
                                "/checkout",
                                "--scenario-id",
                                job_name,
                                "--target-label",
                                f"service:{service}",
                                "--requests",
                                str(PROBE_REQUESTS),
                                "--concurrency",
                                str(PROBE_CONCURRENCY),
                                "--timeout",
                                str(PROBE_TIMEOUT_SECONDS),
                            ],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "128Mi"},
                            },
                        }
                    ],
                },
            },
        },
    }


@dataclass(frozen=True)
class SelectorPatch:
    """The single governed change: one live-Service selector version swap."""

    service_uid: str
    expected_resource_version: str
    from_version: str
    to_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.service_uid, str)
            or not _UID.fullmatch(self.service_uid)
            or not isinstance(self.expected_resource_version, str)
            or not _RESOURCE_VERSION.fullmatch(self.expected_resource_version)
            or self.from_version not in ROUTABLE_VERSIONS
            or self.to_version != "healthy"
            or self.from_version == self.to_version
        ):
            raise SafetyViolation("selector patch outside the reviewed reroute contract")

    def operations(self) -> list[dict[str, Any]]:
        return [
            {"op": "test", "path": "/metadata/uid", "value": self.service_uid},
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": self.expected_resource_version,
            },
            {
                "op": "test",
                "path": "/spec/selector",
                "value": {"app": SELECTOR_APP, "version": self.from_version},
            },
            {
                "op": "replace",
                "path": "/spec/selector",
                "value": {"app": SELECTOR_APP, "version": self.to_version},
            },
        ]

    def argument(self) -> str:
        return json.dumps(self.operations(), separators=(",", ":"), sort_keys=True)


def read_command(shape: Sequence[str]) -> list[str]:
    command = _prefix(READ_TIMEOUT) + list(shape)
    validate_product_command(command)
    return command


def job_status_command(job_name: str) -> list[str]:
    return read_command(("get", "job", job_name, "-o", "json"))


def job_logs_command(job_name: str) -> list[str]:
    return read_command(("logs", f"job/{job_name}"))


def create_probe_command() -> list[str]:
    command = _prefix(READ_TIMEOUT) + list(CREATE_STDIN)
    validate_product_command(command)
    return command


def patch_command(patch: SelectorPatch) -> list[str]:
    command = _prefix(PATCH_TIMEOUT) + [
        "patch",
        "service",
        LIVE_SERVICE,
        "--type=json",
        "-p",
        patch.argument(),
        "-o",
        "json",
        f"--field-manager={FIELD_MANAGER}",
    ]
    validate_product_command(command)
    return command


def _patch_argument_is_reviewed(argument: str) -> bool:
    try:
        operations = json.loads(argument)
        uid = operations[0]["value"]
        version = operations[1]["value"]
        from_version = operations[2]["value"]["version"]
        to_version = operations[3]["value"]["version"]
        return argument == SelectorPatch(uid, version, from_version, to_version).argument()
    except (ValueError, TypeError, KeyError, IndexError, SafetyViolation):
        return False


def validate_product_command(argv: Sequence[str]) -> None:
    """Exact-shape gate; anything else is refused without echoing arguments."""
    command = tuple(argv)
    if any(not isinstance(arg, str) or any(c in arg for c in "\0\r\n") for arg in command):
        raise SafetyViolation("kubectl gateway refused malformed arguments")
    if len(command) < 8 or command[0] != "kubectl" or command[1] != "--kubeconfig":
        raise SafetyViolation("kubectl gateway refused unsupported command shape")
    try:
        validate_kubeconfig_path(command[2])
    except SafetyViolation:
        raise SafetyViolation("kubectl gateway requires the dedicated kubeconfig") from None
    if command[3:5] != ("--context", EXPECTED_CONTEXT) or command[6:8] != ("-n", NAMESPACE):
        raise SafetyViolation("kubectl gateway requires the exact context and namespace")
    flag, rest = command[5], command[8:]
    if flag == READ_TIMEOUT:
        if rest in (GET_SERVICE, GET_ENDPOINTS, GET_DEPLOYMENTS, GET_PODS, CREATE_STDIN):
            return
        if (
            len(rest) == 5
            and rest[:2] == ("get", "job")
            and _PROBE_JOB_NAME.fullmatch(rest[2])
            and rest[3:] == ("-o", "json")
        ):
            return
        if (
            len(rest) == 2
            and rest[0] == "logs"
            and rest[1].startswith("job/")
            and _PROBE_JOB_NAME.fullmatch(rest[1][4:])
        ):
            return
    if flag == PATCH_TIMEOUT and len(rest) == 9:
        if (
            rest[:4] == ("patch", "service", LIVE_SERVICE, "--type=json")
            and rest[4] == "-p"
            and _patch_argument_is_reviewed(rest[5])
            and rest[6:] == ("-o", "json", f"--field-manager={FIELD_MANAGER}")
        ):
            return
    raise SafetyViolation("kubectl gateway refused unsupported command shape")


async def run_kubectl(
    command: Sequence[str], *, timeout: float, stdin: bytes | None = None
) -> KubeResult:
    """Dispatch one validated command without a shell; never retries.

    A worker thread drives ``Popen.communicate`` so this works on every event
    loop (uvicorn selects a Selector loop on Windows under ``--reload``, where
    asyncio subprocesses are unsupported). On timeout *or* cancellation the
    child is killed. Callers must treat an unfinished *mutation* as an
    ambiguous outcome: a killed request may still have reached the API server.
    """
    validate_product_command(command)
    argv = tuple(command)
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    try:
        stdout, stderr = await asyncio.to_thread(process.communicate, stdin, timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.communicate)
        return KubeResult(argv, None, b"", b"", timed_out=True)
    except BaseException:
        process.kill()  # cancellation: never leave a detached mutation running
        raise
    return KubeResult(argv, process.returncode, stdout, stderr)


def redacted_error(result: KubeResult) -> str:
    """Short operator-facing error text; credential-shaped data stripped."""
    from trafficfuse.lab.cluster_facts import redact_for_message

    if result.timed_out:
        return "kubectl timed out"
    return redact_for_message(result.stderr.decode("utf-8", "replace")) or (
        f"kubectl exited {result.returncode}"
    )
