from __future__ import annotations

from task import Task
from tui_state import TaskView, build_task_views, classify_task_highlight, render_progress_blocks, summarize_runtime


def test_build_task_views_sorted_by_updated_desc() -> None:
    t1 = Task(task_id="a", input="x", status="running")
    t2 = Task(task_id="b", input="y", status="paused")
    t1.updated_at = 10
    t2.updated_at = 20
    views = build_task_views({"a": t1, "b": t2}, logs_by_task={"a": ["l1"], "b": ["l2", "l3"]})
    assert [v.task_id for v in views] == ["b", "a"]
    assert views[0].latest_log == "l3"


def test_summarize_runtime_counts() -> None:
    tasks = {
        "a": Task(task_id="a", input="x", status="running"),
        "b": Task(task_id="b", input="y", status="pending"),
        "c": Task(task_id="c", input="z", status="completed"),
    }
    summary = summarize_runtime(tasks, pending_approvals=2)
    assert summary.in_flight == 1
    assert summary.pending == 1
    assert summary.pending_approvals == 2


def test_render_progress_blocks_for_multiple_active_tasks() -> None:
    views = [
        TaskView(
            task_id="aaaaaa11",
            task_ref="aaaaaa",
            status="running",
            retries=0,
            updated_at=3,
            latest_log="step=1",
            key_logs=["step=1"],
            pending_approval_request_id="",
        ),
        TaskView(
            task_id="bbbbbb22",
            task_ref="bbbbbb",
            status="paused",
            retries=1,
            updated_at=2,
            latest_log="waiting",
            key_logs=["waiting"],
            pending_approval_request_id="req-1",
        ),
        TaskView(
            task_id="cccccc33",
            task_ref="cccccc",
            status="pending",
            retries=0,
            updated_at=1,
            latest_log="queued",
            key_logs=["queued"],
            pending_approval_request_id="",
        ),
    ]
    text = render_progress_blocks(views, max_blocks=4)
    assert "[RUNNING] aaaaaa" in text
    assert "[! APPROVAL] bbbbbb" in text
    assert "[RUNNING] cccccc" in text
    assert text.count("status=") == 3


def test_classify_task_highlight_variants() -> None:
    approval_paused = TaskView("t1", "t1", "paused", 0, 1, "", [""], "req-1")
    normal_paused = TaskView("t2", "t2", "paused", 0, 1, "", [""], "")
    failed = TaskView("t3", "t3", "failed", 0, 1, "", [""], "")
    running = TaskView("t4", "t4", "running", 0, 1, "", [""], "")
    assert classify_task_highlight(approval_paused) == "approval_pause"
    assert classify_task_highlight(normal_paused) == "paused"
    assert classify_task_highlight(failed) == "failed"
    assert classify_task_highlight(running) == "normal"
