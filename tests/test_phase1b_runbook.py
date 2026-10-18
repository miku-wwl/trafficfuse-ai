"""Static Phase 1B runbook safety tests (Phase 1B entry review, findings P1-a/P1-b).

These tests parse ``docs/PHASE1B-RUNBOOK.md`` — the executable operator
sequence — and enforce, without running anything:

* no unguarded cluster deletion anywhere in the Phase 1B sequence;
* no raw kubeconfig view (credential disclosure) in any command;
* namespace bootstrap strictly before namespace-dependent ownership
  verification (no circular dependency with cluster creation);
* image load strictly after cluster creation and before workload readiness;
* the probe Job is created, waited for, logged and only then exported;
* every kubectl command pins the dedicated kubeconfig and the exact context;
* every kind command targets exactly ``trafficfuse-lab``;
* explicitly selected manifests per stage (never the whole kustomization,
  which would create Deployments before ownership verification);
* STOP conditions after critical stages and a preservation-only ending.
"""

import re
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RUNBOOK = Path(__file__).resolve().parents[1] / "docs" / "PHASE1B-RUNBOOK.md"

FORBIDDEN_ANYWHERE = (
    "kind delete cluster",
    "kind delete ",
    "kubectl delete",
    "config view",
    "--raw",
    "--minify",
    "client-key-data",
    "client-certificate-data",
    "apply -k deploy/lab",
    "kubectl create namespace",
)

REQUIRED_STAGE_LABELS = (
    "Read-only preflight",
    "Docker image build",
    "Cluster creation",
    "Namespace bootstrap",
    "Live ownership verification",
    "Application deployment",
    "healthy HTTP baseline",
    "faulty live-Service scenario",
    "Evidence export",
    "Preservation of resources for inspection",
)


def _command_lines() -> list[str]:
    """Fenced PowerShell command lines in document order (comments stripped)."""
    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"```powershell\n(.*?)```", text, re.DOTALL)
    lines: list[str] = []
    for block in blocks:
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
    return lines


def _first(pattern: str, lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if pattern in line:
            return index
    raise AssertionError(f"expected command containing {pattern!r} in the runbook")


def test_runbook_exists() -> None:
    assert RUNBOOK.is_file(), "docs/PHASE1B-RUNBOOK.md must exist"


def test_no_unguarded_cluster_deletion_in_the_sequence() -> None:
    # P1-b: a preceding verification log does not enforce ownership checks on
    # a later deletion command. Deletion is removed from the Phase 1B
    # authorization scope entirely: no destructive kind command appears.
    text = RUNBOOK.read_text(encoding="utf-8")
    for forbidden in ("kind delete cluster", "kind delete ", "kubectl delete"):
        assert forbidden not in text, f"forbidden destructive command in runbook: {forbidden}"
    lines = _command_lines()
    assert lines, "the runbook must contain an executable command sequence"
    assert not any(line.startswith("kind delete") for line in lines)


def test_no_credential_disclosure_commands() -> None:
    # P2-b: no raw kubeconfig view (client key material) and no credential
    # fields in any command; identity comes from the trusted local parser
    # via the redacted collector output.
    text = RUNBOOK.read_text(encoding="utf-8")
    for forbidden in ("config view", "--raw", "--minify", "client-key-data", "client-certificate-data"):
        assert forbidden not in text, f"credential-disclosing content in runbook: {forbidden}"


def test_no_whole_kustomization_apply() -> None:
    # Applying the entire deploy/lab configuration would create Deployments
    # and Services during namespace bootstrap, before ownership verification.
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "apply -k deploy/lab" not in text
    assert "kubectl create namespace" not in text


def test_namespace_bootstrap_precedes_namespace_dependent_verification() -> None:
    # P1-a: the trusted collector reads the namespace ownership labels, so
    # the namespace must exist before verification runs.
    lines = _command_lines()
    namespace_apply = _first("apply -f deploy/lab/namespace.yaml", lines)
    namespace_get = _first("get namespace trafficfuse-system", lines)
    verify_collect = _first("--collect", lines)
    assert namespace_apply < verify_collect
    assert namespace_get < verify_collect
    # Workloads must not exist before ownership verification, either.
    workload_apply = _first("apply -f deploy/lab/healthy-deployment.yaml", lines)
    assert verify_collect < workload_apply


def test_image_load_follows_cluster_creation_and_precedes_workloads() -> None:
    lines = _command_lines()
    create = _first("kind create cluster", lines)
    load = _first("kind load docker-image", lines)
    rollout = _first("rollout status deployment/trafficfuse-faulty", lines)
    assert create < load < rollout


def test_probe_job_lifecycle_order() -> None:
    # The Job is created before waiting, completed before evidence export,
    # and logs come from the exact expected Job.
    lines = _command_lines()
    for manifest, job, capture in (
        ("probe-healthy-job.yaml", "trafficfuse-probe-healthy", "healthy.log"),
        ("probe-job.yaml", "trafficfuse-probe", "live.log"),
    ):
        create_job = _first(f"create -f deploy/lab/{manifest}", lines)
        wait_job = _first(f"wait --for=condition=complete job/{job} --timeout", lines)
        logs = _first(f"logs job/{job} >", lines)
        export = _first(f'evidence_cli --logs "$CaptureRoot/{capture}"', lines)
        assert create_job < wait_job < logs < export


def test_every_kubectl_command_pins_kubeconfig_and_context() -> None:
    lines = _command_lines()
    definitions = [line for line in lines if line.startswith("$KC =")]
    assert len(definitions) >= 1, "the runbook must define the KC scoping variable"
    definition = definitions[0]
    assert "--kubeconfig" in definition
    assert "'--context', 'kind-trafficfuse-lab'" in definition
    assert ".runtime/kubeconfig/trafficfuse-lab.yaml" in definition
    kubectl_lines = [line for line in lines if re.search(r"(?:^|= )kubectl ", line)]
    assert kubectl_lines, "the runbook must contain kubectl commands"
    for line in kubectl_lines:
        assert "@KC" in line, f"kubectl command without explicit scoping: {line}"
        if "delete" in line or "patch" in line or "edit" in line:
            raise AssertionError(f"mutating kubectl command in Phase 1B: {line}")


def test_every_mutating_kind_command_targets_the_exact_cluster() -> None:
    lines = _command_lines()
    mutating = [
        line
        for line in lines
        if line.startswith("kind ") and ("create cluster" in line or "load docker-image" in line)
    ]
    assert mutating, "the runbook must contain kind create/load commands"
    for line in mutating:
        assert "--name trafficfuse-lab" in line, f"kind command without exact cluster: {line}"
    # Reads like `kind get clusters` need no target, but must never be
    # combined with a destructive subcommand.
    for line in lines:
        if line.startswith("kind "):
            assert " delete" not in line, f"destructive kind command: {line}"


def test_stage_labels_and_stop_conditions_present() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    for label in REQUIRED_STAGE_LABELS:
        assert label in text, f"missing required stage label: {label}"
    # A STOP condition after every critical stage (preflight, build, create,
    # bootstrap, verification, deployment, probe, export, preservation).
    assert len(re.findall(r"\*\*STOP\*\*", text)) >= 8, "each critical stage needs a STOP condition"
    assert "separate explicit human\nauthorization" in text
    assert "no automatic cleanup" in text.lower() or "No automatic cleanup" in text


def test_runbook_orders_preflight_before_any_mutation() -> None:
    lines = _command_lines()
    preflight = _first("lifecycle_cli preflight", lines)
    create = _first("kind create cluster", lines)
    build = _first("docker build", lines)
    assert preflight < create
    assert preflight < build


def test_full_fifteen_step_workflow_order_and_separate_scenarios():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert re.findall(r"^## (\d+)\.", text, re.MULTILINE) == [str(i) for i in range(1, 16)]
    lines = _command_lines()
    milestones = [
        "lifecycle_cli preflight", "docker build --pull", "kind create cluster",
        "lifecycle_cli record-creation", "kind load docker-image",
        "apply -f deploy/lab/namespace.yaml", "--collect",
        "apply -f deploy/lab/healthy-deployment.yaml", "rollout status",
        "get service trafficfuse-live", "create -f deploy/lab/probe-healthy-job.yaml",
        "create -f deploy/lab/probe-job.yaml", 'logs job/trafficfuse-probe-healthy >',
        'evidence_cli --logs "$CaptureRoot/healthy.log"',
        'evidence_cli --logs "$CaptureRoot/live.log"', "$Summaries = foreach",
    ]
    positions = [_first(milestone, lines) for milestone in milestones]
    assert positions == sorted(positions)
    assert '`receipt_schema` | `"trafficfuse-ownership-record-v2"`' in text
    assert "20 successes/20 errors" in text and "Do not require" in text


def _pwsh(tmp_path, script):
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell 7 required for shell execution checks")
    path = tmp_path / "offline-shell-check.ps1"
    path.write_text(script, encoding="utf-8")
    return subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=tmp_path)


def test_powershell_runbook_parses_offline(tmp_path):
    # Parse only: never invoke any command in the actual runbook.
    commands = "\n".join(re.findall(r"```powershell\n(.*?)```", RUNBOOK.read_text(encoding="utf-8"), re.DOTALL))
    source = tmp_path / "runbook-source.ps1"
    source.write_text(commands, encoding="utf-8")
    script = "$tokens = $null; $errors = $null\n"
    script += "[System.Management.Automation.Language.Parser]::ParseFile('" + str(source).replace("'", "''") + "', [ref]$tokens, [ref]$errors) | Out-Null\n"
    script += "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }\n"
    result = _pwsh(tmp_path, script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_powershell_kubectl_and_kind_argument_expansion_offline(tmp_path):
    # Execute ONLY extracted native call expressions against local stubs;
    # everything else in the runbook (uv, docker, mutations) is excluded.
    lines = _command_lines()
    definition = next(line for line in lines if line.startswith("$KC ="))
    script = definition + "\n"
    script += "function kubectl { ConvertTo-Json -Compress -InputObject @('kubectl', $args) -Depth 4 }\n"
    script += "function kind { ConvertTo-Json -Compress -InputObject @('kind', $args) -Depth 4 }\n"
    invocations = []
    for line in lines:
        match = re.search(r"(?:^|= )((?:kubectl|kind) .*)", line)
        if match:
            invocations.append(match.group(1).split(" > ")[0])
    script += "\n".join(invocations)
    result = _pwsh(tmp_path, script)
    assert result.returncode == 0, result.stderr
    observed = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(observed) == len(invocations)
    for binary, args in observed:
        if binary == "kubectl":
            assert args[:4] == ["--kubeconfig", ".runtime/kubeconfig/trafficfuse-lab.yaml", "--context", "kind-trafficfuse-lab"]
        elif args != ["get", "clusters"]:
            assert args[args.index("--name") + 1] == "trafficfuse-lab"
