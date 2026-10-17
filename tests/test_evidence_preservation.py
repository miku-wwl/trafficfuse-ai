"""Strict timestamps and byte-for-byte raw-log preservation (Phase 1A.2, P2).

Covers: timezone-aware ISO-8601 timestamp validation (Z/+00:00 accepted,
offsets normalized to UTC, naive/malformed/reversed refused via a controlled
error type) and durable raw-log preservation (raw bytes kept separate from the
strict parse copy, bounded size, atomic writes, audit metadata, raw preserved
even when extraction or validation fails). No cluster access anywhere.
"""

import json

import httpx
import pytest

from trafficfuse.lab.evidence import (
    EVIDENCE_BEGIN_MARKER,
    EVIDENCE_END_MARKER,
    EvidenceFormatError,
    TimestampValidationError,
    parse_utc_timestamp,
    render_stdout_document,
    validate_evidence_document,
)
from trafficfuse.lab.evidence_export import (
    AUDIT_FILENAME,
    MAX_RAW_LOG_BYTES,
    RAW_LOGS_FILENAME,
    EvidenceValidationError,
    RawLogDecodingError,
    RawLogSizeError,
    collect_evidence_from_log_bytes,
    collect_evidence_from_log_file,
)
from trafficfuse.lab.evidence_cli import main as evidence_cli_main
from trafficfuse.lab.probe import ProbeConfig, run_probe
from trafficfuse.lab.service import MODE_FAULTY, create_lab_app

import asyncio


def _captured_evidence(rate: int = 50, requests: int = 20) -> str:
    """Real marked-up probe logs from an in-process faulty app."""
    app = create_lab_app(mode=MODE_FAULTY, fault_rate_percent=rate)
    config = ProbeConfig(
        target_url="http://trafficfuse-lab/checkout",
        scenario_id="preservation-selftest",
        target_label="service:trafficfuse-live",
        total_requests=requests,
        concurrency=4,
    )
    evidence = asyncio.run(run_probe(config, transport=httpx.ASGITransport(app=app)))
    return render_stdout_document(evidence.to_json())


def _valid_document() -> dict:
    from trafficfuse.lab.evidence import extract_evidence_document

    return extract_evidence_document(_captured_evidence())


# --- strict timezone-aware timestamps -------------------------------------------------


def test_utc_z_and_offset_forms_accepted_and_normalized() -> None:
    zulu = parse_utc_timestamp("2026-10-18T10:00:00Z", "started_at_utc")
    explicit = parse_utc_timestamp("2026-10-18T10:00:00+00:00", "started_at_utc")
    eastern = parse_utc_timestamp("2026-10-18T18:00:00+08:00", "started_at_utc")
    assert zulu == explicit == eastern  # all normalize to the same UTC instant
    assert zulu.utcoffset().total_seconds() == 0
    # Sub-second precision (as emitted by the probe) must survive.
    precise = parse_utc_timestamp("2026-10-18T10:00:00.123456+00:00", "t")
    assert precise.microsecond == 123456


def test_naive_timestamp_refused() -> None:
    with pytest.raises(TimestampValidationError, match="timezone-aware"):
        parse_utc_timestamp("2026-10-18T10:00:00", "started_at_utc")


@pytest.mark.parametrize("bad", ["", "   ", "not-a-timestamp", "2026-13-01T00:00:00Z", "2026-10-18", "Z"])
def test_malformed_timestamp_refused(bad: str) -> None:
    with pytest.raises(TimestampValidationError):
        parse_utc_timestamp(bad, "started_at_utc")


@pytest.mark.parametrize("bad", [None, 20261018, 1.5, True, {"at": "2026"}])
def test_non_string_timestamp_refused(bad: object) -> None:
    with pytest.raises(TimestampValidationError, match="ISO-8601 string"):
        parse_utc_timestamp(bad, "started_at_utc")


def test_timestamp_error_type_is_controlled() -> None:
    # Controlled type: catchable on its own, still a ValueError for callers
    # that only guard ValueError (backwards compatible).
    assert issubclass(TimestampValidationError, ValueError)
    with pytest.raises(ValueError):
        parse_utc_timestamp("2026-10-18T10:00:00", "t")


def test_document_validation_rejects_naive_and_malformed_timestamps() -> None:
    document = _valid_document()
    document["started_at_utc"] = "2026-10-18T10:00:00"  # naive
    document["completed_at_utc"] = "whenever"
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("started_at_utc" in e and "timezone-aware" in e for e in result.errors)
    assert any("completed_at_utc" in e and "ISO-8601" in e for e in result.errors)


def test_document_validation_accepts_zulu_timestamps() -> None:
    document = _valid_document()
    started = parse_utc_timestamp(document["started_at_utc"], "started_at_utc")
    completed = parse_utc_timestamp(document["completed_at_utc"], "completed_at_utc")
    document["started_at_utc"] = started.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    document["completed_at_utc"] = completed.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    assert validate_evidence_document(document).valid


def test_reversed_timestamps_refused_across_offsets() -> None:
    document = _valid_document()
    # 20:00+08:00 is 12:00Z; completed 11:00Z is genuinely earlier.
    document["started_at_utc"] = "2026-10-18T20:00:00+08:00"
    document["completed_at_utc"] = "2026-10-18T11:00:00Z"
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("precede" in e for e in result.errors)


def test_equal_and_offset_shifted_timestamps_accepted() -> None:
    document = _valid_document()
    document["started_at_utc"] = "2026-10-18T10:00:00Z"
    document["completed_at_utc"] = "2026-10-18T18:00:00+08:00"  # same instant
    assert validate_evidence_document(document).valid


def test_mixed_naive_and_aware_never_raise_type_error() -> None:
    # Regression: the old ordering check compared raw strings, which could raise
    # an unhandled TypeError on naive-vs-aware comparison. Field errors only.
    document = _valid_document()
    document["started_at_utc"] = "2026-10-18T10:00:00"  # naive
    document["completed_at_utc"] = "2026-10-18T09:00:00Z"  # aware
    result = validate_evidence_document(document)
    assert not result.valid
    assert any("timezone-aware" in e for e in result.errors)


# --- byte-for-byte raw log preservation ------------------------------------------------


def test_raw_bytes_preserved_exactly(tmp_path) -> None:
    raw = _captured_evidence().encode("utf-8")
    result = collect_evidence_from_log_bytes(
        raw, run_id="bytes-001", artifacts_root=tmp_path / "artifacts"
    )
    assert result.raw_logs_path.read_bytes() == raw  # byte-for-byte
    assert result.evidence_path.exists()


def test_raw_logs_preserved_when_extraction_fails(tmp_path) -> None:
    raw = f"{EVIDENCE_BEGIN_MARKER}\n{{not valid json\n{EVIDENCE_END_MARKER}\n".encode()
    with pytest.raises(EvidenceFormatError):
        collect_evidence_from_log_bytes(raw, run_id="broken-001", artifacts_root=tmp_path / "a")
    preserved = (tmp_path / "a" / "broken-001" / RAW_LOGS_FILENAME).read_bytes()
    assert preserved == raw
    audit = json.loads((tmp_path / "a" / "broken-001" / AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["failure"]["stage"] == "extract"
    assert audit["failure"]["error_type"] == "EvidenceFormatError"


def test_raw_logs_preserved_when_validation_fails(tmp_path) -> None:
    document = _valid_document()
    document["success_count"] = document["total_requests"]  # inconsistent
    raw = render_stdout_document(json.dumps(document)).encode("utf-8")
    with pytest.raises(EvidenceValidationError):
        collect_evidence_from_log_bytes(raw, run_id="bad-001", artifacts_root=tmp_path / "a")
    preserved = (tmp_path / "a" / "bad-001" / RAW_LOGS_FILENAME).read_bytes()
    assert preserved == raw
    audit = json.loads((tmp_path / "a" / "bad-001" / AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["failure"]["stage"] == "validate"


def test_non_utf8_capture_refused_and_preserved(tmp_path) -> None:
    raw = b"TRAFFICFUSE_EVIDENCE_BEGIN\n\xff\xfe invalid utf-8\nTRAFFICFUSE_EVIDENCE_END\n"
    with pytest.raises(RawLogDecodingError):
        collect_evidence_from_log_bytes(raw, run_id="latin-001", artifacts_root=tmp_path / "a")
    preserved = (tmp_path / "a" / "latin-001" / RAW_LOGS_FILENAME).read_bytes()
    assert preserved == raw  # no lossy re-encoding, bytes kept exactly
    audit = json.loads((tmp_path / "a" / "latin-001" / AUDIT_FILENAME).read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["failure"]["stage"] == "decode"


def test_oversized_capture_refused_before_writing(tmp_path) -> None:
    root = tmp_path / "a"
    with pytest.raises(RawLogSizeError, match="exceeding"):
        collect_evidence_from_log_bytes(
            b"x" * (MAX_RAW_LOG_BYTES + 1), run_id="huge", artifacts_root=root
        )
    # Nothing is written when the capture is refused for size: bounded, fail fast.
    assert not (root / "huge").exists()


def test_successful_audit_metadata_records_exact_capture(tmp_path) -> None:
    import hashlib

    raw = _captured_evidence().encode("utf-8")
    result = collect_evidence_from_log_bytes(
        raw, run_id="audit-001", artifacts_root=tmp_path / "artifacts"
    )
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["audit_schema"] == "trafficfuse-evidence-audit-v1"
    assert audit["run_id"] == "audit-001"
    assert audit["status"] == "ok"
    assert audit["raw_logs"]["bytes"] == len(raw)
    assert audit["raw_logs"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert audit["parse_copy"]["encoding"] == "utf-8"
    # The audit timestamp itself is strict timezone-aware UTC.
    assert parse_utc_timestamp(audit["collected_at_utc"], "collected_at_utc")


def test_no_temporary_files_left_behind(tmp_path) -> None:
    raw = _captured_evidence().encode("utf-8")
    collect_evidence_from_log_bytes(raw, run_id="atomic-001", artifacts_root=tmp_path / "ok")
    with pytest.raises(EvidenceFormatError):
        collect_evidence_from_log_bytes(
            b"no markers here", run_id="atomic-002", artifacts_root=tmp_path / "fail"
        )
    for run_dir in (tmp_path / "ok" / "atomic-001", tmp_path / "fail" / "atomic-002"):
        leftovers = [p.name for p in run_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


def test_existing_run_directory_never_overwritten(tmp_path) -> None:
    raw = _captured_evidence().encode("utf-8")
    collect_evidence_from_log_bytes(raw, run_id="once", artifacts_root=tmp_path / "a")
    with pytest.raises(FileExistsError):
        collect_evidence_from_log_bytes(raw, run_id="once", artifacts_root=tmp_path / "a")


def test_log_file_wrapper_preserves_crlf_bytes(tmp_path) -> None:
    raw = _captured_evidence().replace("\n", "\r\n").encode("utf-8")
    logs_file = tmp_path / "captured-with-crlf.txt"
    logs_file.write_bytes(raw)
    result = collect_evidence_from_log_file(logs_file, run_id="crlf-001", artifacts_root=tmp_path / "a")
    assert result.raw_logs_path.read_bytes() == raw


def test_cli_preserves_raw_bytes_from_file(tmp_path, monkeypatch) -> None:
    raw = _captured_evidence().encode("utf-8")
    logs_file = tmp_path / "captured.txt"
    logs_file.write_bytes(raw)
    root = tmp_path / "artifacts"
    exit_code = evidence_cli_main(
        ["--logs", str(logs_file), "--run-id", "cli-bytes", "--artifacts-root", str(root)]
    )
    assert exit_code == 0
    assert (root / "cli-bytes" / RAW_LOGS_FILENAME).read_bytes() == raw


def test_cli_reports_where_raw_logs_were_preserved(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.stdin",
        type("FakeStdin", (), {"buffer": type("Buf", (), {"read": staticmethod(lambda: b"junk")})()})(),
    )
    root = tmp_path / "artifacts"
    exit_code = evidence_cli_main(
        ["--logs", "-", "--run-id", "stdin-junk", "--artifacts-root", str(root)]
    )
    captured = capsys.readouterr()
    assert exit_code == 3
    assert "raw logs preserved byte-for-byte" in captured.err
    assert str(root / "stdin-junk" / RAW_LOGS_FILENAME) in captured.err
    assert (root / "stdin-junk" / RAW_LOGS_FILENAME).read_bytes() == b"junk"
