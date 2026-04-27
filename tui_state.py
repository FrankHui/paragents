from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from task import Task


@dataclass
class TaskView:
    task_id: str
    task_ref: str
    status: str
    retries: int
    updated_at: float
    latest_log: str
    key_logs: list[str]
    pending_approval_request_id: str


@dataclass
class RuntimeSummary:
    in_flight: int
    pending: int
    pending_approvals: int


TaskHighlight = Literal["normal", "approval_pause", "paused", "failed"]


def build_task_views(tasks: dict[str, Task], logs_by_task: dict[str, list[str]]) -> list[TaskView]:
    views: list[TaskView] = []
    for task in tasks.values():
        logs = logs_by_task.get(task.task_id, [])
        latest = logs[-1] if logs else ""
        views.append(
            TaskView(
                task_id=task.task_id,
                task_ref=str(getattr(task, "task_ref", task.task_id[:6])),
                status=task.status,
                retries=task.retries,
                updated_at=task.updated_at,
                latest_log=latest,
                key_logs=_extract_key_logs(logs),
                pending_approval_request_id=str(task.local_state.get("pending_approval_request_id", "")),
            )
        )
    views.sort(key=lambda v: v.updated_at, reverse=True)
    return views


def _extract_key_logs(logs: list[str]) -> list[str]:
    if not logs:
        return []
    keywords = (
        "submitted",
        "started",
        "agent started",
        "llm infer",
        "tool call",
        "tool observation received",
        "waiting for approval",
        "approval",
        "resumed",
        "failed",
        "completed",
        "finished successfully",
        "task completed",
        "cancelled",
    )
    key_logs = [line for line in logs if any(key in line.lower() for key in keywords)]
    return key_logs if key_logs else logs


def summarize_runtime(tasks: dict[str, Task], pending_approvals: int) -> RuntimeSummary:
    in_flight = sum(1 for t in tasks.values() if t.status == "running")
    pending = sum(1 for t in tasks.values() if t.status == "pending")
    return RuntimeSummary(in_flight=in_flight, pending=pending, pending_approvals=pending_approvals)


def normalize_tui_command(cmd: str) -> str:
    cmd = cmd.strip()
    if not cmd:
        return ""
    if cmd.startswith(":s "):
        return "run " + cmd[3:].strip()
    if cmd.startswith(":sv "):
        return "run " + cmd[4:].strip()
    if cmd == ":a":
        return "approvals"
    if cmd.startswith("/"):
        return cmd[1:].strip()
    if cmd.startswith("submit "):
        return cmd
    return "run " + cmd


def render_task_list(views: list[TaskView]) -> str:
    if not views:
        return "(no tasks)"
    lines = []
    for v in views[:20]:
        task_ref = getattr(v, "task_ref", v.task_id[:6])
        lines.append(f"{task_ref}  {v.status:<10} r={v.retries}")
    return "\n".join(lines)


def render_progress(views: list[TaskView], selected_task_id: str | None) -> str:
    if not views:
        return "No task selected."
    selected = next((v for v in views if v.task_id == selected_task_id), views[0])
    if selected.status == "completed":
        pct = 100
    elif selected.status == "failed":
        pct = 100
    elif selected.status == "running":
        pct = 60
    elif selected.status == "paused":
        pct = 40
    else:
        pct = 10
    bar_len = 20
    fill = int(bar_len * pct / 100)
    bar = "#" * fill + "-" * (bar_len - fill)
    selected_ref = getattr(selected, "task_ref", selected.task_id[:6])
    return f"{selected_ref} [{bar}] {pct}%\nstatus={selected.status}\n{selected.latest_log}"


def render_progress_blocks(views: list[TaskView], max_blocks: int = 4) -> str:
    active = [v for v in views if v.status in {"running", "pending", "paused", "failed"}]
    if not active:
        if not views:
            return "No task selected."
        return render_progress(views, selected_task_id=None)

    blocks: list[str] = []
    for v in active[:max_blocks]:
        highlight = classify_task_highlight(v)
        if v.status == "running":
            pct = 60
        elif v.status == "paused":
            pct = 40
        elif v.status == "failed":
            pct = 100
        else:
            pct = 10
        bar_len = 12
        fill = int(bar_len * pct / 100)
        bar = "#" * fill + "-" * (bar_len - fill)
        latest = (v.latest_log or "").strip()
        if len(latest) > 36:
            latest = latest[:36] + "..."
        if highlight == "approval_pause":
            marker = "[! APPROVAL]"
        elif highlight == "paused":
            marker = "[! PAUSED]"
        elif highlight == "failed":
            marker = "[X FAILED]"
        else:
            marker = "[RUNNING]"
        task_ref = getattr(v, "task_ref", v.task_id[:6])
        blocks.append(f"{marker} {task_ref} [{bar}] {pct}%\nstatus={v.status}\n{latest}")

    more = len(active) - len(blocks)
    if more > 0:
        blocks.append(f"... and {more} more active tasks")
    return "\n\n".join(blocks)


def classify_task_highlight(view: TaskView) -> TaskHighlight:
    if view.status == "failed":
        return "failed"
    if view.status == "paused":
        if view.pending_approval_request_id:
            return "approval_pause"
        return "paused"
    return "normal"
