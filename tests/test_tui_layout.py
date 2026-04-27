from __future__ import annotations

import asyncio

from task import Task
from tui_app import ParagentsTUI, build_tui_layout


class _FakeScheduler:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self.logs_by_task: dict[str, list[str]] = {}

    def get_task_logs(self, task_id: str, limit: int = 200) -> list[str]:
        return self.logs_by_task.get(task_id, [])[-limit:]


def test_build_tui_layout_has_required_regions() -> None:
    layout = build_tui_layout(submit_count_provider=lambda: 0)
    rendered = str(layout)
    assert "log_panel" in rendered
    assert "submit_panels" in rendered
    assert "command_bar" in rendered


def test_options_overlay_does_not_replace_log_panel_text() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    tui.logs = ["hello"]
    tui.show_options = True
    assert tui._log_panel_text() == "hello"


def test_watch_logs_are_appended_into_unified_stream() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    scheduler.tasks = {"t1": object()}
    scheduler.logs_by_task["t1"] = ["line1", "line2"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    tui.watching_task_id = "t1"
    tui.watch_source = "show"

    panel_text = tui._log_panel_text()
    assert "[watch:t1] line1" in panel_text
    assert "[watch:t1] line2" in panel_text
    assert panel_text.startswith("Paragents TUI ready.")


def test_run_command_sets_watch_task_id() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    scheduler.tasks = {"task-123": object()}
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui._update_watch_state("run hello", ["submitted: task-123 (watching)"])
    assert tui.watching_task_id == "task-123"
    assert tui.watch_source == "run"


def test_submit_does_not_enable_auto_watch() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui._update_watch_state("submit hello", ["submitted: task-321 (quiet)"])
    assert tui.watching_task_id is None
    assert tui.watch_source is None


def test_main_log_highlight_for_approval_and_failed() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    scheduler.tasks = {"task-1": object()}
    scheduler.logs_by_task["task-1"] = [
        "[12:00:00] waiting for approval request_id=abcdef123456",
        "[12:00:01] failed: boom",
    ]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    tui.watching_task_id = "task-1"
    tui.watch_source = "show"
    text = tui._log_panel_text()
    assert "[! APPROVAL]" in text
    assert "[X FAILED]" in text


def test_approval_detail_line_is_before_confirm_prompt() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    scheduler.tasks = {"task-1": object()}
    scheduler.logs_by_task["task-1"] = [
        "[12:00:00] waiting for approval request_id=abcdef123456 detail=type=capability_enable payload={'capability': 'shell'}"
    ]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    tui.show_welcome = False
    tui.watching_task_id = "task-1"
    tui.watch_source = "show"
    _ = tui._log_panel_text()
    detail_idx = next(i for i, line in enumerate(tui.logs) if "type=capability_enable" in line)
    prompt_idx = next(i for i, line in enumerate(tui.logs) if "输入 y/n 确认" in line)
    assert detail_idx < prompt_idx


def test_ack_prefix_auto_popup_and_enter_fill_only() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="aaaaaa111111", input="x", status="completed", task_ref="aa11bb")
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = ["done"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
        ack_pending_task_ids_provider=lambda: [task.task_id],
    )
    tui.show_welcome = False
    tui.input_buffer.text = "/ack"
    tui._refresh_context_popup()
    assert tui.context_popup_mode == "ack"
    assert tui.context_candidates[0][0] == "aa11bb"

    tui._apply_context_selection()
    assert tui.input_buffer.text == "/ack aa11bb"
    assert tui.context_popup_mode is None


def test_show_prefix_popup_contains_running_and_unacked_completed() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    running = Task(task_id="bbbbbb222222", input="x", status="running", task_ref="bb22cc")
    unacked_done = Task(task_id="cccccc333333", input="x", status="completed", task_ref="cc33dd")
    scheduler.tasks = {running.task_id: running, unacked_done.task_id: unacked_done}
    scheduler.logs_by_task[running.task_id] = ["run"]
    scheduler.logs_by_task[unacked_done.task_id] = ["done"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
        show_candidate_task_ids_provider=lambda: [running.task_id, unacked_done.task_id],
    )
    tui.show_welcome = False
    tui.input_buffer.text = "/show"
    tui._refresh_context_popup()
    assert tui.context_popup_mode == "show"
    assert {token for token, _ in tui.context_candidates} == {"bb22cc", "cc33dd"}


def test_context_popup_height_matches_candidates() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    t1 = Task(task_id="t11111111111", input="x", status="running", task_ref="t1a1a1")
    t2 = Task(task_id="t22222222222", input="y", status="running", task_ref="t2b2b2")
    scheduler.tasks = {t1.task_id: t1, t2.task_id: t2}
    scheduler.logs_by_task[t1.task_id] = ["line"]
    scheduler.logs_by_task[t2.task_id] = ["line"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
        show_candidate_task_ids_provider=lambda: [t1.task_id, t2.task_id],
    )
    tui.show_welcome = False
    tui.input_buffer.text = "/show"
    tui._refresh_context_popup()
    _ = tui._popup_text()
    assert tui.parts.popup_float.height == 6


def test_approve_and_deny_prefix_popup_fill_request_ref() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
        approve_candidate_request_refs_provider=lambda: ["a1b2c3"],
        deny_candidate_request_refs_provider=lambda: ["d4e5f6"],
    )
    tui.show_welcome = False

    tui.input_buffer.text = "/approve"
    tui._refresh_context_popup()
    assert tui.context_popup_mode == "approve"
    assert tui.context_candidates == [("a1b2c3", "a1b2c3 [pending approval]")]
    tui._apply_context_selection()
    assert tui.input_buffer.text == "/approve a1b2c3"

    tui.input_buffer.text = "/deny"
    tui._refresh_context_popup()
    assert tui.context_popup_mode == "deny"
    assert tui.context_candidates == [("d4e5f6", "d4e5f6 [pending approval]")]
    tui._apply_context_selection()
    assert tui.input_buffer.text == "/deny d4e5f6"


def test_submit_panel_shows_task_name_and_pending_request_id() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="24fefd401234", input="run long task name for panel", status="paused")
    task.local_state["pending_approval_request_id"] = "24fefd40-54f7-43e0-b15a-303cfbe0c103"
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = ["waiting for approval request_id=24fefd40-54f7-43e0-b15a-303cfbe0c103"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    tui._submit_slot_ids = [task.task_id]
    text = tui._submit_panel_text(0)
    assert "run long task name" in text
    assert "request_id=24fefd" in text
    assert text.count("request_id=") == 1


def test_submit_panel_uses_request_ref_provider_for_pending_request_id() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="24fefd401234", input="x", status="paused")
    task.local_state["pending_approval_request_id"] = "147716ab-54f7-43e0-b15a-303cfbe0c103"
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = ["waiting for approval request_id=147716ab-54f7-43e0-b15a-303cfbe0c103"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
        request_ref_provider=lambda req_id: "fb67aa" if req_id.startswith("147716ab") else req_id[:6],
    )
    tui._submit_slot_ids = [task.task_id]
    text = tui._submit_panel_text(0)
    assert "waiting for approval request_id=fb67aa" in text
    assert text.count("request_id=") == 1

def test_task_prefix_style_is_consistent_for_same_task() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    s1 = tui._task_prefix_style("a1b2c3")
    s2 = tui._task_prefix_style("a1b2c3")
    s3 = tui._task_prefix_style("d4e5f6")
    assert s1 == s2
    assert s1.startswith("fg:#")
    assert s3.startswith("fg:#")


def test_task_prefix_style_distinguishes_sample_ids() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    # 来自用户截图的两个不同 task short id
    assert tui._task_prefix_style("91cfee") != tui._task_prefix_style("7db8aa")


def test_attention_marker_for_paused_with_approval() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    marker, style = tui._attention_marker("paused", "waiting for approval request_id=req-1")
    assert marker == "[! APPROVAL]"
    assert "status.approval" in style or style == ""


def test_attention_marker_failed_has_priority_over_approval_log() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    marker, style = tui._attention_marker("failed", "waiting for approval request_id=req-1")
    assert marker == "[X FAILED]"
    assert "status.failed" in style or style == ""


def test_attention_marker_pending_does_not_blink_as_approval() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    marker, style = tui._attention_marker("pending", "waiting for approval request_id=req-1")
    assert marker == "[PENDING]"
    assert style == ""


def test_submit_request_line_blinks_only_when_paused_and_pending_request() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="24fefd401234", input="x", status="paused")
    task.local_state["pending_approval_request_id"] = "24fefd40-54f7-43e0-b15a-303cfbe0c103"
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = [
        "[02:06:54] waiting for approval request_id=24fefd40-54f7-43e0-b15a-303cfbe0c103 detail=type=capability_enable"
    ]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    tui._submit_slot_ids = [task.task_id]
    tui._blink_on = True
    frags = tui._submit_panel_formatted(0)
    assert any(style == "class:status.approval" and "request_id=" in text for style, text in frags)

    task.status = "pending"
    task.local_state["pending_approval_request_id"] = ""
    frags_after = tui._submit_panel_formatted(0)
    assert not any(style == "class:status.approval" and "request_id=" in text for style, text in frags_after)


def test_welcome_text_mentions_4_plus_1_and_foreground_background() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    text = tui._welcome_text()
    assert "4+1" in text
    assert "foreground watch" in text
    assert "background run" in text


def test_submit_panels_exclude_current_watch_task() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    watch_task = Task(task_id="abc999123456", input="x", status="running")
    submit_task = Task(task_id="def111123456", input="y", status="running")
    scheduler.tasks = {watch_task.task_id: watch_task, submit_task.task_id: submit_task}
    scheduler.logs_by_task[watch_task.task_id] = ["watch line"]
    scheduler.logs_by_task[submit_task.task_id] = ["submit line"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.watching_task_id = watch_task.task_id
    tui._submit_slot_ids = [submit_task.task_id]
    text = tui._submit_panel_text(0)
    assert "def111" in text
    assert "abc999" not in text


def test_submit_panel_layout_hint_changes_by_count() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    t1 = Task(task_id="a11111111111", input="x", status="running")
    t2 = Task(task_id="b22222222222", input="x", status="running")
    t3 = Task(task_id="c33333333333", input="x", status="running")
    t4 = Task(task_id="d44444444444", input="x", status="running")
    scheduler.tasks = {t.task_id: t for t in [t1, t2, t3, t4]}
    scheduler.logs_by_task = {t.task_id: ["line"] for t in [t1, t2, t3, t4]}
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )

    scheduler.tasks = {t1.task_id: t1}
    tui._submit_slot_ids = [t1.task_id]
    assert "(single)" in tui._submit_panel_text(0)
    scheduler.tasks = {t1.task_id: t1, t2.task_id: t2}
    tui._submit_slot_ids = [t1.task_id, t2.task_id]
    assert "(stacked)" in tui._submit_panel_text(0)
    scheduler.tasks = {t1.task_id: t1, t2.task_id: t2, t3.task_id: t3}
    tui._submit_slot_ids = [t1.task_id, t2.task_id, t3.task_id]
    assert "(triple)" in tui._submit_panel_text(0)
    scheduler.tasks = {t1.task_id: t1, t2.task_id: t2, t3.task_id: t3, t4.task_id: t4}
    tui._submit_slot_ids = [t1.task_id, t2.task_id, t3.task_id, t4.task_id]
    assert "(quad)" in tui._submit_panel_text(0)


def test_submit_panel_compact_request_id_and_clip_long_line() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="24fefd401234", input="x", status="running")
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = [
        "[02:06:54] waiting for approval request_id=24fefd40-54f7-43e0-b15a-303cfbe0c103 detail=type=capability_enable payload={'capability': 'python'}"
    ]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 1,
        command_handler=_handler,
    )
    tui._submit_slot_ids = [task.task_id]
    text = tui._submit_panel_text(0)
    assert "request_id=24fefd" in text
    assert "request_id=24fefd40-54f7-43e0-b15a-303cfbe0c103" not in text
    assert "..." in text


def test_submit_panel_prints_multiple_key_logs_line_by_line() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task = Task(task_id="aa11bb22cc33", input="x", status="running")
    scheduler.tasks = {task.task_id: task}
    scheduler.logs_by_task[task.task_id] = [
        "[02:06:51] step=1: llm infer",
        "[02:06:52] step=1: tool call -> read_file",
        "[02:06:53] step=1: tool observation received",
        "[02:06:54] step=2: llm infer",
    ]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui._submit_slot_ids = [task.task_id]
    text = tui._submit_panel_text(0)
    assert "step=1: llm infer" in text
    assert "step=1: tool call -> read_file" in text
    assert "step=1: tool observation received" in text
    assert "step=2: llm infer" in text


def test_digit_key_inserts_digit_when_options_hidden() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_options = False
    tui._handle_option_digit("1")
    assert tui.input_buffer.text.endswith("1")


def test_submit_output_short_id_maps_back_to_full_slot_id() -> None:
    full_task_id = "b89961aa-1111-2222-3333-444444444444"
    task = Task(task_id=full_task_id, input="x", status="running", task_ref="m1n2p3")

    async def _handler(_: str) -> list[str]:
        return [f"submitted: {task.task_ref} (quiet)"]

    scheduler = _FakeScheduler()
    scheduler.tasks = {full_task_id: task}
    scheduler.logs_by_task[full_task_id] = ["line"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    asyncio.run(tui._run_command("submit hello"))
    assert full_task_id in tui._submit_slot_ids


def test_run_command_always_appends_output_lines() -> None:
    full_task_id = "aabbcc11-1111-2222-3333-444444444444"

    async def _handler(_: str) -> list[str]:
        return [f"submitted: {full_task_id[:6]} (watching)"]

    scheduler = _FakeScheduler()
    scheduler.tasks = {full_task_id: object()}
    scheduler.logs_by_task[full_task_id] = ["line"]
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    asyncio.run(tui._run_command("run hello"))
    assert any("submitted:" in line for line in tui.logs)


def test_log_panel_uses_dynamic_line_limit_instead_of_fixed_30() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    tui = ParagentsTUI(
        scheduler=_FakeScheduler(),  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
    )
    tui.show_welcome = False
    tui.logs = [f"line-{idx}" for idx in range(40)]
    text = tui._log_panel_text()
    assert "line-0" not in text
    assert "line-39" in text
    assert "line-10" in text


def test_provider_driven_state_keeps_task_only_in_foreground_panel() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    watch_task = Task(task_id="faceaa111111", input="x", status="running")
    submit_task = Task(task_id="beefbb222222", input="y", status="running")
    scheduler.tasks = {watch_task.task_id: watch_task, submit_task.task_id: submit_task}
    scheduler.logs_by_task[watch_task.task_id] = ["watch log"]
    scheduler.logs_by_task[submit_task.task_id] = ["submit log"]
    ui_state = {
        "watching_task_id": watch_task.task_id,
        "submit_slot_ids": [watch_task.task_id, submit_task.task_id],
    }
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
        watching_task_id_provider=lambda: ui_state["watching_task_id"],
        submit_slot_ids_provider=lambda: list(ui_state["submit_slot_ids"]),
    )
    tui.show_welcome = False

    submit_views = tui._submit_task_views()
    assert all(v.task_id != watch_task.task_id for v in submit_views)
    assert any(v.task_id == submit_task.task_id for v in submit_views)


def test_provider_driven_show_hide_roundtrip_updates_submit_panels() -> None:
    async def _handler(_: str) -> list[str]:
        return []

    scheduler = _FakeScheduler()
    task_a = Task(task_id="aaa111111111", input="x", status="running")
    task_b = Task(task_id="bbb222222222", input="y", status="running")
    scheduler.tasks = {task_a.task_id: task_a, task_b.task_id: task_b}
    scheduler.logs_by_task[task_a.task_id] = ["line a"]
    scheduler.logs_by_task[task_b.task_id] = ["line b"]
    ui_state = {
        "watching_task_id": None,
        "submit_slot_ids": [task_a.task_id, task_b.task_id],
    }
    tui = ParagentsTUI(
        scheduler=scheduler,  # type: ignore[arg-type]
        pending_approvals_provider=lambda: 0,
        command_handler=_handler,
        watching_task_id_provider=lambda: ui_state["watching_task_id"],
        submit_slot_ids_provider=lambda: list(ui_state["submit_slot_ids"]),
    )
    tui.show_welcome = False

    before = [v.task_id for v in tui._submit_task_views()]
    assert set(before) == {task_a.task_id, task_b.task_id}

    ui_state["watching_task_id"] = task_a.task_id
    ui_state["submit_slot_ids"] = [task_b.task_id]
    during = [v.task_id for v in tui._submit_task_views()]
    assert during == [task_b.task_id]

    ui_state["watching_task_id"] = None
    ui_state["submit_slot_ids"] = [task_a.task_id, task_b.task_id]
    after = [v.task_id for v in tui._submit_task_views()]
    assert set(after) == {task_a.task_id, task_b.task_id}
