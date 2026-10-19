"""Durable consumer incident store (SQLite): incidents, audit events, refusals.

This is TrafficFuse's own record of what it observed and did. Governance facts
(Run/Approval/Action/Authorization state) are *not* duplicated as authority
here — they are re-read from the Agentic Intent Gate ledger whenever an
incident is displayed; the events below record what TrafficFuse requested and
what the gate returned, with timestamps, for the audit timeline.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL,
    stage TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    bundle_digest TEXT NOT NULL UNIQUE,
    assessment_json TEXT NOT NULL,
    diagnosis_json TEXT,
    model_json TEXT,
    diagnosis_error TEXT,
    plan_json TEXT,
    run_id TEXT,
    action_id TEXT,
    approver TEXT,
    verification_json TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT,
    at TEXT NOT NULL,
    stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    outcome TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS refusals (
    refusal_id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    scenario TEXT NOT NULL,
    title TEXT NOT NULL,
    origin TEXT NOT NULL,
    layer TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    run_id TEXT,
    run_state TEXT,
    error_code TEXT,
    issues_json TEXT NOT NULL
) STRICT;
"""

_JSON_COLUMNS = {"bundle_json", "assessment_json", "diagnosis_json", "model_json", "plan_json", "verification_json"}
_UPDATABLE = {
    "stage", "diagnosis_json", "model_json", "diagnosis_error", "plan_json",
    "run_id", "action_id", "approver", "verification_json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in list(value):
            if key in _JSON_COLUMNS or key.endswith("_json"):
                raw = value.pop(key)
                value[key.removesuffix("_json")] = json.loads(raw) if raw else None
        return value

    def create_incident(self, incident_id: str, *, source: str, stage: str, bundle: dict, assessment: dict) -> None:
        now = utc_now()
        with self._lock, closing(self._connect()) as db:
            db.execute(
                "INSERT INTO incidents(incident_id, created_at, updated_at, source, stage, bundle_json, "
                "bundle_digest, assessment_json) VALUES (?,?,?,?,?,?,?,?)",
                (incident_id, now, now, source, stage, json.dumps(bundle), bundle["digest"], json.dumps(assessment)),
            )

    def update(self, incident_id: str, **fields: Any) -> None:
        if not fields or not set(fields) <= _UPDATABLE:
            raise ValueError("unsupported incident update")
        values = {
            key: (json.dumps(value) if key.endswith("_json") and value is not None else value)
            for key, value in fields.items()
        }
        assignments = ",".join(f"{key}=?" for key in values)
        with self._lock, closing(self._connect()) as db:
            db.execute(
                f"UPDATE incidents SET {assignments}, updated_at=? WHERE incident_id=?",
                (*values.values(), utc_now(), incident_id),
            )

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        return self._decode(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()
        return [self._decode(row) for row in rows]

    def bundle_by_digest(self, digest: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT bundle_json FROM incidents WHERE bundle_digest=?", (digest,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_event(
        self,
        incident_id: str | None,
        *,
        stage: str,
        actor: str,
        outcome: str,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute(
                "INSERT INTO events(incident_id, at, stage, actor, outcome, summary, detail_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (incident_id, utc_now(), stage, actor, outcome, summary, json.dumps(detail or {})),
            )

    def events(self, incident_id: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            if incident_id is None:
                rows = db.execute("SELECT * FROM events ORDER BY seq").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM events WHERE incident_id=? ORDER BY seq", (incident_id,)
                ).fetchall()
        return [self._decode(row) for row in rows]

    def add_refusal(self, record: dict[str, Any]) -> None:
        with self._lock, closing(self._connect()) as db:
            db.execute(
                "INSERT INTO refusals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["refusal_id"], record["created_at"], record["scenario"], record["title"],
                    record["origin"], record["layer"], json.dumps(record["plan"]), record.get("run_id"),
                    record.get("run_state"), record.get("error_code"), json.dumps(record["issues"]),
                ),
            )

    def refusals(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM refusals ORDER BY created_at DESC").fetchall()
        return [self._decode(row) for row in rows]
