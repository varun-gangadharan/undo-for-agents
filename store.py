"""Shared timeline storage between the MCP server and the web UI.

The MCP server (driven by an external agent) writes sessions, steps and rewinds here;
the Streamlit "Agent sessions" page reads them live and queues rewind commands that the
server picks up. SQLite for local use; keep all access behind Store so a hosted
deployment can swap in Postgres without touching callers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from contracts import Step

DEFAULT_PATH = Path.home() / ".undo-for-agents" / "undo.db"
LIVE_HEARTBEAT_S = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT 'local',
    agent TEXT NOT NULL,
    engine TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'live',
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    ended_at REAL
);
CREATE TABLE IF NOT EXISTS steps (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    command TEXT NOT NULL,
    stdout TEXT NOT NULL,
    stderr TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    fork_s REAL NOT NULL,
    file_hashes TEXT,
    added TEXT NOT NULL,
    removed TEXT NOT NULL,
    modified TEXT NOT NULL,
    PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS rewinds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    before_step INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    undone TEXT NOT NULL,
    restored TEXT NOT NULL,
    removed TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    step INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at REAL NOT NULL,
    done_at REAL
);
"""


class Store:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path or os.getenv("UNDO_DB") or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            if "removed" not in {r["name"] for r in db.execute("PRAGMA table_info(rewinds)")}:
                db.execute("ALTER TABLE rewinds ADD COLUMN removed TEXT NOT NULL DEFAULT '[]'")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per call keeps this safe across threads and processes.
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_session(self, agent: str, engine: str) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._conn() as db:
            db.execute(
                "INSERT INTO sessions (id, agent, engine, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                (session_id, agent, engine, now, now),
            )
        return session_id

    def heartbeat(self, session_id: str) -> None:
        with self._conn() as db:
            db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (time.time(), session_id))

    def end_session(self, session_id: str) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE sessions SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'live'",
                (time.time(), session_id),
            )
            db.execute(
                "UPDATE commands SET status = 'failed', result = ?, done_at = ? "
                "WHERE session_id = ? AND status = 'pending'",
                (json.dumps({"error": "The agent session ended."}), time.time(), session_id),
            )

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM steps WHERE session_id = s.id) AS step_count "
                "FROM sessions s ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._session_dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._conn() as db:
            row = db.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM steps WHERE session_id = s.id) AS step_count "
                "FROM sessions s WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._session_dict(row) if row else None

    def delete_ended_sessions(self) -> int:
        with self._conn() as db:
            ids = [r["id"] for r in db.execute("SELECT * FROM sessions").fetchall()
                   if not self._session_dict(r)["live"]]
            db.executemany("DELETE FROM sessions WHERE id = ?", [(i,) for i in ids])
        return len(ids)

    def save_steps(self, session_id: str, steps: list[Step]) -> None:
        """Replace the session's current steps with the engine's list."""
        with self._conn() as db:
            db.execute("DELETE FROM steps WHERE session_id = ?", (session_id,))
            db.executemany(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        session_id, s.index, s.command, s.stdout, s.stderr, s.exit_code,
                        s.duration_s, s.fork_s,
                        json.dumps(s.file_hashes) if s.file_hashes is not None else None,
                        json.dumps(s.added), json.dumps(s.removed), json.dumps(s.modified),
                    )
                    for s in steps
                ],
            )
            db.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (time.time(), session_id))

    def get_steps(self, session_id: str) -> list[Step]:
        with self._conn() as db:
            rows = db.execute("SELECT * FROM steps WHERE session_id = ? ORDER BY idx", (session_id,)).fetchall()
        return [
            Step(
                index=r["idx"], command=r["command"], stdout=r["stdout"], stderr=r["stderr"],
                exit_code=r["exit_code"], duration_s=r["duration_s"], fork_s=r["fork_s"],
                file_hashes=json.loads(r["file_hashes"]) if r["file_hashes"] else None,
                added=json.loads(r["added"]), removed=json.loads(r["removed"]),
                modified=json.loads(r["modified"]),
            )
            for r in rows
        ]

    def add_rewind(
        self, session_id: str, before_step: int, reason: str, source: str,
        undone: list[str], restored: list[str], removed: list[str],
    ) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO rewinds (session_id, before_step, reason, source, undone, restored, removed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, before_step, reason, source, json.dumps(undone), json.dumps(restored),
                 json.dumps(removed), time.time()),
            )

    def get_rewinds(self, session_id: str) -> list[dict]:
        with self._conn() as db:
            rows = db.execute("SELECT * FROM rewinds WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        return [
            {
                "before_step": r["before_step"], "reason": r["reason"], "source": r["source"],
                "undone": json.loads(r["undone"]), "restored": json.loads(r["restored"]),
                "removed": json.loads(r["removed"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def request_rewind(self, session_id: str, step: int, reason: str = "") -> int:
        with self._conn() as db:
            cur = db.execute(
                "INSERT INTO commands (session_id, kind, step, reason, created_at) VALUES (?, 'rewind', ?, ?, ?)",
                (session_id, step, reason, time.time()),
            )
            return cur.lastrowid

    def pending_commands(self, session_id: str) -> list[dict]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT * FROM commands WHERE session_id = ? AND status = 'pending' ORDER BY id", (session_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def finish_command(self, command_id: int, ok: bool, result: dict[str, Any]) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE commands SET status = ?, result = ?, done_at = ? WHERE id = ?",
                ("done" if ok else "failed", json.dumps(result), time.time(), command_id),
            )

    def get_command(self, command_id: int) -> Optional[dict]:
        with self._conn() as db:
            row = db.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        command = dict(row)
        command["result"] = json.loads(command["result"]) if command["result"] else None
        return command

    @staticmethod
    def _session_dict(row: sqlite3.Row) -> dict:
        session = dict(row)
        session["live"] = session["status"] == "live" and time.time() - session["last_seen"] < LIVE_HEARTBEAT_S
        return session
