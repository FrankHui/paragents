from __future__ import annotations

import os
import shutil
import time
from typing import Any

from task import Task

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
GRAY = "\x1b[90m"
BG_DARK = "\x1b[48;5;17m"


def clear_screen() -> str:
    return "\x1b[2J\x1b[H"


def terminal_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def _box_line(left: str, fill: str, right: str, width: int) -> str:
    inner = max(0, width - 2)
    return f"{left}{fill * inner}{right}"


def _box_text(text: str, width: int) -> str:
    inner = max(0, width - 2)
    clipped = text[:inner]
    padding = max(0, inner - len(clipped))
    return f"│{clipped}{' ' * padding}│"


def render_home_screen(provider: str, model: str, mode: str) -> str:
    width = min(max(72, terminal_width()), 120)
    cwd = os.getcwd()
    lines = [
        _box_line("┌", "─", "┐", width),
        _box_text(f"{BOLD}{CYAN}Paragents Runtime{RESET}", width),
        _box_text(f"{DIM}{provider} · {model} · mode={mode}{RESET}", width),
        _box_text(f"{DIM}{cwd}{RESET}", width),
        _box_line("├", "─", "┤", width),
        _box_text(f"{BOLD}> {RESET}:s <text>  {DIM}submit task{RESET}", width),
        _box_text(f"{BOLD}> {RESET}:sv <text> {DIM}submit and watch{RESET}", width),
        _box_text(f"{BOLD}> {RESET}:a         {DIM}approval center{RESET}", width),
        _box_text(f"{BOLD}> {RESET}:h         {DIM}help{RESET}", width),
        _box_line("└", "─", "┘", width),
    ]
    return "\n".join(lines)


def format_startup_banner(provider: str, model: str, mode: str) -> str:
    # 兼容旧调用，内部升级为新首页布局
    return render_home_screen(provider=provider, model=model, mode=mode)


def format_status_line(in_flight: int, pending_approvals: int, watching_task_id: str | None) -> str:
    watch = watching_task_id[:6] if watching_task_id else "-"
    now = time.strftime("%H:%M:%S")
    return (
        f"{BG_DARK}{BOLD} {now} {RESET} "
        f"{CYAN}in-flight={in_flight}{RESET}  "
        f"{YELLOW}approvals={pending_approvals}{RESET}  "
        f"{GRAY}watch={watch}{RESET}"
    )


def format_task_table(tasks: dict[str, Task]) -> str:
    headers = ("TASK_ID", "STATUS", "RETRIES", "RESULT")
    rows: list[tuple[str, str, str, str]] = []
    for task in tasks.values():
        result_preview = "" if task.result is None else str(task.result)
        if len(result_preview) > 24:
            result_preview = result_preview[:24] + "..."
        rows.append((task.task_ref, task.status, str(task.retries), result_preview))
    all_rows = [headers, *rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(4)]
    lines = [
        " | ".join(headers[i].ljust(widths[i]) for i in range(4)),
        "-+-".join("-" * widths[i] for i in range(4)),
    ]
    for row in rows:
        lines.append(" | ".join(row[i].ljust(widths[i]) for i in range(4)))
    return "\n".join(lines)


def paginate_lines(lines: list[str], page: int, size: int) -> list[str]:
    if size <= 0:
        size = 20
    if page <= 0:
        page = 1
    start = (page - 1) * size
    end = start + size
    return lines[start:end]


def normalize_command_alias(cmd: str) -> str:
    cmd = cmd.strip()
    alias_map: dict[str, str] = {
        ":h": "help",
        ":a": "approvals",
        ":p": "permissions",
    }
    if cmd in alias_map:
        return alias_map[cmd]
    if cmd.startswith(":s "):
        return "submit " + cmd[3:].strip()
    if cmd.startswith(":sv "):
        return "submit -v " + cmd[4:].strip()
    return cmd


def provider_and_model_summary(settings: Any) -> tuple[str, str]:
    if settings is None:
        return "unknown", "unknown"
    provider = getattr(settings, "default_provider", "unknown")
    model = "unknown"
    providers = getattr(settings, "providers", {})
    if provider in providers:
        model = getattr(providers[provider], "model", "unknown")
    return str(provider), str(model)


def colorize_watch_line(line: str) -> str:
    lower = line.lower()
    if "failed" in lower or "error" in lower:
        return f"{RED}{line}{RESET}"
    if "completed" in lower or "success" in lower:
        return f"{GREEN}{line}{RESET}"
    if "approval" in lower or "needs_approval" in lower:
        return f"{YELLOW}{line}{RESET}"
    return line
