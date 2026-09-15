from __future__ import annotations

import json
import os
import shutil
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class TaskLeaseError(RuntimeError):
    pass


@dataclass(slots=True)
class LeaseInfo:
    task_id: str
    pid: int
    host: str
    token: str
    acquired_at: str


class TaskLease:
    """Cross-process best-effort lease for one task.

    Acquisition uses atomic directory creation, so two local workers cannot own
    the same task simultaneously. If a process crashes, a lease from the same
    host is reclaimed only when its PID is no longer alive. Foreign-host leases
    are reclaimed only after the configured stale timeout.
    """

    def __init__(self, root: str | Path, task_id: str, *, stale_after_seconds: int = 43200):
        self.root = Path(root).resolve()
        self.task_id = task_id
        self.stale_after_seconds = stale_after_seconds
        self.lock_dir = self.root / ".agent-system" / "locks" / f"{task_id}.lock"
        self.info_path = self.lock_dir / "lease.json"
        self.token = uuid.uuid4().hex
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self._owned = False

    def __enter__(self) -> "TaskLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def acquire(self) -> None:
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.lock_dir.mkdir()
            except FileExistsError:
                if self._reclaim_if_stale():
                    continue
                owner = self._read_owner()
                detail = "unknown owner"
                if owner:
                    detail = f"pid={owner.get('pid')} host={owner.get('host')} acquired_at={owner.get('acquired_at')}"
                raise TaskLeaseError(f"Task {self.task_id} is already running ({detail})")
            info = LeaseInfo(
                task_id=self.task_id,
                pid=self.pid,
                host=self.host,
                token=self.token,
                acquired_at=datetime.now(timezone.utc).isoformat(),
            )
            tmp = self.lock_dir / f"lease.{self.token}.tmp"
            tmp.write_text(json.dumps(asdict(info), indent=2))
            os.replace(tmp, self.info_path)
            self._owned = True
            return
        raise TaskLeaseError(f"Could not acquire task lease for {self.task_id}")

    def release(self) -> None:
        if not self._owned:
            return
        owner = self._read_owner()
        if owner and owner.get("token") == self.token:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
        self._owned = False

    def _read_owner(self) -> dict | None:
        try:
            value = json.loads(self.info_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _reclaim_if_stale(self) -> bool:
        owner = self._read_owner()
        if not owner:
            if self._lock_age() >= self.stale_after_seconds:
                shutil.rmtree(self.lock_dir, ignore_errors=True)
                return True
            return False

        owner_host = str(owner.get("host", ""))
        try:
            owner_pid = int(owner.get("pid", 0))
        except (TypeError, ValueError):
            owner_pid = 0

        if owner_host == self.host and owner_pid > 0 and not self._pid_alive(owner_pid):
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            return True
        if owner_host != self.host and self._lock_age() >= self.stale_after_seconds:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            return True
        return False

    def _lock_age(self) -> float:
        try:
            return max(0.0, time.time() - self.lock_dir.stat().st_mtime)
        except OSError:
            return 0.0

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
