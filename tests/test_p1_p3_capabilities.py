from __future__ import annotations

import asyncio
from pathlib import Path

from mcp_runtime import MCPRegistry
from permissions import FsScope, PermissionManager, PermissionsConfig
from scheduler import Scheduler
from tools import create_main_tools, create_subagent_tools


def _manager(tmp_path: Path, *, mcp_enabled: bool = True, git_enabled: bool = True, github_enabled: bool = True):
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": True,
            "git": git_enabled,
            "github": github_enabled,
            "web": False,
            "mcp": mcp_enabled,
        },
        fs_scopes=[FsScope(path=str(tmp_path), read=True, write=True)],
    )
    return PermissionManager(cfg, tmp_path / "permissions.json")


def test_main_vs_subagent_tool_profiles(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    main = create_main_tools(mgr, submit_subagent=None, mcp_registry=None)
    sub = create_subagent_tools(mgr)
    assert "spawn" in main
    assert "spawn" not in sub
    assert "schedule_task" in main
    assert "schedule_task" not in sub


def test_spawn_uses_subagent_toolset(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    captured = {}

    async def _submit(input_text: str, tools):
        captured["input"] = input_text
        captured["tool_names"] = sorted(list(tools.keys()))
        return "sub-task-id"

    main = create_main_tools(mgr, submit_subagent=_submit, mcp_registry=None)
    result = asyncio.run(main["spawn"]({"input": "hello"}))
    assert result["ok"] is True
    assert result["prompt_id"] == "sub-task-id"
    assert "spawn" not in captured["tool_names"]


def test_mcp_tool_respects_whitelist(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, mcp_enabled=True)
    reg = MCPRegistry()

    async def _echo(args):
        return {"ok": True, "echo": args.get("text", "")}

    reg.register_server("demo", {"echo": _echo})
    tools = create_main_tools(mgr, submit_subagent=None, mcp_registry=reg, mcp_enabled_tools={"demo": ["echo"]})
    assert "mcp_demo_echo" in tools
    out = asyncio.run(tools["mcp_demo_echo"]({"text": "hi"}))
    assert out["ok"] is True


def test_git_and_github_write_require_approval(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, git_enabled=True, github_enabled=True)
    tools = create_main_tools(mgr, submit_subagent=None, mcp_registry=None)
    git_out = asyncio.run(tools["git_push"]({"remote": "origin", "branch": "main"}))
    gh_out = asyncio.run(tools["github_api"]({"method": "POST", "path": "/repos/x/y/issues", "body": {"title": "a"}}))
    assert git_out["ok"] is False and git_out.get("needs_approval") is True
    assert gh_out["ok"] is False and gh_out.get("needs_approval") is True


def test_schedule_task_lifecycle() -> None:
    scheduler = Scheduler(llm_client=None, max_in_flight=1)  # type: ignore[arg-type]

    async def _run():
        task_id = await scheduler.schedule_task_in(0.01, "hello", {})
        assert task_id in scheduler.list_scheduled_tasks()
        cancelled = await scheduler.cancel_scheduled_task(task_id)
        assert cancelled is True

    asyncio.run(_run())
