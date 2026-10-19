"""Offline evidence-export tests (Phase 1A.1, review finding P1).

Covers: valid complete JSON on stdout; file/stdout equivalence; malformed and
truncated JSON; missing required fields; incorrect request totals; duplicate
evidence documents; missing log output; failed evidence export; and raw-log
preservation. No Kubernetes cluster access is used anywhere.
"""

import asyncio
import json

import httpx
import pytest

from trafficfuse.lab.evidence import (
    EVIDENCE_BEGIN_MARKER,
    EVIDENCE_END_MARKER,
    EvidenceFormatError,
    extract_evidence_document,
    find_evidence_documents,
    render_stdout_document,
    validate_evidence_document,
)
from trafficfuse.lab.evidence_cli import main as evidence_cli_main
from trafficfuse.lab.evidence_export import (
    EvidenceValidationError,
    collect_evidence_from_logs,
)
from trafficfuse.lab.probe import ProbeConfig, run_probe
from trafficfuse.lab.probe_cli import EXIT_INVALID_EVIDENCE, EXIT_OK, EXIT_OUTPUT_FAILED
from trafficfuse.lab.probe_cli import main as probe_cli_main
from trafficfuse.lab.service import MODE_FAULTY, create_lab_app


def _captured_evidence(rate: int = 50, requests: int = 20) -> str:
    """Produce real marked-up probe logs by probing the in-process app."""
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    config = ProbeConfig(
        target_url="http://trafficfuse-lab/checkout",
        scenario_id="export-selftest",
        target_label="service:trafficfuse-live",
        total_requests=requests,
        concurrency=4,
    )
    evidence = asyncio.run(run_probe(config, transport=httpx.ASGITransport(app=app)))
    return render_stdout_document(evidence.to_json())


def _valid_document() -> dict:
    return extract_evidence_document(_captured_evidence())


# --- valid stdout evidence -------------------------------------------------------


def test_probe_cli_stdout_yields_exactly_one_valid_document(capsys) -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    exit_code = probe_cli_main(
        [
            "--url",
            "http://trafficfuse-lab/checkout",
            "--scenario-id",
            "export-stdout",
            "--target-label",
            "service:trafficfuse-live",
            "--requests",
            "20",
        ],
        transport=httpx.ASGITransport(app=app),
    )
    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    out = captured.out
    assert out.count(EVIDENCE_BEGIN_MARKER) == 1
    assert out.count(EVIDENCE_END_MARKER) == 1
    document = extract_evidence_document(out)
    result = validate_evidence_document(document)
    assert result.valid, result.errors
    assert document["scenario_id"] == "export-stdout"
    assert document["total_requests"] == 20
    assert document["success_count"] + document["error_count"] == 20
    # Single ordered stream (live Kind finding, session phase1b-001): progress
    # precedes the document on stdout, the document is last, stderr is unused.
    before, _, rest = out.partition(EVIDENCE_BEGIN_MARKER)
    assert "probing" in before and "probe complete" in before
    assert rest.rstrip().endswith(EVIDENCE_END_MARKER)
    assert captured.err == ""
    assert json.loads(out.split(EVIDENCE_BEGIN_MARKER)[1].split(EVIDENCE_END_MARKER)[0])


def test_merged_stream_interleaving_is_refused_not_repaired() -> None:
    # Reproduces the live capture defect: a stderr line merged by `kubectl logs`
    # inside the document must fail extraction (raw bytes are preserved by the
    # exporter); the extractor never skips or repairs foreign lines.
    lines = _captured_evidence().splitlines()
    begin = lines.index(EVIDENCE_BEGIN_MARKER)
    lines.insert(begin + 5, "probe complete: 40 success / 0 error (observed 0.0%)")
    with pytest.raises(EvidenceFormatError, match="not valid JSON"):
        extract_evidence_document("\n".join(lines) + "\n")


def test_probe_cli_file_and_stdout_evidence_are_equivalent(tmp_path, capsys) -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=50)
    output = tmp_path / "nested" / "probe-live.json"
    exit_code = probe_cli_main(
        [
            "--url",
            "http://trafficfuse-lab/checkout",
            "--scenario-id",
            "export-equivalence",
            "--target-label",
            "service:trafficfuse-live",
            "--requests",
            "20",
            "--output",
            str(output),
        ],
        transport=httpx.ASGITransport(app=app),
    )
    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    file_text = output.read_text(encoding="utf-8")
    assert EVIDENCE_BEGIN_MARKER not in file_text  # file is pure JSON
    file_document = json.loads(file_text)
    assert validate_evidence_document(file_document).valid
    # Same run: the stdout document and the file document must be identical.
    stdout_document = extract_evidence_document(captured.out)
    assert stdout_document == file_document


def test_stdout_document_rejects_contaminated_logs() -> None:
    # Progress text before/after the markers must not break extraction.
    logs = "starting probe...\n" + _captured_evidence() + "done\n"
    assert validate_evidence_document(extract_evidence_document(logs)).valid


# --- malformed / truncated / missing ---------------------------------------------


def test_malformed_json_between_markers_refused() -> None:
    logs = f"{EVIDENCE_BEGIN_MARKER}\n{{not valid json\n{EVIDENCE_END_MARKER}\n"
    with pytest.raises(EvidenceFormatError, match="not valid JSON"):
        extract_evidence_document(logs)


def test_truncated_document_refused() -> None:
    payload = _captured_evidence().splitlines()
    logs = "\n".join(payload[:-1]) + "\n"  # drop the END marker
    with pytest.raises(EvidenceFormatError, match="truncated"):
        extract_evidence_document(logs)


def test_empty_and_markerless_logs_refused() -> None:
    with pytest.raises(EvidenceFormatError, match="empty"):
        extract_evidence_document("")
    with pytest.raises(EvidenceFormatError, match="no TRAFFICFUSE_EVIDENCE_BEGIN"):
        extract_evidence_document("some unrelated log line\nanother one\n")


def test_duplicate_evidence_documents_refused() -> None:
    block = _captured_evidence()
    logs = block + block  # exactly duplicated run output
    with pytest.raises(EvidenceFormatError, match="2 evidence documents"):
        extract_evidence_document(logs)
    # find_evidence_documents reports both for diagnostics.
    assert len(find_evidence_documents(logs)) == 2


def test_end_marker_without_begin_refused() -> None:
    logs = f"noise\n{EVIDENCE_END_MARKER}\n"
    with pytest.raises(EvidenceFormatError):
        extract_evidence_document(logs)


# --- strict validation -------------------------------------------------------------


def test_missing_required_fields_reported() -> None:
    document = _valid_document()
    del document["scenario_id"]
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("scenario_id" in error for error in result.errors)


def test_wrong_schema_version_reported() -> None:
    document = _valid_document()
    document["schema"] = "trafficfuse-probe-evidence-v0"
    assert not validate_evidence_document(document).valid


def test_incorrect_request_totals_reported() -> None:
    document = _valid_document()
    document["success_count"] += 1  # success + error != total
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("must equal total_requests" in error for error in result.errors)


def test_incorrect_records_count_reported() -> None:
    document = _valid_document()
    document["records"] = document["records"][:-1]
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("records length" in error for error in result.errors)


def test_duplicate_and_missing_sequences_reported() -> None:
    document = _valid_document()
    document["records"][1]["sequence"] = document["records"][0]["sequence"]
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("duplicate sequence" in error for error in result.errors)

    document = _valid_document()
    document["records"][0]["sequence"] = 999
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("cover every sequence" in error for error in result.errors)


def test_inconsistent_observed_rate_reported() -> None:
    document = _valid_document()
    document["observed_error_percent"] = 99.9
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("observed_error_percent" in error for error in result.errors)


def test_bad_outcome_status_and_timestamps_reported() -> None:
    document = _valid_document()
    document["records"][0]["outcome"] = "mystery"
    document["records"][0]["status_code"] = "200"
    document["completed_at_utc"] = "1999-01-01T00:00:00+00:00"
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("outcome" in error for error in result.errors)
    assert any("status_code" in error for error in result.errors)
    assert any("precede" in error for error in result.errors)


def test_missing_latency_and_declaration_reported() -> None:
    document = _valid_document()
    del document["latency_ms"]["p95"]
    del document["workload_declaration"]
    document["synthetic_workload"] = "yes"
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("latency_ms.p95" in error for error in result.errors)
    assert any("workload_declaration" in error for error in result.errors)
    assert any("synthetic_workload" in error for error in result.errors)


# --- export failures and raw preservation -------------------------------------------


def test_probe_cli_output_write_failure_is_hard_error(tmp_path, capsys) -> None:
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=100)
    exit_code = probe_cli_main(
        [
            "--url",
            "http://trafficfuse-lab/checkout",
            "--scenario-id",
            "export-failure",
            "--target-label",
            "service:trafficfuse-live",
            "--requests",
            "4",
            "--output",
            str(tmp_path),  # a directory: not a writable file path
        ],
        transport=httpx.ASGITransport(app=app),
    )
    captured = capsys.readouterr()
    assert exit_code == EXIT_OUTPUT_FAILED
    assert "export FAILED" in captured.err


def test_collect_stores_evidence_and_preserves_raw_logs(tmp_path) -> None:
    raw_logs = _captured_evidence()
    result = collect_evidence_from_logs(
        raw_logs, run_id="phase1b-live-001", artifacts_root=tmp_path / "artifacts"
    )
    stored = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert validate_evidence_document(stored).valid
    assert stored == json.loads(raw_logs.split(EVIDENCE_BEGIN_MARKER)[1].split(EVIDENCE_END_MARKER)[0])
    assert result.raw_logs_path.read_bytes() == raw_logs.encode("utf-8")
    # A second collection for the same run id must not overwrite evidence.
    with pytest.raises(FileExistsError):
        collect_evidence_from_logs(
            raw_logs, run_id="phase1b-live-001", artifacts_root=tmp_path / "artifacts"
        )


def test_collect_rejects_invalid_evidence_and_unsafe_run_ids(tmp_path) -> None:
    # Structurally fine but semantically invalid totals.
    document = _valid_document()
    document["success_count"] = document["total_requests"]
    raw_logs = render_stdout_document(json.dumps(document))
    with pytest.raises(EvidenceValidationError):
        collect_evidence_from_logs(raw_logs, run_id="bad-totals", artifacts_root=tmp_path / "a")

    with pytest.raises(ValueError, match="path-safe"):
        collect_evidence_from_logs(
            _captured_evidence(), run_id="../escape", artifacts_root=tmp_path / "b"
        )


def test_evidence_cli_collects_from_captured_file(tmp_path) -> None:
    logs_file = tmp_path / "captured-logs.txt"
    logs_file.write_text(_captured_evidence(), encoding="utf-8")
    root = tmp_path / "artifacts"
    exit_code = evidence_cli_main(
        ["--logs", str(logs_file), "--run-id", "cli-run", "--artifacts-root", str(root)]
    )
    assert exit_code == 0
    stored = json.loads((root / "cli-run" / "probe-evidence.json").read_text(encoding="utf-8"))
    assert validate_evidence_document(stored).valid


def test_evidence_cli_reports_format_and_validation_errors(tmp_path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    assert evidence_cli_main(["--logs", str(empty), "--run-id", "r", "--artifacts-root", str(tmp_path)]) == 3

    document = _valid_document()
    document["total_requests"] = 3  # inconsistent with counts/records
    broken = tmp_path / "broken.txt"
    broken.write_text(render_stdout_document(json.dumps(document)), encoding="utf-8")
    assert (
        evidence_cli_main(["--logs", str(broken), "--run-id", "r2", "--artifacts-root", str(tmp_path)])
        == 4
    )


def test_exit_codes_defined() -> None:
    assert (EXIT_OK, EXIT_INVALID_EVIDENCE, EXIT_OUTPUT_FAILED) == (0, 2, 3)
