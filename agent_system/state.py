from __future__ import annotations

from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from .models import TaskStatus


class IntegrationPolicyError(RuntimeError):
    pass


class StateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, goal TEXT NOT NULL,
                    status TEXT NOT NULL, risk TEXT NOT NULL, branch TEXT, worktree TEXT,
                    provider_id TEXT, repair_attempts INTEGER DEFAULT 0,
                    max_free_attempts INTEGER DEFAULT 2, max_total_repair_rounds INTEGER DEFAULT 3,
                    production_target INTEGER DEFAULT 1, pr_number INTEGER, pr_url TEXT,
                    merge_sha TEXT, blocker TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
            """)
            existing_cols = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
            for col, col_type in [
                ("production_target", "INTEGER DEFAULT 1"),
                ("pr_number", "INTEGER"),
                ("pr_url", "TEXT"),
                ("merge_sha", "TEXT"),
                ("blocker", "TEXT"),
            ]:
                if col not in existing_cols:
                    con.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_type}")

            con.execute("""
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, state_before TEXT, state_after TEXT,
                    provider_id TEXT, action TEXT NOT NULL, result TEXT, duration_ms INTEGER,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL,
                    depends_on_task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, depends_on_task_id),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id) ON DELETE RESTRICT
                )
            """)
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_dependencies_parent "
                "ON task_dependencies(depends_on_task_id)"
            )

    def next_task_id(self) -> str:
        with self._connect() as con:
            row = con.execute("SELECT id FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:
            return "TASK-0001"
        return f"TASK-{int(row[0].split('-')[-1]) + 1:04d}"

    def create_task(self, task_id: str, project: str, goal: str, risk: str, max_free_attempts: int = 2, max_total_repair_rounds: int = 3, production_target: bool = True) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """INSERT INTO tasks (id, project, goal, status, risk, max_free_attempts, max_total_repair_rounds, production_target, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, project, goal, TaskStatus.NEW.value, risk, max_free_attempts, max_total_repair_rounds, 1 if production_target else 0, now, now),
            )

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        branch: str | None = None,
        worktree: str | None = None,
        provider_id: str | None = None,
        repair_attempts: int | None = None,
        production_target: bool | None = None,
        pr_number: int | None = None,
        pr_url: str | None = None,
        merge_sha: str | None = None,
        blocker: str | None = None,
        force_completed: bool = False,
    ) -> None:
        if status == TaskStatus.COMPLETED.value and not force_completed:
            current = self.get_task(task_id)
            if current and current.get("production_target", 1):
                forbidden_predecessors = {
                    TaskStatus.READY_FOR_REVIEW.value,
                    TaskStatus.READY_FOR_PR.value,
                    TaskStatus.PR_READY.value,
                    TaskStatus.PR_OPEN.value,
                    TaskStatus.CI_PENDING.value,
                    TaskStatus.CI_FAILED.value,
                    TaskStatus.WAITING_HUMAN.value,
                    TaskStatus.REVIEWING.value,
                    TaskStatus.VERIFYING.value,
                    TaskStatus.IMPLEMENTING.value,
                    TaskStatus.PLANNED.value,
                    TaskStatus.NEW.value,
                }
                if current.get("status") in forbidden_predecessors:
                    raise IntegrationPolicyError(
                        f"Production-targeted task {task_id} cannot transition directly from {current['status']} to COMPLETED. "
                        "Integration requires PR -> CI -> guarded merge -> main confirmation."
                    )
                # Check that merge SHA or integration receipt exists
                effective_merge_sha = merge_sha or current.get("merge_sha")
                receipt_path = self.db_path.parent.parent / "runs" / task_id / "integration_receipt.json"
                if not effective_merge_sha and not receipt_path.exists():
                    raise IntegrationPolicyError(
                        f"Production-targeted task {task_id} cannot be marked COMPLETED without confirmed merge SHA and integration receipt."
                    )

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
        if production_target is not None:
            fields.append("production_target = ?"); values.append(1 if production_target else 0)
        if pr_number is not None:
            fields.append("pr_number = ?"); values.append(pr_number)
        if pr_url is not None:
            fields.append("pr_url = ?"); values.append(pr_url)
        if merge_sha is not None:
            fields.append("merge_sha = ?"); values.append(merge_sha)
        if blocker is not None:
            fields.append("blocker = ?"); values.append(blocker)
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

    def add_dependency(self, task_id: str, depends_on_task_id: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO task_dependencies "
                "(task_id, depends_on_task_id, created_at) VALUES (?, ?, ?)",
                (task_id, depends_on_task_id, datetime.now(timezone.utc).isoformat()),
            )

    def remove_dependency(self, task_id: str, depends_on_task_id: str) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
                (task_id, depends_on_task_id),
            )

    def get_dependencies(self, task_id: str) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT depends_on_task_id FROM task_dependencies "
                "WHERE task_id = ? ORDER BY created_at, depends_on_task_id",
                (task_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def get_dependents(self, task_id: str) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT task_id FROM task_dependencies "
                "WHERE depends_on_task_id = ? ORDER BY created_at, task_id",
                (task_id,),
            ).fetchall()
        return [row[0] for row in rows]

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
