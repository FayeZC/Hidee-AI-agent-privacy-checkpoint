"""SQLite persistence for agents, actions, grants, disclosures, and the audit log."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, kind TEXT NOT NULL, recipient TEXT NOT NULL,
  fields TEXT NOT NULL, minimal TEXT, status TEXT NOT NULL, reason TEXT NOT NULL, marks TEXT NOT NULL,
  decided_by TEXT, executed TEXT, created_at REAL NOT NULL, decided_at REAL, expires_at REAL);
CREATE TABLE IF NOT EXISTS grants (
  id TEXT PRIMARY KEY, action_id TEXT NOT NULL, version TEXT NOT NULL, payload_digest TEXT NOT NULL,
  expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS disclosures (
  id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL, recipient TEXT NOT NULL,
  category TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, event TEXT NOT NULL, action_id TEXT,
  detail TEXT NOT NULL);
"""
JSON_COLUMNS = ("fields", "minimal", "marks", "executed")


@dataclass(frozen=True)
class ActionRecord:
    id: str
    agent_id: str
    kind: str
    recipient: str
    fields: dict[str, dict[str, str]]
    minimal: dict[str, dict[str, str]] | None
    status: str
    reason: str
    marks: dict[str, str]
    decided_by: str | None
    executed: dict[str, str] | None
    created_at: float
    decided_at: float | None
    expires_at: float | None


def _encode(column: str, value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if column in JSON_COLUMNS and value is not None else value


def _record(row: sqlite3.Row) -> ActionRecord:
    data = dict(row)
    for column in JSON_COLUMNS:
        if data[column] is not None:
            data[column] = json.loads(data[column])
    return ActionRecord(**data)


class _Result:
    """The rows and row count of one statement, read while the store's lock was held."""

    def __init__(self, rows: list, rowcount: int) -> None:
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list:
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Store:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self._db.executescript(SCHEMA)

    def clear(self) -> None:
        """Remove every action, approval, disclosure, and audit event (used to restart a demo)."""
        with self.lock:
            for table in ("actions", "grants", "disclosures", "audit"):
                self._db.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        self._db.close()

    def _exec(self, sql: str, params=()) -> "_Result":
        # Fetch inside the lock: the connection is shared by every thread, and a cursor read after another thread
        # has run a statement can come back empty or wrong (a waiting session once crashed on an empty row).
        with self.lock:
            cursor = self._db.execute(sql, params)
            return _Result(cursor.fetchall(), cursor.rowcount)

    # Agents
    def add_agent(self, agent_id: str, name: str, token_hash: str, now: float) -> None:
        self._exec("INSERT OR REPLACE INTO agents (id, name, token_hash, created_at, revoked) VALUES (?,?,?,?,0)",
                   (agent_id, name, token_hash, now))

    def agent_by_token_hash(self, token_hash: str) -> dict | None:
        row = self._exec("SELECT id, name FROM agents WHERE token_hash=? AND revoked=0", (token_hash,)).fetchone()
        return dict(row) if row else None

    def revoke_agent(self, agent_id: str) -> None:
        self._exec("UPDATE agents SET revoked=1 WHERE id=?", (agent_id,))

    def list_agents(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT id, name, created_at, revoked FROM agents ORDER BY created_at")]

    # Actions
    def insert_action(self, rec: ActionRecord) -> None:
        values = {k: _encode(k, v) for k, v in asdict(rec).items()}
        cols = list(values)
        self._exec(f"INSERT INTO actions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [values[c] for c in cols])

    def update_action(self, action_id: str, **changes: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in changes)
        self._exec(f"UPDATE actions SET {sets} WHERE id=?", [*(_encode(k, v) for k, v in changes.items()), action_id])

    def get_action(self, action_id: str) -> ActionRecord | None:
        row = self._exec("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return _record(row) if row else None

    def list_actions(self, status: str | None = None, limit: int = 200) -> list[ActionRecord]:
        if status:
            rows = self._exec("SELECT * FROM actions WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            rows = self._exec("SELECT * FROM actions ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_record(r) for r in rows.fetchall()]

    def expired_waiting_ids(self, now: float) -> list[str]:
        rows = self._exec("SELECT id FROM actions WHERE status='waiting' AND expires_at<=? ORDER BY created_at", (now,))
        return [r["id"] for r in rows.fetchall()]

    # Grants
    def insert_grant(self, grant_id: str, action_id: str, version: str, payload_digest: str, expires_at: float) -> None:
        self._exec("INSERT INTO grants (id, action_id, version, payload_digest, expires_at) VALUES (?,?,?,?,?)",
                   (grant_id, action_id, version, payload_digest, expires_at))

    def get_grant(self, grant_id: str) -> dict | None:
        row = self._exec("SELECT * FROM grants WHERE id=?", (grant_id,)).fetchone()
        return dict(row) if row else None

    def grants_for(self, action_id: str) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM grants WHERE action_id=?", (action_id,)).fetchall()]

    def use_grant(self, grant_id: str) -> bool:
        return self._exec("UPDATE grants SET used=1 WHERE id=? AND used=0", (grant_id,)).rowcount == 1

    # Disclosures
    def add_disclosures(self, action_id: str, recipient: str, categories: list[str], now: float) -> None:
        with self.lock:
            for category in categories:
                self._db.execute("INSERT INTO disclosures (action_id, recipient, category, at) VALUES (?,?,?,?)",
                                 (action_id, recipient, category, now))

    def disclosure_counts(self) -> list[tuple[str, str, int]]:
        rows = self._exec("SELECT recipient, category, COUNT(*) AS n FROM disclosures "
                          "GROUP BY recipient, category ORDER BY recipient, category")
        return [(r["recipient"], r["category"], r["n"]) for r in rows.fetchall()]

    def disclosure_summary(self) -> list[dict]:
        """Per recipient and category: how often it was sent, when last, and by which step."""
        rows = self._exec("SELECT recipient, category, COUNT(*) AS n, MAX(at) AS last_at, action_id AS last_action "
                          "FROM disclosures GROUP BY recipient, category ORDER BY recipient, category")
        return [dict(r) for r in rows.fetchall()]   # SQLite takes the bare action_id from the MAX(at) row

    # Audit (metadata only; never payload values or secrets)
    def audit(self, event: str, action_id: str | None, detail: dict, now: float) -> None:
        self._exec("INSERT INTO audit (at, event, action_id, detail) VALUES (?,?,?,?)",
                   (now, event, action_id, json.dumps(detail, ensure_ascii=False)))

    def audit_for(self, action_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM audit WHERE action_id=? ORDER BY id", (action_id,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def audit_log(self, limit: int = 500) -> list[dict]:
        rows = self._exec("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
