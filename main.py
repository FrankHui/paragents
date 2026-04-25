from __future__ import annotations

import asyncio
import contextlib
import readline
from typing import Any

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


def _print_help() -> None:
    print("Commands:")
    print("  submit <text>         Submit task in quiet mode (default)")
    print("  submit -v <text>      Submit and live-watch task")
    print("  submit --watch <text> Submit and live-watch task")
    print("  list                  List tasks")
    print("  show <task_id>        Show live logs for hidden task")
    print("  hide                  Hide current watched task")
    print("  h                     Shortcut: hide current watched task")
    print("  cancel <task_id>      Cancel task")
    print("  pause <task_id>       Pause task")
    print("  resume <task_id>      Resume paused task")
    print("  retry <task_id>       Retry failed/cancelled task")
    print("  schedule <sec> <text> Schedule a delayed task")
    print("  schedules             List scheduled tasks")
    print("  cancel-schedule <id>  Cancel a scheduled task")
    print("  setup                 Reconfigure LLM settings")
    print("  show-config           Show current config path/provider")
    print("  approvals             List pending approval requests")
    print("  approve <id> [always] Approve request (persist if always)")
    print("  deny <id>             Deny request")
    print("  permissions           Show active permission config")
    print("  quit                  Exit")
    print("")
    print("Aliases: :h :a :p :s <text> :sv <text>")


def _parse_submit(cmd: str) -> tuple[bool, str] | None:
    if not cmd.startswith("submit "):
        return None
    body = cmd[len("submit ") :].strip()
    if not body:
        return None
    if body.startswith("-v "):
        return True, body[len("-v ") :].strip()
    if body.startswith("--watch "):
        return True, body[len("--watch ") :].strip()
    return False, body


async def _watch_task_logs(scheduler: Scheduler, task_id: str, replay: bool = True) -> None:
    if replay:
        print(f"[watch:{task_id}] --- replay recent logs ---")
        for line in scheduler.get_task_logs(task_id):
            print(f"[watch:{task_id}] {colorize_watch_line(line)}")
    queue = scheduler.subscribe_task_logs(task_id)
    try:
        print(f"[watch:{task_id}] live watch started (输入 h 或 hide 隐藏)")
        while True:
            line = await queue.get()
            print(f"[watch:{task_id}] {colorize_watch_line(line)}")
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.unsubscribe_task_logs(task_id, queue)
        print(f"[watch:{task_id}] hidden")


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
    _setup_readline()

    async def _submit_subagent(input_text: str, tools: dict[str, Any]) -> str:
        return await scheduler.submit(input_text, tools=tools)

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
        in_flight = len([t for t in scheduler.tasks.values() if t.status == "running"])
        pending_approvals = len(permission_manager.list_pending())
        status_line = format_status_line(
            in_flight=in_flight,
            pending_approvals=pending_approvals,
            watching_task_id=watching_task_id,
        )
        raw = await asyncio.to_thread(input, f"{status_line}\nruntime> ")
        cmd = normalize_command_alias(raw.strip())

        if not cmd:
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
                print("submit content is empty")
                continue
            task_id = await scheduler.submit(
                content,
                tools=_build_main_tools(),
            )
            print("submitted:", task_id, "(quiet)" if not watch_mode else "(watching)")
            if watch_mode:
                if watching_handle is not None:
                    watching_handle.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watching_handle
                watching_handle = asyncio.create_task(_watch_task_logs(scheduler, task_id, replay=True))
                watching_task_id = task_id
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
            task_id = cmd.split(" ", 1)[1].strip()
            if task_id not in scheduler.tasks:
                print("task not found")
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
            lines = [f"{k} -> {v}" for k, v in schedules.items()]
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

        handled, lines = handle_approval_command(cmd, permission_manager)
        if handled:
            for line in lines:
                print(line)
            if cmd.startswith("approve "):
                approved_request_id = _extract_approved_request_id(lines)
                if approved_request_id:
                    resumed = await _resume_tasks_waiting_for_approval(scheduler, approved_request_id)
                    if resumed:
                        print(f"resumed tasks: {', '.join(resumed)}")
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
        "submit",
        "submit -v",
        "list",
        "show",
        "hide",
        "cancel",
        "pause",
        "resume",
        "retry",
        "schedule",
        "schedules",
        "cancel-schedule",
        "approvals",
        "approve",
        "deny",
        "permissions",
        "setup",
        "show-config",
        "help",
        "quit",
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


def _extract_approved_request_id(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("approved "):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return None


async def _resume_tasks_waiting_for_approval(scheduler: Scheduler, request_id: str) -> list[str]:
    resumed: list[str] = []
    for task_id, task in scheduler.tasks.items():
        if task.status != "paused":
            continue
        pending_id = str(task.local_state.get("pending_approval_request_id", ""))
        if pending_id != request_id:
            continue
        await scheduler.resume(task_id)
        resumed.append(task_id)
    return resumed


if __name__ == "__main__":
    asyncio.run(_run_cli())
