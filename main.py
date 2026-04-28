from __future__ import annotations

import argparse
import asyncio
import contextlib
import readline
from typing import Any

from approval_view import format_approval_request
from cli_approvals import handle_approval_command
from config import (
    build_interactive_settings,
    get_runtime_config_path,
    load_runtime_settings,
    save_runtime_settings,
)
from llm_client import LLMClient
from mcp_runtime import MCPRegistry
from permissions import PermissionManager
from scheduler import Scheduler
from tools import create_main_tools
from tui_app import run_tui
from ui_cli import (
    clear_screen,
    colorize_watch_line,
    format_startup_banner,
    format_status_line,
    format_task_table,
    normalize_command_alias,
    paginate_lines,
    provider_and_model_summary,
)


def _task_ref(tasks: dict[str, Any], task_id: str) -> str:
    task = tasks.get(task_id)
    if task is None:
        return task_id[:6]
    return str(getattr(task, "task_ref", task_id[:6]))


def _request_ref(permission_manager: PermissionManager, request_id: str) -> str:
    req = permission_manager._pending.get(request_id)  # runtime helper
    if req is None:
        return request_id[:6]
    return req.request_ref


def _print_help() -> None:
    print("输入模式：")
    print("  /命令                显式执行命令（例如 /list, /run xxx）")
    print("  普通语句             自动按 /run <语句> 处理")
    print("")
    print("Commands (/前缀):")
    print("  /new <text>           新建 foreground 任务")
    print("  /run <text>           在当前 foreground 任务上续跑（需任务空闲）")
    print("  /submit <text>        提交后台任务")
    print("  /list                 List tasks")
    print("  /show <task_id>       切换 foreground 到指定任务")
    print("  /log <task_id>        只读查看任务日志（不占 foreground）")
    print("  /finish <task_id>     通知任务结束并释放槽位")
    print("  /cancel <task_id>     Cancel task")
    print("  /pause <task_id>      Pause task")
    print("  /resume <task_id>     Resume paused task")
    print("  /retry <task_id>      Retry failed/cancelled task")
    print("  /schedule <sec> <text> Schedule a delayed task")
    print("  /schedules            List scheduled tasks")
    print("  /cancel-schedule <id> Cancel a scheduled task")
    print("  /setup                Reconfigure LLM settings")
    print("  /show-config          Show current config path/provider")
    print("  /approvals            List pending approval requests")
    print("  /approve <id> [always] Approve request (persist if always)")
    print("  /deny <id>            Deny request")
    print("  /permissions          Show active permission config")
    print("  /quit                 Exit")
    print("")
    print("运行模型：")
    print("  总槽位最多 5 个（包含 foreground 与后台任务）")
    print("  非 / 开头输入默认按 /run <text> 处理")
    print("")
    print("审批提示：")
    print("  /run 触发审批时，可直接输入 y/n + 回车确认。")
    print("  submit 模式保持 request_id 手动 approve/deny。")
    print("")
    print("兼容别名: :h :a :p :s <text> :sv <text>")


def _normalize_user_input(raw: str) -> str:
    cmd = normalize_command_alias(raw.strip())
    if not cmd:
        return ""
    if cmd.lower() in {"y", "n"}:
        return cmd.lower()
    if cmd.startswith("/"):
        return cmd[1:].strip()
    if cmd.startswith("submit ") or cmd.startswith("/submit "):
        return cmd
    return cmd


def _parse_submit(cmd: str) -> str | None:
    if not cmd.startswith("submit "):
        return None
    body = cmd[len("submit ") :].strip()
    if not body:
        return None
    return body


def _parse_new(cmd: str) -> str | None:
    if not cmd.startswith("new "):
        return None
    body = cmd[len("new ") :].strip()
    if not body:
        return None
    return body


def _parse_run(cmd: str) -> str | None:
    if not cmd.startswith("run "):
        return None
    body = cmd[len("run ") :].strip()
    if not body:
        return None
    return body


async def _watch_task_logs(scheduler: Scheduler, task_id: str, replay: bool = True) -> None:
    short_task_id = _task_ref(scheduler.tasks, task_id)
    if replay:
        print(f"[watch:{short_task_id}] --- replay recent logs ---")
        for line in scheduler.get_task_logs(task_id):
            print(f"[watch:{short_task_id}] {colorize_watch_line(line)}")
    queue = scheduler.subscribe_task_logs(task_id)
    try:
        print(f"[watch:{short_task_id}] live watch started (输入 h 或 hide 隐藏)")
        while True:
            line = await queue.get()
            print(f"[watch:{short_task_id}] {colorize_watch_line(line)}")
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.unsubscribe_task_logs(task_id, queue)
        print(f"[watch:{short_task_id}] hidden")


async def _run_initial_setup_if_needed() -> None:
    settings = load_runtime_settings()
    if settings is not None:
        return
    print(f"未检测到配置文件：{get_runtime_config_path()}")
    print("首次启动需要配置 LLM。")
    new_settings = await asyncio.to_thread(build_interactive_settings, None)
    await asyncio.to_thread(save_runtime_settings, new_settings)
    print(f"配置已保存到：{get_runtime_config_path()}")


async def _run_cli() -> None:
    await _run_initial_setup_if_needed()
    settings = load_runtime_settings()
    permission_manager = PermissionManager.load_or_create()
    llm_client = LLMClient(max_concurrency=16, timeout_s=20, max_retries=2, runtime_settings=settings)
    scheduler = Scheduler(llm_client=llm_client, max_in_flight=6)
    await scheduler.start()
    mcp_registry = MCPRegistry()
    watching_task_id: str | None = None
    run_task_id: str | None = None
    pending_run_request_id: str | None = None
    run_task_id: str | None = None
    pending_run_request_id: str | None = None
    last_prompted_run_request_id: str | None = None
    run_task_id: str | None = None
    pending_run_request_id: str | None = None
    _setup_readline()

    async def _submit_subagent(
        input_text: str,
        tools: dict[str, Any],
        parent_task_id: str | None = None,
        lineage_root_id: str | None = None,
    ) -> str:
        return await scheduler.submit(
            input_text,
            tools=tools,
            parent_task_id=parent_task_id,
            lineage_root_id=lineage_root_id,
        )

    def _build_main_tools() -> dict[str, Any]:
        return create_main_tools(
            permission_manager=permission_manager,
            submit_subagent=_submit_subagent,
            mcp_registry=mcp_registry,
            mcp_enabled_tools=None,
            schedule_task_in=scheduler.schedule_task_in,
            list_scheduled_tasks=scheduler.list_scheduled_tasks,
            cancel_scheduled_task=scheduler.cancel_scheduled_task,
        )
    watching_handle: asyncio.Task[None] | None = None
    provider, model = provider_and_model_summary(settings)
    print(clear_screen() + format_startup_banner(provider=provider, model=model, mode="main"))
    _print_help()
    while True:
        if run_task_id is not None:
            pending_run_request_id = _get_task_pending_approval_request_id(scheduler.tasks.get(run_task_id))
            if pending_run_request_id and pending_run_request_id != last_prompted_run_request_id:
                print(_approval_prompt_text(permission_manager, pending_run_request_id))
                last_prompted_run_request_id = pending_run_request_id
        in_flight = len([t for t in scheduler.tasks.values() if t.status == "running"])
        pending_approvals = len(permission_manager.list_pending())
        status_line = format_status_line(
            in_flight=in_flight,
            pending_approvals=pending_approvals,
            watching_task_id=watching_task_id,
        )
        raw = await asyncio.to_thread(input, f"{status_line}\nruntime> ")
        cmd = _normalize_user_input(raw)

        if not cmd:
            continue

        if cmd in {"y", "n"} and pending_run_request_id:
            lines = await _handle_run_approval_answer(cmd, pending_run_request_id, permission_manager, scheduler)
            for line in lines:
                print(line)
            pending_run_request_id = None
            last_prompted_run_request_id = None
            continue

        if cmd in {"quit", "exit"}:
            if watching_handle is not None:
                watching_handle.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watching_handle
            break

        parsed = _parse_submit(cmd)
        if parsed is not None:
            watch_mode, content = parsed
            if not content:
                print("run content is empty" if cmd.startswith("run ") else "submit content is empty")
                continue
            task_id = await scheduler.submit(
                content,
                tools=_build_main_tools(),
            )
            print("submitted:", _task_ref(scheduler.tasks, task_id), "(quiet)" if not watch_mode else "(watching)")
            if watch_mode:
                if watching_handle is not None:
                    watching_handle.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watching_handle
                watching_handle = asyncio.create_task(_watch_task_logs(scheduler, task_id, replay=True))
                watching_task_id = task_id
            if cmd.startswith("run "):
                run_task_id = task_id
                pending_run_request_id = None
                last_prompted_run_request_id = None
            continue

        if cmd == "list" or cmd.startswith("list "):
            if not scheduler.tasks:
                print("(no tasks)")
                continue
            page, size = _parse_page_size(cmd)
            table_lines = format_task_table(scheduler.tasks).splitlines()
            print("\n".join(paginate_lines(table_lines, page=page, size=size)))
            continue

        if cmd == "h" or cmd == "hide":
            if watching_handle is None:
                print("no watching task")
                continue
            watching_handle.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watching_handle
            watching_handle = None
            watching_task_id = None
            continue

        if cmd.startswith("show "):
            raw_task_id = cmd.split(" ", 1)[1].strip()
            task_id, err = _resolve_task_id(raw_task_id, scheduler.tasks)
            if task_id is None:
                print(err or "task not found")
                continue
            if watching_handle is not None:
                watching_handle.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watching_handle
            watching_handle = asyncio.create_task(_watch_task_logs(scheduler, task_id, replay=True))
            watching_task_id = task_id
            continue

        if cmd.startswith("cancel "):
            await scheduler.cancel(cmd.split(" ", 1)[1].strip())
            continue

        if cmd.startswith("pause "):
            await scheduler.pause(cmd.split(" ", 1)[1].strip())
            continue

        if cmd.startswith("resume "):
            await scheduler.resume(cmd.split(" ", 1)[1].strip())
            continue

        if cmd.startswith("retry "):
            await scheduler.retry(cmd.split(" ", 1)[1].strip())
            continue

        if cmd.startswith("schedule "):
            body = cmd[len("schedule ") :].strip()
            parts = body.split(" ", 1)
            if len(parts) < 2:
                print("usage: schedule <sec> <text>")
                continue
            delay_s = float(parts[0])
            content = parts[1].strip()
            scheduled_id = await scheduler.schedule_task_in(delay_s, content, _build_main_tools())
            print("scheduled:", scheduled_id)
            continue

        if cmd == "schedules" or cmd.startswith("schedules "):
            page, size = _parse_page_size(cmd)
            schedules = scheduler.list_scheduled_tasks()
            lines = [f"{k[:6]} -> {v}" for k, v in schedules.items()]
            if not lines:
                print("(no scheduled tasks)")
            else:
                print("\n".join(paginate_lines(lines, page=page, size=size)))
            continue

        if cmd.startswith("cancel-schedule "):
            scheduled_id = cmd.split(" ", 1)[1].strip()
            ok = await scheduler.cancel_scheduled_task(scheduled_id)
            print("cancelled" if ok else "scheduled task not found")
            continue

        if cmd == "setup":
            existing = load_runtime_settings()
            new_settings = await asyncio.to_thread(build_interactive_settings, existing)
            await asyncio.to_thread(save_runtime_settings, new_settings)
            scheduler.set_llm_client(
                LLMClient(max_concurrency=16, timeout_s=20, max_retries=2, runtime_settings=new_settings)
            )
            print(f"配置已更新：{get_runtime_config_path()}")
            continue

        if cmd == "show-config":
            current = load_runtime_settings()
            if current is None:
                print(f"未找到配置文件：{get_runtime_config_path()}")
            else:
                print(f"path={get_runtime_config_path()}")
                print(f"default_provider={current.default_provider}")
                provider = current.providers.get(current.default_provider)
                if provider is not None:
                    print(f"base_url={provider.base_url}")
                    print(f"model={provider.model}")
            continue

        pending_ids_before = [(req.request_id, req.request_ref) for req in permission_manager.list_pending()]
        handled, lines = handle_approval_command(cmd, permission_manager)
        if handled:
            for line in lines:
                print(line)
            if cmd.startswith("approve "):
                approved_request_id = _resolve_request_id_from_approve_cmd(cmd, pending_ids_before)
                if approved_request_id:
                    resumed = await _resume_tasks_waiting_for_approval(scheduler, approved_request_id)
                    if resumed:
                        print("resumed tasks: " + ", ".join(_task_ref(scheduler.tasks, task_id) for task_id in resumed))
            continue

        if cmd == "permissions":
            print(permission_manager.describe())
            continue

        if cmd == "help":
            _print_help()
            continue

        print(f"unknown command: {cmd}")
        _print_help()


def _setup_readline() -> None:
    commands = [
        "/new",
        "/run",
        "/submit",
        "/list",
        "/show",
        "/log",
        "/finish",
        "/cancel",
        "/pause",
        "/resume",
        "/retry",
        "/schedule",
        "/schedules",
        "/cancel-schedule",
        "/approvals",
        "/approve",
        "/deny",
        "/permissions",
        "/setup",
        "/show-config",
        "/help",
        "/quit",
    ]

    def _completer(text: str, state: int) -> str | None:
        options = [cmd for cmd in commands if cmd.startswith(text)]
        if state < len(options):
            return options[state]
        return None

    readline.parse_and_bind("set editing-mode emacs")
    # 常见终端快捷键（接近 bash/zsh 输入体验）
    readline.parse_and_bind('"\\C-u": unix-line-discard')
    readline.parse_and_bind('"\\C-w": unix-word-rubout')
    readline.parse_and_bind('"\\C-a": beginning-of-line')
    readline.parse_and_bind('"\\C-e": end-of-line')
    readline.parse_and_bind('"\\C-k": kill-line')
    readline.parse_and_bind('"\\C-y": yank')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(_completer)


def _parse_page_size(cmd: str) -> tuple[int, int]:
    page = 1
    size = 20
    tokens = cmd.split()
    for i, token in enumerate(tokens):
        if token == "--page" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            page = int(tokens[i + 1])
        if token == "--size" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            size = int(tokens[i + 1])
    return page, size


def _parse_finish_indices(raw: str) -> list[int]:
    text = raw.strip()
    if not text:
        return []
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if not parts:
        return []
    if not all(p.isdigit() for p in parts):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for p in parts:
        idx = int(p)
        if idx <= 0:
            return []
        if idx not in seen:
            result.append(idx)
            seen.add(idx)
    return result


def _parse_ack_indices(raw: str) -> list[int]:
    # Backward-compatible alias.
    return _parse_finish_indices(raw)


def _resolve_request_id_from_approve_cmd(
    cmd: str, pending_requests: list[tuple[str, str]] | list[str]
) -> str | None:
    if not (cmd.startswith("approve ") or cmd.startswith("deny ")):
        return None
    rest = cmd.split(" ", 1)[1].strip()
    parts = rest.split()
    request_id_input = parts[0] if parts else ""
    if not request_id_input:
        return None
    normalized_pending: list[tuple[str, str]]
    if pending_requests and isinstance(pending_requests[0], str):
        normalized_pending = [(rid, rid[:6]) for rid in pending_requests]  # backward-compatible tests/callers
    else:
        normalized_pending = pending_requests  # type: ignore[assignment]
    if request_id_input.startswith("#") and request_id_input[1:].isdigit():
        idx = int(request_id_input[1:])
        if 1 <= idx <= len(normalized_pending):
            return normalized_pending[idx - 1][0]
        return None
    pending_ids = [rid for rid, _ in normalized_pending]
    if request_id_input in pending_ids:
        return request_id_input
    matched = [rid for rid in pending_ids if rid.startswith(request_id_input)]
    if len(matched) == 1:
        return matched[0]
    ref_matched = [rid for rid, rref in normalized_pending if rref == request_id_input]
    if len(ref_matched) == 1:
        return ref_matched[0]
    ref_prefix = [rid for rid, rref in normalized_pending if rref.startswith(request_id_input)]
    if len(ref_prefix) == 1:
        return ref_prefix[0]
    return None


def _resolve_task_id(task_id_input: str, tasks: dict[str, Any]) -> tuple[str | None, str | None]:
    raw = task_id_input.strip()
    if not raw:
        return None, "missing task id"
    if raw in tasks:
        return raw, None
    ref_exact = [tid for tid, t in tasks.items() if str(getattr(t, "task_ref", "")) == raw]
    if len(ref_exact) == 1:
        return ref_exact[0], None
    matched = [tid for tid in tasks.keys() if tid.startswith(raw)]
    if len(matched) == 1:
        return matched[0], None
    if len(matched) > 1:
        return None, f"ambiguous task id prefix: {raw}"
    ref_matched = [tid for tid, t in tasks.items() if str(getattr(t, "task_ref", "")).startswith(raw)]
    if len(ref_matched) == 1:
        return ref_matched[0], None
    if len(ref_matched) > 1:
        return None, f"ambiguous task id prefix: {raw}"
    return None, f"task not found: {raw}"


def _get_task_pending_approval_request_id(task: Any | None) -> str | None:
    if task is None:
        return None
    if getattr(task, "status", "") != "paused":
        return None
    pending_id = str(getattr(task, "local_state", {}).get("pending_approval_request_id", ""))
    return pending_id or None


def _get_active_approval_request_id(
    scheduler: Scheduler,
    run_task_id: str | None,
    watching_task_id: str | None,
) -> str | None:
    # Foreground task should always win input focus.
    # If user /show-ed a task that is waiting for approval, y/n should target it first.
    if watching_task_id is not None:
        pending = _get_task_pending_approval_request_id(scheduler.tasks.get(watching_task_id))
        if pending:
            return pending
    if run_task_id is not None:
        pending = _get_task_pending_approval_request_id(scheduler.tasks.get(run_task_id))
        if pending:
            return pending
    return None


def _is_watch_slot_blocking(scheduler: Scheduler, watching_task_id: str | None) -> bool:
    if not watching_task_id:
        return False
    task = scheduler.tasks.get(watching_task_id)
    if task is None:
        return False
    return getattr(task, "status", "") in {"running", "pending", "paused"}


async def _handle_run_approval_answer(
    answer: str,
    request_id: str,
    permission_manager: PermissionManager,
    scheduler: Scheduler,
) -> list[str]:
    normalized = answer.strip().lower()
    req = permission_manager.get_pending(request_id)
    short_id = req.request_ref if req is not None else request_id[:6]
    if normalized == "y":
        ok = permission_manager.approve(request_id, always=False)
        if not ok:
            return ["approval request not found"]
        resumed = await _resume_tasks_waiting_for_approval(scheduler, request_id)
        if resumed:
            resumed_short = ", ".join(_task_ref(scheduler.tasks, t) for t in resumed)
            return [f"approved {short_id} (session)", f"resumed tasks: {resumed_short}"]
        return [f"approved {short_id} (session)"]
    if normalized == "n":
        ok = permission_manager.deny(request_id)
        if not ok:
            return ["approval request not found"]
        failed = _fail_tasks_waiting_for_approval(scheduler, request_id, reason=f"approval denied ({short_id})")
        if failed:
            failed_short = ", ".join(_task_ref(scheduler.tasks, t) for t in failed)
            return [f"denied {short_id}", f"failed tasks: {failed_short}"]
        return [f"denied {short_id}"]
    return ["请输入 y 或 n"]


def _approval_prompt_text(permission_manager: PermissionManager, request_id: str) -> str:
    req = permission_manager.get_pending(request_id)
    if req is None:
        return f"assistant: 权限请求 {_request_ref(permission_manager, request_id)}，是否同意？[y/n]"
    rendered = format_approval_request(req)
    if "|" in rendered:
        rendered = rendered.split("|", 1)[1].strip()
    return f"assistant: 权限请求 {_request_ref(permission_manager, request_id)} | {rendered} | 是否同意？[y/n]"


async def _resume_tasks_waiting_for_approval(scheduler: Scheduler, request_id: str) -> list[str]:
    resumed: list[str] = []
    for task_id, task in scheduler.tasks.items():
        if task.status != "paused":
            continue
        pending_id = str(task.local_state.get("pending_approval_request_id", ""))
        if pending_id != request_id:
            continue
        task.local_state.pop("pending_approval_request_id", None)
        task.local_state.pop("pending_approval_tool_name", None)
        await scheduler.resume(task_id)
        resumed.append(task_id)
    return resumed


def _fail_tasks_waiting_for_approval(scheduler: Scheduler, request_id: str, reason: str) -> list[str]:
    failed: list[str] = []
    for task_id, task in scheduler.tasks.items():
        if task.status != "paused":
            continue
        pending_id = str(task.local_state.get("pending_approval_request_id", ""))
        if pending_id != request_id:
            continue
        task.local_state.pop("pending_approval_request_id", None)
        task.local_state.pop("pending_approval_tool_name", None)
        task.status = "failed"
        task.error = reason
        task.touch()
        scheduler._publish_log(task_id, f"task failed: {reason}")  # noqa: SLF001
        failed.append(task_id)
    return failed


async def _run_tui_mode() -> None:
    await _run_initial_setup_if_needed()
    settings = load_runtime_settings()
    permission_manager = PermissionManager.load_or_create()
    llm_client = LLMClient(max_concurrency=16, timeout_s=20, max_retries=2, runtime_settings=settings)
    scheduler = Scheduler(llm_client=llm_client, max_in_flight=6)
    await scheduler.start()
    mcp_registry = MCPRegistry()
    foreground_task_id: str | None = None
    log_task_id: str | None = None
    pending_run_request_id: str | None = None
    task_slots: list[str] = []
    finished_task_ids: set[str] = set()

    async def _submit_subagent(
        input_text: str,
        tools: dict[str, Any],
        parent_task_id: str | None = None,
        lineage_root_id: str | None = None,
    ) -> str:
        return await scheduler.submit(
            input_text,
            tools=tools,
            parent_task_id=parent_task_id,
            lineage_root_id=lineage_root_id,
        )

    def _build_main_tools() -> dict[str, Any]:
        return create_main_tools(
            permission_manager=permission_manager,
            submit_subagent=_submit_subagent,
            mcp_registry=mcp_registry,
            mcp_enabled_tools=None,
            schedule_task_in=scheduler.schedule_task_in,
            list_scheduled_tasks=scheduler.list_scheduled_tasks,
            cancel_scheduled_task=scheduler.cancel_scheduled_task,
        )

    def _refresh_task_state() -> None:
        nonlocal foreground_task_id, log_task_id
        valid = [tid for tid in task_slots if tid in scheduler.tasks]
        task_slots[:] = valid[:5]
        finished_task_ids.intersection_update(set(task_slots))
        if foreground_task_id not in task_slots:
            foreground_task_id = None
        if log_task_id not in task_slots:
            log_task_id = None

    def _finish_prompt_lines() -> list[str]:
        if not task_slots:
            return ["当前没有可 finish 的任务。"]
        lines = ["可 finish 任务："]
        for idx, task_id in enumerate(task_slots, start=1):
            lines.append(f"{idx}) {_task_ref(scheduler.tasks, task_id)}")
        lines.append("支持批量输入，例如: 1/2")
        lines.append("或使用: /finish <task_id前6位>")
        return lines

    def _resolve_slot_task_id(task_id_input: str) -> str | None:
        raw = task_id_input.strip()
        if not raw:
            return None
        if raw in task_slots:
            return raw
        ref_exact = [tid for tid in task_slots if _task_ref(scheduler.tasks, tid) == raw]
        if len(ref_exact) == 1:
            return ref_exact[0]
        matched = [tid for tid in task_slots if tid.startswith(raw)]
        if len(matched) == 1:
            return matched[0]
        ref_prefix = [tid for tid in task_slots if _task_ref(scheduler.tasks, tid).startswith(raw)]
        if len(ref_prefix) == 1:
            return ref_prefix[0]
        return None

    async def _handle_cmd(cmd: str) -> list[str]:
        nonlocal foreground_task_id, log_task_id, pending_run_request_id
        out: list[str] = []
        _refresh_task_state()
        slash_mode = False
        if cmd.startswith("__slash__ "):
            slash_mode = True
            cmd = cmd[len("__slash__ ") :].strip()
        pending_ids_before = [(req.request_id, req.request_ref) for req in permission_manager.list_pending()]
        pending_run_request_id = _get_active_approval_request_id(scheduler, foreground_task_id, foreground_task_id)
        if cmd in {"y", "n"} and pending_run_request_id:
            return await _handle_run_approval_answer(cmd, pending_run_request_id, permission_manager, scheduler)
        known_prefixes = (
            "new ",
            "run ",
            "submit ",
            "list",
            "show ",
            "show",
            "log ",
            "resume ",
            "finish ",
            "approvals",
            "approve ",
            "deny ",
            "cancel ",
            "pause ",
            "retry ",
            "schedule ",
            "schedules",
            "cancel-schedule ",
            "setup",
            "show-config",
            "permissions",
            "help",
            "quit",
            "exit",
            "y",
            "n",
        )
        if not slash_mode and not cmd.startswith(known_prefixes):
            cmd = f"{'run' if foreground_task_id else 'new'} {cmd}"
        if cmd.startswith("finish "):
            _refresh_task_state()
            raw_task_id = cmd.split(" ", 1)[1].strip()
            target_task_id = _resolve_slot_task_id(raw_task_id)
            if not target_task_id:
                return [
                    f"未找到可 finish 任务: {raw_task_id}",
                    *_finish_prompt_lines(),
                ]
            finished_task_ids.add(target_task_id)
            if target_task_id in task_slots:
                task_slots.remove(target_task_id)
            if foreground_task_id == target_task_id:
                foreground_task_id = None
                log_task_id = None
            return [f"已 finish 任务 {_task_ref(scheduler.tasks, target_task_id)}，已释放槽位。"]
        finish_indices = _parse_finish_indices(cmd)
        if finish_indices:
            _refresh_task_state()
            if not task_slots:
                return ["当前没有可 finish 任务。"]
            if max(finish_indices) > len(task_slots):
                return _finish_prompt_lines()
            target_ids = [task_slots[idx - 1] for idx in finish_indices]
            for task_id in target_ids:
                finished_task_ids.add(task_id)
                if task_id in task_slots:
                    task_slots.remove(task_id)
                if foreground_task_id == task_id:
                    foreground_task_id = None
                    log_task_id = None
            released = ", ".join(_task_ref(scheduler.tasks, task_id) for task_id in target_ids)
            return [f"已 finish 任务 {released}，已释放槽位。"]
        handled, lines = handle_approval_command(cmd, permission_manager)
        if handled:
            out.extend(lines)
            decision_request_id = _resolve_request_id_from_approve_cmd(cmd, pending_ids_before)
            if decision_request_id:
                if cmd.startswith("approve "):
                    resumed = await _resume_tasks_waiting_for_approval(scheduler, decision_request_id)
                    if resumed:
                        out.append("resumed tasks: " + ", ".join(_task_ref(scheduler.tasks, task_id) for task_id in resumed))
                elif cmd.startswith("deny "):
                    failed = _fail_tasks_waiting_for_approval(
                        scheduler,
                        decision_request_id,
                        reason=f"approval denied ({_request_ref(permission_manager, decision_request_id)})",
                    )
                    if failed:
                        out.append("failed tasks: " + ", ".join(_task_ref(scheduler.tasks, task_id) for task_id in failed))
            return out
        new_content = _parse_new(cmd)
        if new_content is not None:
            _refresh_task_state()
            if len(task_slots) >= 5:
                return ["任务槽位已满（最多 5 个），请先 /finish 释放后再运行。", *_finish_prompt_lines()]
            task_id = await scheduler.submit(new_content, tools=_build_main_tools())
            if task_id not in task_slots:
                task_slots.append(task_id)
            foreground_task_id = task_id
            log_task_id = None
            pending_run_request_id = None
            return [f"submitted: {_task_ref(scheduler.tasks, task_id)} (foreground)"]

        run_content = _parse_run(cmd)
        if run_content is not None:
            _refresh_task_state()
            if foreground_task_id is None:
                return ["当前没有 foreground 任务，请使用 /new <text> 新建任务。"]
            fg_task = scheduler.tasks.get(foreground_task_id)
            fg_status = str(getattr(fg_task, "status", "")) if fg_task is not None else ""
            if fg_status in {"running", "pending", "paused"}:
                return [f"foreground 任务 {_task_ref(scheduler.tasks, foreground_task_id)} 正在执行中，当前不可 /run 新序列。"]
            ok = await scheduler.continue_task(foreground_task_id, run_content)
            if not ok:
                return [f"foreground 任务 {_task_ref(scheduler.tasks, foreground_task_id)} 当前不可续跑。"]
            log_task_id = None
            pending_run_request_id = None
            return [f"continued in foreground: {_task_ref(scheduler.tasks, foreground_task_id)}"]

        submit_content = _parse_submit(cmd)
        if submit_content is not None:
            content = submit_content
            if not content:
                return ["submit content is empty"]
            _refresh_task_state()
            if len(task_slots) >= 5:
                return ["任务槽位已满（最多 5 个），请先 /finish 释放后再提交。", *_finish_prompt_lines()]
            task_id = await scheduler.submit(content, tools=_build_main_tools())
            if task_id not in task_slots:
                task_slots.append(task_id)
            out.append("submitted: " + _task_ref(scheduler.tasks, task_id) + " (background)")
            return out
        if cmd == "list" or cmd.startswith("list "):
            if not scheduler.tasks:
                return ["(no tasks)"]
            page, size = _parse_page_size(cmd)
            lines = format_task_table(scheduler.tasks).splitlines()
            return paginate_lines(lines, page=page, size=size)
        if cmd == "show" and foreground_task_id:
            return scheduler.get_task_logs(foreground_task_id, limit=30)
        if cmd.startswith("show "):
            raw_task_id = cmd.split(" ", 1)[1].strip()
            task_id, err = _resolve_task_id(raw_task_id, scheduler.tasks)
            if task_id is None:
                return [err or "task not found"]
            if task_id in finished_task_ids:
                return [f"任务 {_task_ref(scheduler.tasks, task_id)} 已 finish 并释放，不支持 /show。"]
            foreground_task_id = task_id
            log_task_id = None
            return [f"switched to foreground: {_task_ref(scheduler.tasks, task_id)}"]
        if cmd.startswith("log "):
            raw_task_id = cmd.split(" ", 1)[1].strip()
            task_id, err = _resolve_task_id(raw_task_id, scheduler.tasks)
            if task_id is None:
                return [err or "task not found"]
            foreground_task_id = None
            log_task_id = task_id
            return [f"readonly log view: {_task_ref(scheduler.tasks, task_id)}"]
        if cmd.startswith("cancel "):
            await scheduler.cancel(cmd.split(" ", 1)[1].strip())
            return ["cancel signal sent"]
        if cmd.startswith("pause "):
            await scheduler.pause(cmd.split(" ", 1)[1].strip())
            _refresh_submit_task_state()
            return ["paused"]
        if cmd.startswith("resume "):
            raw_task_id = cmd.split(" ", 1)[1].strip()
            task_id, err = _resolve_task_id(raw_task_id, scheduler.tasks)
            if task_id is None:
                return [err or "task not found"]
            if task_id in finished_task_ids:
                return [f"任务 {_task_ref(scheduler.tasks, task_id)} 已 finish，不可 resume。"]
            await scheduler.resume(task_id)
            foreground_task_id = task_id
            log_task_id = None
            return [f"resumed and switched to foreground: {_task_ref(scheduler.tasks, task_id)}"]
        return ["unknown command"]

    def _finish_candidate_task_ids_provider() -> list[str]:
        _refresh_task_state()
        return [tid for tid in task_slots if tid not in finished_task_ids]

    def _show_candidate_task_ids_provider() -> list[str]:
        _refresh_task_state()
        return [tid for tid in task_slots if tid not in finished_task_ids]

    def _pending_approval_request_refs_provider() -> list[str]:
        return [req.request_ref for req in permission_manager.list_pending()]

    await run_tui(
        scheduler=scheduler,
        pending_approvals_provider=lambda: len(permission_manager.list_pending()),
        command_handler=_handle_cmd,
        foreground_task_id_provider=lambda: foreground_task_id,
        task_slot_ids_provider=lambda: list(task_slots),
        finish_candidate_task_ids_provider=_finish_candidate_task_ids_provider,
        show_candidate_task_ids_provider=_show_candidate_task_ids_provider,
        approve_candidate_request_refs_provider=_pending_approval_request_refs_provider,
        deny_candidate_request_refs_provider=_pending_approval_request_refs_provider,
        request_ref_provider=lambda request_id: _request_ref(permission_manager, request_id),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui", choices=["cli", "tui"], default="cli")
    args = parser.parse_args()
    if args.ui == "tui":
        asyncio.run(_run_tui_mode())
    else:
        asyncio.run(_run_cli())
