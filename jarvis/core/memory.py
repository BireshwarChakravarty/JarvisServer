"""Long-term memory: facts about you, conversation transcripts, session summaries,
presence log and small key/value state — all in one local SQLite file."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started REAL NOT NULL,
    ended REAL,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ts REAL NOT NULL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_transcript_session ON transcript(session);
CREATE INDEX IF NOT EXISTS idx_log_kind ON log(kind, ts);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, category, content='facts', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text, category) VALUES (new.id, new.text, new.category);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text, category) VALUES('delete', old.id, old.text, old.category);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text, category) VALUES('delete', old.id, old.text, old.category);
  INSERT INTO facts_fts(rowid, text, category) VALUES (new.id, new.text, new.category);
END;
"""


class Memory:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        try:
            self.db.executescript(FTS)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()

    # ── facts ────────────────────────────────────────────────────────────
    def remember(self, text: str, category: str = "general") -> int:
        text = text.strip()
        now = time.time()
        with self._lock:
            # de-duplicate near-identical facts
            row = self.db.execute("SELECT id FROM facts WHERE lower(text)=lower(?)", (text,)).fetchone()
            if row:
                self.db.execute("UPDATE facts SET updated=? WHERE id=?", (now, row["id"]))
                self.db.commit()
                return int(row["id"])
            cur = self.db.execute(
                "INSERT INTO facts(text, category, created, updated) VALUES (?,?,?,?)",
                (text, category, now, now),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def forget(self, fact_id: int | None = None, matching: str | None = None) -> int:
        with self._lock:
            if fact_id is not None:
                cur = self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            elif matching:
                cur = self.db.execute("DELETE FROM facts WHERE text LIKE ?", (f"%{matching}%",))
            else:
                return 0
            self.db.commit()
            return cur.rowcount

    def recall(self, query: str, limit: int = 8) -> list[dict]:
        with self._lock:
            rows: list[sqlite3.Row] = []
            if self.fts and query.strip():
                terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
                if terms:
                    match = " OR ".join(f'"{t}"*' for t in terms)
                    try:
                        rows = self.db.execute(
                            "SELECT f.id, f.text, f.category, f.updated FROM facts_fts "
                            "JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? "
                            "ORDER BY rank LIMIT ?",
                            (match, limit),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
            if not rows:
                rows = self.db.execute(
                    "SELECT id, text, category, updated FROM facts WHERE text LIKE ? ORDER BY updated DESC LIMIT ?",
                    (f"%{query}%", limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def all_facts(self, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, text, category, updated FROM facts ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── transcripts & sessions ───────────────────────────────────────────
    def start_session(self, session_id: str) -> None:
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO sessions(id, started) VALUES (?,?)", (session_id, time.time()))
            self.db.commit()

    def end_session(self, session_id: str, summary: str | None) -> None:
        with self._lock:
            self.db.execute("UPDATE sessions SET ended=?, summary=? WHERE id=?", (time.time(), summary, session_id))
            self.db.commit()

    def log_turn(self, session_id: str, role: str, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self.db.execute(
                "INSERT INTO transcript(session, role, text, ts) VALUES (?,?,?,?)",
                (session_id, role, text, time.time()),
            )
            self.db.commit()

    def session_transcript(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT role, text, ts FROM transcript WHERE session=? ORDER BY id", (session_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_summaries(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, started, ended, summary FROM sessions WHERE summary IS NOT NULL AND summary != '' "
                "ORDER BY started DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]

    # ── log & kv ─────────────────────────────────────────────────────────
    def log_event(self, kind: str, /, **data: Any) -> None:
        with self._lock:
            self.db.execute("INSERT INTO log(kind, ts, data) VALUES (?,?,?)", (kind, time.time(), json.dumps(data)))
            self.db.commit()

    def last_event(self, kind: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT ts, data FROM log WHERE kind=? ORDER BY ts DESC LIMIT 1", (kind,)).fetchone()
            if not row:
                return None
            return {"ts": row["ts"], **json.loads(row["data"] or "{}")}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, json.dumps(value)))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()
