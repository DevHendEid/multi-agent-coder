from __future__ import annotations

from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from .models import TaskStatus


class StateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            # WAL allows readers and independent task workers to coexist while
            # SQLite still serializes individual writes safely.
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, goal TEXT NOT NULL,
                    status TEXT NOT NULL, risk TEXT NOT NULL, branch TEXT, worktree TEXT,
                    provider_id TEXT, repair_attempts INTEGER DEFAULT 0,
                    max_free_attempts INTEGER DEFAULT 2, max_total_repair_rounds INTEGER DEFAULT 3,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, state_before TEXT, state_after TEXT,
                    provider_id TEXT, action TEXT NOT NULL, result TEXT, duration_ms INTEGER,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
            """)

    def next_task_id(self) -> str:
        with self._connect() as con:
            row = con.execute("SELECT id FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            return "TASK-0001"
        return f"TASK-{int(row[0].split('-')[-1]) + 1:04d}"

    def create_task(self, task_id: str, project: str, goal: str, risk: str, max_free_attempts: int = 2, max_total_repair_rounds: int = 3) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """INSERT INTO tasks (id, project, goal, status, risk, max_free_attempts, max_total_repair_rounds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, project, goal, TaskStatus.NEW.value, risk, max_free_attempts, max_total_repair_rounds, now, now),
            )

    def update_task(self, task_id: str, *, status: str | None = None, branch: str | None = None, worktree: str | None = None, provider_id: str | None = None, repair_attempts: int | None = None) -> None:
        fields, values = [], []
        if status is not None:
            fields.append("status = ?"); values.append(status)
        if branch is not None:
            fields.append("branch = ?"); values.append(branch)
        if worktree is not None:
            fields.append("worktree = ?"); values.append(worktree)
        if provider_id is not None:
            fields.append("provider_id = ?"); values.append(provider_id)
        if repair_attempts is not None:
            fields.append("repair_attempts = ?"); values.append(repair_attempts)
        fields.append("updated_at = ?"); values.append(datetime.now(timezone.utc).isoformat())
        values.append(task_id)
        with self._connect() as con:
            con.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)

    def increment_repair_attempts(self, task_id: str) -> int:
        with self._connect() as con:
            con.execute("UPDATE tasks SET repair_attempts = repair_attempts + 1, updated_at = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), task_id))
            row = con.execute("SELECT repair_attempts FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row[0] if row else 0

    def get_task(self, task_id: str):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def record_event(self, task_id: str, state_before: str | None, state_after: str, provider_id: str | None, action: str, result: str | None = None, duration_ms: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """INSERT INTO task_events (task_id, timestamp, state_before, state_after, provider_id, action, result, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, now, state_before, state_after, provider_id, action, result, duration_ms),
            )

    def get_events(self, task_id: str):
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return [dict(r) for r in rows]
