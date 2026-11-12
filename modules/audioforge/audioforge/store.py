from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import DB_PATH, MAX_ATTEMPTS

from .hq_contract import MODEL as HQ_MODEL, canonical, require_enabled, validate_payload

class IdempotencyConflict(ValueError):
    pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, status TEXT NOT NULL,
 request_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
 artifact_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path); self.lock = threading.RLock(); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db: db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat()

    def submit(self, request: dict[str, Any], key: str | None) -> tuple[dict[str, Any], bool]:
        hq = request.get("model") == HQ_MODEL
        if hq:
            validate_payload(request)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                row = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
                if row:
                    existing = self.decode(row)
                    if hq or existing["request"].get("model") == HQ_MODEL:
                        if canonical(existing["request"]) != canonical(request):
                            raise IdempotencyConflict("IDEMPOTENCY_REQUEST_CONFLICT")
                    return existing, False
            if hq:
                require_enabled()
            job_id, now = uuid.uuid4().hex, self.now()
            db.execute("INSERT INTO jobs(id,idempotency_key,status,request_json,max_attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (job_id,key,"queued",json.dumps(request),1 if hq else MAX_ATTEMPTS,now,now))
            db.commit(); return self.get(job_id), True

    def get(self, job_id: str) -> dict[str, Any]:
        with self.connect() as db: row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row: raise KeyError(job_id)
        return self.decode(row)

    def next_job(self) -> dict[str, Any] | None:
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1").fetchone()
            if not row: db.commit(); return None
            now = self.now(); db.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (now,row["id"])); db.commit()
        return self.get(row["id"])

    def recover_running(self):
        with self.lock, self.connect() as db:
            # HQ interrupted execution is uncertain and must never be regenerated automatically.
            rows = db.execute("SELECT id,request_json FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                hq = json.loads(row["request_json"]).get("model") == HQ_MODEL
                db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?",
                           ("failed" if hq else "queued",
                            "HQ_INTERRUPTED_RECONCILIATION_REQUIRED" if hq else "recovered after service restart",
                            self.now(), row["id"]))
            db.commit()

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row: raise KeyError(job_id)
            status = "cancelled" if row["status"] == "queued" else row["status"]
            db.execute("UPDATE jobs SET cancel_requested=1,status=?,updated_at=? WHERE id=?", (status,self.now(),job_id)); db.commit()
        return self.get(job_id)

    def finish(self, job_id: str, artifact_id: str): self._update(job_id, status="completed", artifact_id=artifact_id, error=None)
    def cancel(self, job_id: str): self._update(job_id, status="cancelled")
    def fail_or_retry(self, job_id: str, error: str):
        job = self.get(job_id); status = "queued" if job["request"].get("model") != HQ_MODEL and job["attempts"] < job["max_attempts"] and not job["cancel_requested"] else "failed"
        self._update(job_id, status=status, error=error)

    def _update(self, job_id: str, **values):
        values["updated_at"] = self.now(); columns = ",".join(f"{key}=?" for key in values)
        with self.lock, self.connect() as db: db.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*values.values(),job_id)); db.commit()

    @staticmethod
    def decode(row):
        data = dict(row); data["request"] = json.loads(data.pop("request_json")); data["cancel_requested"] = bool(data["cancel_requested"]); return data
