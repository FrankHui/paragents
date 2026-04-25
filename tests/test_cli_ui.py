from __future__ import annotations

from task import Task
from ui_cli import (
    clear_screen,
    colorize_watch_line,
    format_startup_banner,
    format_status_line,
    format_task_table,
    normalize_command_alias,
    paginate_lines,
)


def test_startup_banner_contains_core_info() -> None:
    text = format_startup_banner(provider="openai", model="gpt-4o-mini", mode="main")
    assert "Paragents Runtime" in text
    assert "openai" in text
    assert "gpt-4o-mini" in text


def test_status_line_contains_counters() -> None:
    line = format_status_line(in_flight=2, pending_approvals=1, watching_task_id="abc")
    assert "in-flight=2" in line
    assert "approvals=1" in line
    assert "watch=abc" in line


def test_task_table_renders_rows() -> None:
    t1 = Task(task_id="t1", input="a", status="running", retries=0)
    t2 = Task(task_id="t2", input="b", status="completed", retries=1, result="done")
    table = format_task_table({"t1": t1, "t2": t2})
    assert "TASK_ID" in table
    assert "t1" in table and "running" in table
    assert "t2" in table and "completed" in table


def test_command_alias_normalization() -> None:
    assert normalize_command_alias(":s hello") == "submit hello"
    assert normalize_command_alias(":sv hello") == "submit -v hello"
    assert normalize_command_alias(":a") == "approvals"
    assert normalize_command_alias("list") == "list"


def test_paginate_lines_returns_correct_slice() -> None:
    lines = [f"item-{i}" for i in range(1, 11)]
    page2 = paginate_lines(lines, page=2, size=3)
    assert page2 == ["item-4", "item-5", "item-6"]


def test_colorize_watch_line_by_level() -> None:
    assert "\x1b[" in colorize_watch_line("task completed")
    assert "\x1b[" in colorize_watch_line("failed: boom")
    assert "\x1b[" in colorize_watch_line("needs_approval request")


def test_clear_screen_escape() -> None:
    assert clear_screen().startswith("\x1b[2J")
