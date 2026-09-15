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
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    branch TEXT,
                    worktree TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def next_task_id(self) -> str:
        with self._connect() as con:
            row = con.execute("SELECT id FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            return "TASK-0001"
        n = int(row[0].split("-")[-1]) + 1
        return f"TASK-{n:04d}"

    def create_task(self, task_id: str, project: str, goal: str, risk: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                "INSERT INTO tasks (id, project, goal, status, risk, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, project, goal, TaskStatus.NEW.value, risk, now, now),
            )

    def update_task(self, task_id: str, *, status: str | None = None, branch: str | None = None, worktree: str | None = None) -> None:
        fields, values = [], []
        if status is not None:
            fields.append("status = ?"); values.append(status)
        if branch is not None:
            fields.append("branch = ?"); values.append(branch)
        if worktree is not None:
            fields.append("worktree = ?"); values.append(worktree)
        fields.append("updated_at = ?"); values.append(datetime.now(timezone.utc).isoformat())
        values.append(task_id)
        with self._connect() as con:
            con.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)

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
