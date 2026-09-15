from agent_system.state import StateStore


def test_state_persists(tmp_path):
    db = tmp_path / "state.db"
    store = StateStore(db)
    tid = store.next_task_id()
    assert tid == "TASK-0001"
    store.create_task(tid, "demo", "goal", "medium")
    assert store.get_task(tid)["status"] == "NEW"
    store.update_task(tid, status="PLANNED", branch="agent/task-0001", worktree="/tmp/wt")
    row = store.get_task(tid)
    assert row["status"] == "PLANNED"
    assert row["branch"] == "agent/task-0001"
