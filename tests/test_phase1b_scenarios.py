"""Offline dual-scenario exercise: real probe code, synthetic ASGI transports.

No network, Docker or Kubernetes execution. These measurements are test data,
never Phase 1B deployment evidence.
"""

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest
import yaml

from trafficfuse.lab.evidence import validate_evidence_document
from trafficfuse.lab.evidence_export import collect_evidence_from_log_bytes
from trafficfuse.lab.probe_cli import main as probe_main
from trafficfuse.lab.service import create_lab_app
from test_phase1b_runbook import RUNBOOK, _pwsh

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ["probe-healthy-job.yaml", "probe-job.yaml"]


def jobs():
    return [yaml.safe_load((ROOT / "deploy/lab" / name).read_text(encoding="utf-8")) for name in MANIFESTS]


def test_probe_scenarios_have_distinct_identities_and_read_only_workloads():
    healthy, live = jobs()
    identities = []
    for job, version in zip((healthy, live), ("healthy", "live")):
        pod = job["spec"]["template"]["spec"]
        container, = pod["containers"]
        args = dict(zip(container["args"][::2], container["args"][1::2]))
        identities.append((job["metadata"]["name"], args["--scenario-id"], args["--output"]))
        assert args["--url"] == f"http://trafficfuse-{version}.trafficfuse-system.svc.cluster.local"
        assert args["--scenario-id"] == f"phase1b-{version}"
        assert args["--target-label"] == f"service:trafficfuse-{version}"
        assert args["--path"] == "/checkout"
        assert container["command"] == ["python", "-m", "trafficfuse.lab.probe_cli"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["restartPolicy"] == "Never" and job["spec"]["backoffLimit"] == 0
    assert all(a != b for a, b in zip(*identities))
    kustomization = yaml.safe_load((ROOT / "deploy/lab/kustomization.yaml").read_text())
    assert not set(MANIFESTS) & set(kustomization["resources"])


@pytest.fixture
def scenario_exports(tmp_path, capsys):
    exports = []
    for job, mode, version in zip(jobs(), ("healthy", "faulty"), ("healthy", "live")):
        args = list(job["spec"]["template"]["spec"]["containers"][0]["args"])
        args[args.index("--output") + 1] = str(tmp_path / f"pod-{version}.json")
        # Deliberately different from the manifest's configured 50% policy:
        # the evidence must follow measured responses, not a scenario name.
        app = create_lab_app(mode=mode, fault_rate_percent=35)
        assert probe_main(args, transport=httpx.ASGITransport(app=app)) == 0
        raw = capsys.readouterr().out.encode()
        exports.append(collect_evidence_from_log_bytes(raw, run_id=f"offline-{version}", artifacts_root=tmp_path / "artifacts/phase1b"))
    return exports


def test_both_scenarios_validate_and_preserve_actual_measurements(scenario_exports):
    for exported in scenario_exports:
        document = exported.document
        assert validate_evidence_document(document).valid
        records = document["records"]
        counts = Counter(record["status_code"] for record in records)
        assert document["total_requests"] == len(records) == sum(counts.values()) == 40
        assert document["error_count"] == sum(record["outcome"] != "success" for record in records)
        assert document["observed_error_percent"] == round(100 * document["error_count"] / len(records), 2)
        assert document["latency_ms"]["min"] == min(record["duration_ms"] for record in records)
        assert document["latency_ms"]["max"] == max(record["duration_ms"] for record in records)
        assert document["synthetic_workload"] is True
        assert "fault_rate_percent" not in document
        audit = json.loads(exported.audit_path.read_text())
        assert audit["status"] == "ok"
        assert audit["raw_logs"]["sha256"] == hashlib.sha256(exported.raw_logs_path.read_bytes()).hexdigest()
        assert audit["evidence"]["sha256"] == hashlib.sha256(exported.evidence_path.read_bytes()).hexdigest()
    healthy, live = scenario_exports
    assert healthy.run_directory != live.run_directory
    assert healthy.document["error_count"] == 0
    assert live.document["observed_error_percent"] == 35.0


def test_healthy_named_probe_does_not_fabricate_success(tmp_path, capsys):
    args = list(jobs()[0]["spec"]["template"]["spec"]["containers"][0]["args"])
    args[args.index("--output") + 1] = str(tmp_path / "unexpected.json")
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"test": "unexpected failure"}))
    assert probe_main(args, transport=transport) == 0
    exported = collect_evidence_from_log_bytes(capsys.readouterr().out.encode(), run_id="unexpected-healthy", artifacts_root=tmp_path)
    assert exported.document["observed_error_percent"] == 100.0
    assert {record["status_code"] for record in exported.document["records"]} == {503}


@pytest.mark.parametrize("tamper", [None, "evidence", "raw", "identity"])
def test_documented_powershell_summary_uses_both_exports_and_checks_integrity(tmp_path, scenario_exports, tamper):
    healthy, live = scenario_exports
    if tamper in ("evidence", "raw"):
        path = live.evidence_path if tamper == "evidence" else live.raw_logs_path
        path.write_bytes(path.read_bytes() + b"\n")
    elif tamper == "identity":
        # Rebind even a valid audited healthy document to the live path:
        # hash checks alone must not allow swapped scenarios.
        for attr in ("evidence_path", "raw_logs_path", "audit_path"):
            getattr(live, attr).write_bytes(getattr(healthy, attr).read_bytes())
    text = RUNBOOK.read_text(encoding="utf-8")
    summary = re.search(r"# BEGIN EVIDENCE SUMMARY\n(.*?)# END EVIDENCE SUMMARY", text, re.DOTALL).group(1)
    capture = tmp_path / "captures"
    capture.mkdir()
    result = _pwsh(tmp_path, "$ErrorActionPreference = 'Stop'\n$HealthyRunId = 'offline-healthy'\n$LiveRunId = 'offline-live'\n$CaptureRoot = 'captures'\n" + summary)
    report = capture / "phase1b-evidence-report.json"
    if tamper:
        assert result.returncode != 0
        assert not report.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        entries = json.loads(report.read_text(encoding="utf-8"))
        assert [entry["scenario_id"] for entry in entries] == ["phase1b-healthy", "phase1b-live"]
        for entry, exported in zip(entries, scenario_exports):
            assert entry["observed_error_percent"] == exported.document["observed_error_percent"]
            assert entry["latency_ms"] == exported.document["latency_ms"]
            assert entry["status_code_distribution"] == dict(Counter(str(r["status_code"]) for r in exported.document["records"]))


def test_powershell_native_redirection_preserves_bytes_offline(tmp_path):
    # Test the documented 7.4+ native redirection semantics with a harmless
    # Python byte fixture. No runbook infrastructure commands are executed.
    interpreter = str(Path(sys.executable)).replace("'", "''")
    payload = b"synthetic native logs\r\n\x00\xff\n"
    code = f"import sys;sys.stdout.buffer.write(bytes({list(payload)}))"
    script = f"& '{interpreter}' -c '{code}' > capture.log\nif ($LASTEXITCODE -ne 0) {{ exit 1 }}"
    result = _pwsh(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "capture.log").read_bytes() == payload
