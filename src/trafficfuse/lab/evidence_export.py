"""Durable probe-evidence collection for the Phase 1B workflow.

The cluster-side Probe Job prints its complete evidence document to stdout
between ``TRAFFICFUSE_EVIDENCE_BEGIN`` / ``TRAFFICFUSE_EVIDENCE_END`` markers
(see ``trafficfuse.lab.evidence``). The Phase 1B collection process is:

1. ``kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \\
       --context kind-trafficfuse-lab -n trafficfuse-system \\
       logs job/trafficfuse-probe``   (exact owned Job/Pod, read-only)
2. pipe the captured logs into :func:`collect_evidence_from_logs` (or
   ``python -m trafficfuse.lab.evidence_cli --logs <captured-file>``);
3. the module extracts exactly one complete document, strictly validates it,
   and stores both the parsed document and the original raw logs under
   ``artifacts/phase1b/<run-id>/`` (git-ignored).

Failure modes are hard errors, never silent: missing, truncated, malformed,
invalid or duplicated evidence, or an export that cannot be written. Raw logs
are preserved byte-for-byte for auditability, and an existing run directory is
never overwritten. No cluster is contacted by this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EvidenceValidationError(Exception):
    """Raised when an extracted evidence document fails strict validation."""


@dataclass(frozen=True)
class ExportResult:
    run_id: str
    run_directory: Path
    evidence_path: Path
    raw_logs_path: Path
    document: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_directory": str(self.run_directory),
            "evidence_path": str(self.evidence_path),
            "raw_logs_path": str(self.raw_logs_path),
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


def collect_evidence_from_logs(
    raw_logs: str,
    *,
    run_id: str,
    artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
) -> ExportResult:
    """Extract, validate and durably store the evidence from captured logs."""
    _check_run_id(run_id)
    document = extract_evidence_document(raw_logs)  # structural, exactly one
    validation = validate_evidence_document(document)
    if not validation.valid:
        raise EvidenceValidationError(
            "evidence document failed strict validation: "
            + "; ".join(validation.errors)
        )

    run_directory = Path(artifacts_root) / run_id
    if run_directory.exists():
        raise FileExistsError(
            f"run directory {run_directory} already exists; refusing to overwrite "
            "existing evidence (use a new run id)"
        )
    run_directory.mkdir(parents=True, exist_ok=False)

    evidence_path = run_directory / EVIDENCE_FILENAME
    raw_logs_path = run_directory / RAW_LOGS_FILENAME
    evidence_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Raw logs preserved byte-for-byte for auditability.
    raw_logs_path.write_bytes(raw_logs.encode("utf-8"))
    return ExportResult(
        run_id=run_id,
        run_directory=run_directory,
        evidence_path=evidence_path,
        raw_logs_path=raw_logs_path,
        document=document,
    )


def collect_evidence_from_log_file(
    logs_path: Path | str,
    *,
    run_id: str | None = None,
    artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
) -> ExportResult:
    """Convenience wrapper: capture logs from a file, derive the run id."""
    path = Path(logs_path)
    resolved_run_id = run_id or path.stem or "run"
    return collect_evidence_from_logs(
        path.read_text(encoding="utf-8", errors="replace"),
        run_id=resolved_run_id,
        artifacts_root=artifacts_root,
    )
