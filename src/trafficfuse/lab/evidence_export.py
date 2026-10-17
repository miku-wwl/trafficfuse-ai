"""Durable probe-evidence collection for the Phase 1B workflow.

The cluster-side Probe Job prints its complete evidence document to stdout
between ``TRAFFICFUSE_EVIDENCE_BEGIN`` / ``TRAFFICFUSE_EVIDENCE_END`` markers
(see ``trafficfuse.lab.evidence``). The Phase 1B collection process is:

1. ``kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
       --context kind-trafficfuse-lab -n trafficfuse-system \\
       logs job/trafficfuse-probe``   (exact owned Job/Pod, read-only)
2. pipe the captured logs into :func:`collect_evidence_from_log_bytes` (or
   ``python -m trafficfuse.lab.evidence_cli --logs <captured-file>``);
3. the module extracts exactly one complete document, strictly validates it,
   and stores both the parsed document and the original raw logs under
   ``artifacts/phase1b/<run-id>/`` (git-ignored).

Failure modes are hard errors, never silent: missing, truncated, malformed,
invalid or duplicated evidence, undecodable or oversized logs, or an export
that cannot be written. An existing run directory is never overwritten. No
cluster is contacted by this module.

Byte-for-byte preservation (Phase 1A.2 review finding P2):

* the capture is handled as **raw bytes** — the strict UTF-8 decode exists only
  as a private parse copy, so nothing is silently transcoded or lossily
  re-encoded before it hits disk;
* the raw logs are written **before** parsing and are therefore preserved even
  when extraction or validation fails (the audit file records the failure);
* raw capture size is bounded (:data:`MAX_RAW_LOG_BYTES`);
* every file is written atomically (temporary file + ``os.replace``), so a
  crashed export never leaves a half-written artifact;
* each run directory carries an audit file (``evidence-audit.json``) with the
  capture's exact byte length, SHA-256, strict UTC collection timestamp and
  final status.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trafficfuse.lab.evidence import (
    EvidenceFormatError,
    extract_evidence_document,
    validate_evidence_document,
)

DEFAULT_ARTIFACTS_ROOT = Path("artifacts") / "phase1b"
EVIDENCE_FILENAME = "probe-evidence.json"
RAW_LOGS_FILENAME = "raw-probe-logs.txt"
AUDIT_FILENAME = "evidence-audit.json"

# Bounded raw capture: a Probe Job run produces tens of kilobytes; 16 MiB is a
# generous ceiling that still refuses a runaway or hostile capture.
MAX_RAW_LOG_BYTES = 16 * 1024 * 1024

AUDIT_SCHEMA = "trafficfuse-evidence-audit-v1"

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EvidenceValidationError(Exception):
    """Raised when an extracted evidence document fails strict validation."""


class RawLogSizeError(ValueError):
    """Raised when the raw capture exceeds :data:`MAX_RAW_LOG_BYTES`."""


class RawLogDecodingError(ValueError):
    """Raised when the raw capture is not valid UTF-8 (no lossy fallback)."""


@dataclass(frozen=True)
class ExportResult:
    run_id: str
    run_directory: Path
    evidence_path: Path
    raw_logs_path: Path
    audit_path: Path
    document: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_directory": str(self.run_directory),
            "evidence_path": str(self.evidence_path),
            "raw_logs_path": str(self.raw_logs_path),
            "audit_path": str(self.audit_path),
            "scenario_id": self.document.get("scenario_id"),
            "total_requests": self.document.get("total_requests"),
            "error_count": self.document.get("error_count"),
            "observed_error_percent": self.document.get("observed_error_percent"),
        }


def _check_run_id(run_id: str) -> str:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(
            f"run_id {run_id!r} must match {_RUN_ID.pattern} (path-safe, no traversal)"
        )
    return run_id


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    """Strict timezone-aware UTC timestamp (same form the probe emits)."""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically: temporary file in the same dir, then replace."""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _record_audit_failure(
    audit_path: Path, audit: dict[str, Any], stage: str, error: BaseException
) -> None:
    """Preserve the failure in the audit file (raw logs already on disk)."""
    audit = dict(audit)
    audit["status"] = "failed"
    audit["failure"] = {
        "stage": stage,
        "error_type": type(error).__name__,
        "error": str(error),
        "failed_at_utc": _utc_now_iso(),
    }
    _atomic_write_json(audit_path, audit)


def collect_evidence_from_log_bytes(
    raw_logs: bytes | bytearray,
    *,
    run_id: str,
    artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
) -> ExportResult:
    """Extract, validate and durably store the evidence from raw captured bytes.

    The raw bytes are preserved byte-for-byte **before** any parsing happens, so
    a failed extraction or validation still leaves an auditable capture. The
    strict UTF-8 decode is a private parse copy only. Fails closed on an
    existing run directory, an oversized capture, undecodable bytes, missing /
    truncated / malformed / duplicated evidence, or invalid content.
    """
    _check_run_id(run_id)
    raw = bytes(raw_logs)
    if len(raw) > MAX_RAW_LOG_BYTES:
        raise RawLogSizeError(
            f"raw logs are {len(raw)} bytes, exceeding the {MAX_RAW_LOG_BYTES}-byte "
            "limit; refusing to collect an unbounded capture"
        )

    run_directory = Path(artifacts_root) / run_id
    if run_directory.exists():
        raise FileExistsError(
            f"run directory {run_directory} already exists; refusing to overwrite "
            "existing evidence (use a new run id)"
        )
    run_directory.mkdir(parents=True, exist_ok=False)

    raw_logs_path = run_directory / RAW_LOGS_FILENAME
    audit_path = run_directory / AUDIT_FILENAME

    # 1. Preserve the raw capture first, byte-for-byte (atomic write).
    _atomic_write_bytes(raw_logs_path, raw)
    audit: dict[str, Any] = {
        "audit_schema": AUDIT_SCHEMA,
        "run_id": run_id,
        "collected_at_utc": _utc_now_iso(),
        "status": "collected",
        "raw_logs": {
            "filename": RAW_LOGS_FILENAME,
            "bytes": len(raw),
            "sha256": _sha256_hex(raw),
        },
    }
    _atomic_write_json(audit_path, audit)

    # 2. Strict decode — the parse copy only; raw bytes on disk stay untouched.
    try:
        log_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _record_audit_failure(audit_path, audit, "decode", exc)
        raise RawLogDecodingError(
            f"raw logs are not valid UTF-8 at byte {exc.start}: {exc.reason}; "
            "raw capture preserved for inspection"
        ) from exc

    # 3. Extract exactly one complete document (structural).
    try:
        document = extract_evidence_document(log_text)
    except EvidenceFormatError as exc:
        _record_audit_failure(audit_path, audit, "extract", exc)
        raise

    # 4. Strict content validation.
    validation = validate_evidence_document(document)
    if not validation.valid:
        failure = EvidenceValidationError(
            "evidence document failed strict validation: " + "; ".join(validation.errors)
        )
        _record_audit_failure(audit_path, audit, "validate", failure)
        raise failure

    # 5. Store the parsed document; mark the run audited and complete.
    evidence_path = run_directory / EVIDENCE_FILENAME
    document_bytes = (
        json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(evidence_path, document_bytes)

    audit["status"] = "ok"
    audit["parse_copy"] = {"encoding": "utf-8", "characters": len(log_text)}
    audit["evidence"] = {
        "filename": EVIDENCE_FILENAME,
        "bytes": len(document_bytes),
        "sha256": _sha256_hex(document_bytes),
    }
    _atomic_write_json(audit_path, audit)

    return ExportResult(
        run_id=run_id,
        run_directory=run_directory,
        evidence_path=evidence_path,
        raw_logs_path=raw_logs_path,
        audit_path=audit_path,
        document=document,
    )


def collect_evidence_from_logs(
    raw_logs: str,
    *,
    run_id: str,
    artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
) -> ExportResult:
    """Text-entry wrapper around :func:`collect_evidence_from_log_bytes`.

    Prefer the bytes API: a ``str`` caller has already decoded the capture, so
    byte-for-byte fidelity can only be guaranteed from the original bytes.
    """
    return collect_evidence_from_log_bytes(
        raw_logs.encode("utf-8"),
        run_id=run_id,
        artifacts_root=artifacts_root,
    )


def collect_evidence_from_log_file(
    logs_path: Path | str,
    *,
    run_id: str | None = None,
    artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
) -> ExportResult:
    """Convenience wrapper: read a capture file as raw bytes, derive the run id."""
    path = Path(logs_path)
    resolved_run_id = run_id or path.stem or "run"
    return collect_evidence_from_log_bytes(
        path.read_bytes(),
        run_id=resolved_run_id,
        artifacts_root=artifacts_root,
    )
