"""Structured probe-evidence validation and a stable stdout/stdout-log format.

Phase 1A.1 (review finding P1): probe evidence must be reliably extractable
from Kubernetes logs and strictly validated before it is trusted. This module
owns:

* the unambiguous stdout markers that wrap exactly one complete JSON evidence
  document (progress logs go to stderr, never into the document);
* strict validation of a complete evidence document (schema, identity, counts,
  HTTP status classifications, latency, observed error rate, synthetic
  workload declaration, and strict timezone-aware observation timestamps
  normalized to UTC — a naive or malformed timestamp is refused, and
  ``completed_at_utc`` may never precede ``started_at_utc``);
* extraction of the single evidence document from raw logs, rejecting
  missing, truncated, malformed or duplicated documents.

The configured fault percentage is a policy input and never appears in the
evidence; the observed error rate is derived from real responses only
(AGENTS.md synthetic-metrics rule).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trafficfuse.lab.probe import (
    _SERVED_POD,
    _SERVED_VERSION,
    EVIDENCE_SOURCE,
    OUTCOME_HTTP_ERROR,
    OUTCOME_MALFORMED,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    OUTCOME_TRANSPORT_ERROR,
    PROBE_EVIDENCE_SCHEMA,
)

EVIDENCE_BEGIN_MARKER = "TRAFFICFUSE_EVIDENCE_BEGIN"
EVIDENCE_END_MARKER = "TRAFFICFUSE_EVIDENCE_END"

ALLOWED_OUTCOMES = (
    OUTCOME_SUCCESS,
    OUTCOME_HTTP_ERROR,
    OUTCOME_TIMEOUT,
    OUTCOME_TRANSPORT_ERROR,
    OUTCOME_MALFORMED,
)

REQUIRED_LATENCY_KEYS = ("min", "p50", "p95", "max", "mean")


class EvidenceFormatError(Exception):
    """Raised when logs do not contain exactly one complete evidence document."""


class TimestampValidationError(ValueError):
    """Raised when a timestamp is not a strict timezone-aware ISO-8601 value.

    Controlled error type: subclasses ``ValueError`` so existing callers keep
    working, but lets callers distinguish timestamp defects from unrelated
    ``ValueError``/``TypeError`` raised by stdlib datetime parsing.
    """


def parse_utc_timestamp(value: Any, field_name: str = "timestamp") -> datetime:
    """Strictly parse an ISO-8601 timestamp and normalize it to UTC.

    Rules (Phase 1A.2 review finding P2):

    * the value must be a string;
    * it must parse as ISO-8601;
    * it must carry an explicit UTC offset — a naive timestamp (no offset)
      is refused, because an unlabeled local time cannot be evidence;
    * an explicit UTC designator (``Z``/``z`` or ``+00:00``) is accepted, and
      any other explicit offset is accepted and normalized to UTC;
    * malformed values raise :class:`TimestampValidationError` with the field
      name, never a bare stdlib exception.
    """
    if not isinstance(value, str):
        raise TimestampValidationError(
            f"{field_name} must be an ISO-8601 string, got {value!r}"
        )
    text = value.strip()
    if not text:
        raise TimestampValidationError(f"{field_name} must not be empty")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise TimestampValidationError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TimestampValidationError(
            f"{field_name} must be timezone-aware (explicit 'Z' or '+00:00' offset), "
            f"got {value!r}"
        )
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class EvidenceValidationResult:
    """Strict validation outcome: never raises, always reports every problem."""

    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return not self.errors

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.valid


def _require_str(document: dict[str, Any], key: str, errors: list[str], *, non_empty: bool = True) -> None:
    value = document.get(key)
    if not isinstance(value, str) or (non_empty and not value.strip()):
        errors.append(f"{key} must be a non-empty string, got {value!r}")


def _require_iso_timestamp(value: Any, key: str, errors: list[str]) -> datetime | None:
    """Validate one timestamp strictly; return the normalized UTC value or None."""
    try:
        return parse_utc_timestamp(value, key)
    except TimestampValidationError as exc:
        errors.append(str(exc))
        return None


def validate_evidence_document(document: Any) -> EvidenceValidationResult:
    """Strictly validate one complete probe evidence document.

    Required: schema version; scenario id; source/target identity; request
    count; success and error counts; HTTP status classifications (per-record
    outcomes); measured latency; observed error rate; synthetic workload
    declaration; real observation timestamps. Any deviation is reported, and
    an invalid document must never be written to disk or trusted.
    """
    errors: list[str] = []
    if not isinstance(document, dict):
        return EvidenceValidationResult(errors=(f"evidence must be a JSON object, got {type(document).__name__}",))

    if document.get("schema") != PROBE_EVIDENCE_SCHEMA:
        errors.append(
            f"schema must be {PROBE_EVIDENCE_SCHEMA!r}, got {document.get('schema')!r}"
        )

    _require_str(document, "scenario_id", errors)
    _require_str(document, "target_label", errors)
    _require_str(document, "target_url", errors)
    _require_str(document, "evidence_source", errors)
    if document.get("evidence_source") != EVIDENCE_SOURCE:
        errors.append(f"evidence_source must be {EVIDENCE_SOURCE!r}")
    _require_str(document, "workload_declaration", errors)

    if not isinstance(document.get("synthetic_workload"), bool):
        errors.append("synthetic_workload must be a boolean")

    total = document.get("total_requests")
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        errors.append(f"total_requests must be an integer >= 1, got {total!r}")
        total = None

    success = document.get("success_count")
    if isinstance(success, bool) or not isinstance(success, int) or success < 0:
        errors.append(f"success_count must be an integer >= 0, got {success!r}")
        success = None
    error = document.get("error_count")
    if isinstance(error, bool) or not isinstance(error, int) or error < 0:
        errors.append(f"error_count must be an integer >= 0, got {error!r}")
        error = None
    if total is not None and success is not None and error is not None:
        if success + error != total:
            errors.append(
                f"success_count + error_count ({success}+{error}) must equal "
                f"total_requests ({total})"
            )

    observed = document.get("observed_error_percent")
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        errors.append(f"observed_error_percent must be a number, got {observed!r}")
    elif total is not None and error is not None:
        expected = round(100.0 * error / total, 2)
        if abs(float(observed) - expected) > 0.011:
            errors.append(
                f"observed_error_percent {observed} is inconsistent with "
                f"{error}/{total} (expected {expected})"
            )

    # Strict timezone-aware timestamps, normalized to UTC before comparison:
    # a naive value never reaches the ordering check (already reported above),
    # and a completed_at earlier than started_at is refused.
    started = _require_iso_timestamp(document.get("started_at_utc"), "started_at_utc", errors)
    completed = _require_iso_timestamp(document.get("completed_at_utc"), "completed_at_utc", errors)
    if started is not None and completed is not None and completed < started:
        errors.append("completed_at_utc must not precede started_at_utc")

    latency = document.get("latency_ms")
    if not isinstance(latency, dict):
        errors.append(f"latency_ms must be an object, got {latency!r}")
    else:
        for key in REQUIRED_LATENCY_KEYS:
            if key not in latency:
                errors.append(f"latency_ms.{key} is missing")
            elif latency[key] is not None and (
                isinstance(latency[key], bool) or not isinstance(latency[key], (int, float))
            ):
                errors.append(f"latency_ms.{key} must be a number or null")

    records = document.get("records")
    if not isinstance(records, list):
        errors.append(f"records must be a list, got {records!r}")
    else:
        if total is not None and len(records) != total:
            errors.append(
                f"records length ({len(records)}) must equal total_requests ({total})"
            )
        sequences: list[int] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                errors.append(f"records[{index}] must be an object")
                continue
            sequence = record.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                errors.append(f"records[{index}].sequence must be an integer >= 1, got {sequence!r}")
            else:
                sequences.append(sequence)
            outcome = record.get("outcome")
            if outcome not in ALLOWED_OUTCOMES:
                errors.append(
                    f"records[{index}].outcome {outcome!r} is not a classified outcome "
                    f"({', '.join(ALLOWED_OUTCOMES)})"
                )
            status_code = record.get("status_code")
            if status_code is not None and (
                isinstance(status_code, bool) or not isinstance(status_code, int)
            ):
                errors.append(f"records[{index}].status_code must be an integer or null")
            duration = record.get("duration_ms")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
                errors.append(f"records[{index}].duration_ms must be a number >= 0")
            # Optional routing evidence (absent in earlier documents): null or
            # the identifier shapes the probe itself accepts.
            for key, pattern in (("served_version", _SERVED_VERSION), ("served_pod", _SERVED_POD)):
                value = record.get(key)
                if value is not None and (
                    not isinstance(value, str) or not pattern.fullmatch(value)
                ):
                    errors.append(f"records[{index}].{key} must be null or a short identifier")
        if len(sequences) == len(records) and len(set(sequences)) != len(sequences):
            errors.append("records contain duplicate sequence numbers")
        if total is not None and sorted(sequences) != list(range(1, total + 1)):
            errors.append("records must cover every sequence number 1..total_requests exactly once")

    return EvidenceValidationResult(errors=tuple(errors))


def render_stdout_document(payload: str) -> str:
    """Wrap one complete JSON evidence document in the stable log markers."""
    return f"{EVIDENCE_BEGIN_MARKER}\n{payload}\n{EVIDENCE_END_MARKER}\n"


def _blocks(log_text: str) -> tuple[list[str], list[str]]:
    """Return (complete_json_blocks, structural_problems) for the log text."""
    problems: list[str] = []
    blocks: list[str] = []
    begin: int | None = None
    for lineno, line in enumerate(log_text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == EVIDENCE_BEGIN_MARKER:
            if begin is not None:
                problems.append(f"nested {EVIDENCE_BEGIN_MARKER} marker at line {lineno}")
            begin = lineno
        elif stripped == EVIDENCE_END_MARKER:
            if begin is None:
                problems.append(f"{EVIDENCE_END_MARKER} at line {lineno} without a begin marker")
            else:
                blocks.append("\n".join(log_text.splitlines()[begin : lineno - 1]))
                begin = None
    if begin is not None:
        problems.append(
            f"evidence block begun at line {begin + 1} has no {EVIDENCE_END_MARKER} "
            "marker (truncated output)"
        )
    if not blocks and not problems:
        problems.append(f"no {EVIDENCE_BEGIN_MARKER} marker in logs")
    return blocks, problems


def find_evidence_documents(log_text: str) -> list[dict[str, Any]]:
    """Parse every complete evidence block in ``log_text``.

    Raises EvidenceFormatError with a precise reason when the logs are
    missing, truncated, malformed or ambiguous.
    """
    blocks, problems = _blocks(log_text)
    documents: list[dict[str, Any]] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            problems.append("an evidence block is empty")
            continue
        try:
            documents.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            problems.append(f"evidence block is not valid JSON: {exc}")
    if problems:
        raise EvidenceFormatError("; ".join(problems))
    return documents


def extract_evidence_document(log_text: str) -> dict[str, Any]:
    """Extract exactly one complete, structurally sound evidence document.

    Refuses missing output, truncated documents, malformed JSON and
    ambiguous or duplicated evidence documents. Structure only: callers must
    still run :func:`validate_evidence_document` before trusting content.
    """
    if not log_text or not log_text.strip():
        raise EvidenceFormatError("log output is empty; no evidence document found")
    documents = find_evidence_documents(log_text)
    if not documents:
        raise EvidenceFormatError("no complete evidence document found in logs")
    if len(documents) > 1:
        raise EvidenceFormatError(
            f"logs contain {len(documents)} evidence documents; expected exactly one "
            "(ambiguous or duplicated evidence)"
        )
    return documents[0]
