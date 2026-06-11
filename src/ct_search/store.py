"""SQLite persistence for runs and their progress events.

Phase 2 of the roadmap: runs survive page reloads and server restarts, the
workbench can list history, and the SSE stream can replay missed events on
reconnect. Stdlib sqlite3 in WAL mode keeps the stack zero-config — no
external database or queue to operate.

The store is intentionally synchronous: every call is a single fast statement
on a local file, called either from request handlers (cheap) or the run task.
A process-wide lock serializes writers; WAL keeps readers unblocked.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ct_search.models import ResearchRequest, ResearchResponse

RunStatus = Literal["queued", "running", "done", "error"]

_DB_PATH = Path(
    os.environ.get(
        "CT_SEARCH_DB_PATH",
        Path(__file__).resolve().parent.parent.parent / "output" / "edna.db",
    )
)
_LOCK = threading.Lock()
_CONNECTION: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    query TEXT NOT NULL,
    mode TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT,
    error TEXT,
    provider TEXT,
    strategy TEXT,
    estimated_cost REAL,
    is_demo INTEGER,
    elapsed_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS run_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events (run_id, seq);
"""


def _connection() -> sqlite3.Connection:
    global _CONNECTION
    if _CONNECTION is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(_DB_PATH, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(_SCHEMA)
        _CONNECTION = connection
    return _CONNECTION


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


def create_run(run_id: str, request: ResearchRequest) -> None:
    now = _now()
    with _LOCK:
        _connection().execute(
            """INSERT INTO runs (id, created_at, updated_at, status, query, mode,
                                 row_count, request_json)
               VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)""",
            (
                run_id,
                now,
                now,
                request.query,
                request.mode,
                len(request.rows),
                request.model_dump_json(),
            ),
        )
        _connection().commit()


def mark_running(run_id: str) -> None:
    with _LOCK:
        _connection().execute(
            "UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?",
            (_now(), run_id),
        )
        _connection().commit()


def complete_run(run_id: str, response: ResearchResponse) -> None:
    with _LOCK:
        _connection().execute(
            """UPDATE runs SET status = 'done', updated_at = ?, response_json = ?,
                               provider = ?, strategy = ?, estimated_cost = ?,
                               is_demo = ?, elapsed_ms = ?
               WHERE id = ?""",
            (
                _now(),
                response.model_dump_json(),
                response.provider,
                response.route.strategy,
                response.estimated_cost,
                1 if response.is_demo else 0,
                response.elapsed_ms,
                run_id,
            ),
        )
        _connection().commit()


def fail_run(run_id: str, error: str) -> None:
    with _LOCK:
        _connection().execute(
            "UPDATE runs SET status = 'error', updated_at = ?, error = ? WHERE id = ?",
            (_now(), error[:500], run_id),
        )
        _connection().commit()


def fail_orphaned_runs(reason: str) -> int:
    """Mark queued/running runs as errored — called on startup after a restart."""
    with _LOCK:
        cursor = _connection().execute(
            """UPDATE runs SET status = 'error', updated_at = ?, error = ?
               WHERE status IN ('queued', 'running')""",
            (_now(), reason),
        )
        _connection().commit()
        return cursor.rowcount


def get_run(run_id: str) -> dict[str, Any] | None:
    row = _connection().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run_to_dict(row) if row else None


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    rows = _connection().execute(
        """SELECT id, created_at, updated_at, status, query, mode, row_count,
                  error, provider, strategy, estimated_cost, is_demo, elapsed_ms
           FROM runs ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_run_to_dict(row) for row in rows]


def append_event(run_id: str, kind: str, payload: dict[str, Any]) -> int:
    """Persist one progress event; returns its monotonic sequence number."""
    with _LOCK:
        cursor = _connection().execute(
            "INSERT INTO run_events (run_id, created_at, kind, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, _now(), kind, json.dumps(payload, separators=(",", ":"))),
        )
        _connection().commit()
        return int(cursor.lastrowid or 0)


def list_events(run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    rows = _connection().execute(
        """SELECT seq, created_at, kind, payload_json FROM run_events
           WHERE run_id = ? AND seq > ? ORDER BY seq""",
        (run_id, after_seq),
    ).fetchall()
    return [
        {
            "seq": row["seq"],
            "created_at": row["created_at"],
            "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def _run_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["is_demo"] = bool(data.get("is_demo"))
    if "response_json" in data:
        raw = data.pop("response_json")
        data["response"] = json.loads(raw) if raw else None
    if "request_json" in data:
        data["request"] = json.loads(data.pop("request_json"))
    return data


def db_path() -> Path:
    return _DB_PATH
