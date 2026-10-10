"""SQLite storage: long-term memory, tasks, reminders, notes and conversations.

Everything lives in one file under DATA_DIR so a backup is a single copy.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import settings

_lock = threading.Lock()
_conn = sqlite3.connect(settings.db_path, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    importance INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, category, content='memories', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, category) VALUES (new.id, new.content, new.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category)
    VALUES ('delete', old.id, old.content, old.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, category)
    VALUES ('delete', old.id, old.content, old.category);
    INSERT INTO memories_fts(rowid, content, category) VALUES (new.id, new.content, new.category);
END;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    due TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, content='notes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    messages TEXT NOT NULL DEFAULT '[]',
    transcript TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

with _lock:
    _conn.executescript(SCHEMA)
    _conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def _exec(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.lastrowid or cur.rowcount


def _fts_query(text: str) -> str:
    """Turn free text into a forgiving FTS5 OR-query of prefix terms."""
    words = [w for w in re.findall(r"[\w']+", text.lower()) if len(w) > 2]
    return " OR ".join(f'"{w}"*' for w in words[:24])


# ---------------------------------------------------------------- memories

def add_memory(content: str, category: str = "fact", importance: int = 3) -> int:
    ts = now_iso()
    importance = max(1, min(5, int(importance)))
    return _exec(
        "INSERT INTO memories(content, category, importance, created_at, updated_at) VALUES (?,?,?,?,?)",
        (content.strip(), category.strip().lower() or "fact", importance, ts, ts),
    )


def update_memory(memory_id: int, content: str) -> bool:
    return _exec(
        "UPDATE memories SET content=?, updated_at=? WHERE id=?", (content.strip(), now_iso(), memory_id)
    ) > 0


def delete_memory(memory_id: int) -> bool:
    return _exec("DELETE FROM memories WHERE id=?", (memory_id,)) > 0


def search_memories(query: str, limit: int = 8) -> list[dict[str, Any]]:
    q = _fts_query(query)
    if not q:
        return []
    return _rows(
        """SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid
           WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) - m.importance * 0.5 LIMIT ?""",
        (q, limit),
    )


def core_memories(limit: int = 25) -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM memories WHERE importance >= 4 ORDER BY importance DESC, updated_at DESC LIMIT ?",
        (limit,),
    )


def recent_memories(limit: int = 30) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,))


def memory_count() -> int:
    return _rows("SELECT COUNT(*) AS n FROM memories")[0]["n"]


# ------------------------------------------------------------------- tasks

def add_task(title: str, notes: str = "", priority: str = "normal", due: str | None = None) -> int:
    return _exec(
        "INSERT INTO tasks(title, notes, priority, due, created_at) VALUES (?,?,?,?,?)",
        (title.strip(), notes, priority, due, now_iso()),
    )


def list_tasks(status: str = "open") -> list[dict[str, Any]]:
    order = "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, due IS NULL, due, id"
    if status == "all":
        return _rows(f"SELECT * FROM tasks ORDER BY status DESC, {order}")
    return _rows(f"SELECT * FROM tasks WHERE status=? ORDER BY {order}", (status,))


def complete_task(task_id: int) -> bool:
    return _exec(
        "UPDATE tasks SET status='done', completed_at=? WHERE id=? AND status='open'", (now_iso(), task_id)
    ) > 0


def delete_task(task_id: int) -> bool:
    return _exec("DELETE FROM tasks WHERE id=?", (task_id,)) > 0


# --------------------------------------------------------------- reminders

def add_reminder(text: str, due_at: str) -> int:
    return _exec("INSERT INTO reminders(text, due_at, created_at) VALUES (?,?,?)", (text.strip(), due_at, now_iso()))


def list_reminders(status: str = "pending") -> list[dict[str, Any]]:
    return _rows("SELECT * FROM reminders WHERE status=? ORDER BY due_at", (status,))


def due_reminders(now: str) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM reminders WHERE status='pending' AND due_at <= ? ORDER BY due_at", (now,))


def set_reminder_status(reminder_id: int, status: str) -> bool:
    return _exec("UPDATE reminders SET status=? WHERE id=?", (status, reminder_id)) > 0


# ------------------------------------------------------------------- notes

def add_note(title: str, body: str) -> int:
    return _exec("INSERT INTO notes(title, body, created_at) VALUES (?,?,?)", (title.strip(), body, now_iso()))


def search_notes(query: str, limit: int = 5) -> list[dict[str, Any]]:
    q = _fts_query(query)
    if not q:
        return _rows("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,))
    return _rows(
        """SELECT n.* FROM notes_fts f JOIN notes n ON n.id = f.rowid
           WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?""",
        (q, limit),
    )


def recent_notes(limit: int = 10) -> list[dict[str, Any]]:
    return _rows("SELECT id, title, created_at FROM notes ORDER BY id DESC LIMIT ?", (limit,))


# ----------------------------------------------------------- conversations

def latest_conversation() -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM conversations ORDER BY id DESC LIMIT 1")
    if not rows:
        return None
    row = rows[0]
    row["messages"] = json.loads(row["messages"])
    row["transcript"] = json.loads(row["transcript"])
    return row


def new_conversation() -> int:
    ts = now_iso()
    return _exec("INSERT INTO conversations(created_at, updated_at) VALUES (?,?)", (ts, ts))


def save_conversation(conv_id: int, messages: list, transcript: list, title: str = "") -> None:
    _exec(
        "UPDATE conversations SET messages=?, transcript=?, title=COALESCE(NULLIF(?, ''), title), updated_at=? WHERE id=?",
        (json.dumps(messages), json.dumps(transcript), title, now_iso(), conv_id),
    )
