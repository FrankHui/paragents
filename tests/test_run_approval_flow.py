from __future__ import annotations

import asyncio

from main import (
    _get_active_approval_request_id,
    _get_task_pending_approval_request_id,
    _handle_run_approval_answer,
    _is_watch_slot_blocking,
    _normalize_user_input,
    _parse_ack_indices,
    _resolve_request_id_from_approve_cmd,
)
from permissions import FsScope, PermissionManager, PermissionsConfig
from scheduler import Scheduler
from task import Task


def _build_manager(tmp_path):
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": True,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        },
        fs_scopes=[FsScope(path=str(tmp_path), read=True, write=True)],
    )
    return PermissionManager(cfg, tmp_path / "permissions.json")


def test_normalize_user_input_preserves_yn() -> None:
    assert _normalize_user_input("y") == "y"
    assert _normalize_user_input("n") == "n"
    assert _normalize_user_input("hello") == "run hello"
    assert _normalize_user_input("/list") == "list"


def test_get_task_pending_approval_request_id() -> None:
    task = Task(task_id="t1", input="x", status="paused")
    task.local_state["pending_approval_request_id"] = "req-1"
    assert _get_task_pending_approval_request_id(task) == "req-1"
    task.status = "running"
    assert _get_task_pending_approval_request_id(task) is None


def test_handle_run_approval_answer_approve_resumes(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager.check_shell_command("python -V")
    request_id = manager.list_pending()[0].request_id

    scheduler = Scheduler(llm_client=None)
    task = Task(task_id="task-123456", input="x", status="paused")
    task.local_state["pending_approval_request_id"] = request_id
    scheduler.tasks[task.task_id] = task

    lines = asyncio.run(_handle_run_approval_answer("y", request_id, manager, scheduler))
    assert any("approved" in line for line in lines)
    assert task.status == "pending"


def test_handle_run_approval_answer_deny(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager.check_shell_command("python -V")
    pending_req = manager.list_pending()[0]
    request_id = pending_req.request_id
    scheduler = Scheduler(llm_client=None)
    lines = asyncio.run(_handle_run_approval_answer("n", request_id, manager, scheduler))
    assert lines == [f"denied {pending_req.request_ref}"]


def test_resolve_request_id_from_approve_cmd_supports_short_prefix() -> None:
    pending_ids = ["9ec5d612-1111-2222-3333-444444444444", "abc12345-1111-2222-3333-444444444444"]
    assert _resolve_request_id_from_approve_cmd("approve 9ec5d6", pending_ids) == pending_ids[0]
    assert _resolve_request_id_from_approve_cmd("approve #2", pending_ids) == pending_ids[1]


def test_parse_ack_indices_supports_slash_batch() -> None:
    assert _parse_ack_indices("1/2") == [1, 2]
    assert _parse_ack_indices("1/3/4") == [1, 3, 4]
    assert _parse_ack_indices("2/2/1") == [2, 1]
    assert _parse_ack_indices("0/1") == []


def test_watch_slot_blocking_only_for_active_status() -> None:
    scheduler = Scheduler(llm_client=None)
    running = Task(task_id="run-1", input="x", status="running")
    completed = Task(task_id="done-1", input="x", status="completed")
    scheduler.tasks[running.task_id] = running
    scheduler.tasks[completed.task_id] = completed
    assert _is_watch_slot_blocking(scheduler, running.task_id) is True
    assert _is_watch_slot_blocking(scheduler, completed.task_id) is False
    assert _is_watch_slot_blocking(scheduler, "missing") is False
    assert _is_watch_slot_blocking(scheduler, None) is False


def test_get_active_approval_request_id_prefers_run_then_watch() -> None:
    scheduler = Scheduler(llm_client=None)
    run_task = Task(task_id="run-1", input="x", status="paused")
    run_task.local_state["pending_approval_request_id"] = "req-run"
    watch_task = Task(task_id="watch-1", input="x", status="paused")
    watch_task.local_state["pending_approval_request_id"] = "req-watch"
    scheduler.tasks[run_task.task_id] = run_task
    scheduler.tasks[watch_task.task_id] = watch_task

    assert _get_active_approval_request_id(scheduler, run_task.task_id, watch_task.task_id) == "req-run"
    assert _get_active_approval_request_id(scheduler, None, watch_task.task_id) == "req-watch"
    assert _get_active_approval_request_id(scheduler, None, None) is None
