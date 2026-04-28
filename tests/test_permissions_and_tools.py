from __future__ import annotations

import asyncio
import json
from pathlib import Path

from permissions import FsScope, PermissionManager, PermissionsConfig
from tools import create_default_tools


def _build_manager(tmp_path: Path, scopes: list[FsScope]) -> PermissionManager:
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": True,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        },
        fs_scopes=scopes,
    )
    return PermissionManager(cfg, tmp_path / "permissions.json")


def _build_disabled_shell_manager(tmp_path: Path, scopes: list[FsScope]) -> PermissionManager:
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": False,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        },
        fs_scopes=scopes,
    )
    return PermissionManager(cfg, tmp_path / "permissions.json")


def test_read_file_allowed_in_scope(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    result = asyncio.run(tools["read_file"]({"path": str(target)}))
    assert result["ok"] is True
    assert result["content"] == "hello"


def test_read_file_out_of_scope_requires_approval(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    target = denied / "private.txt"
    target.write_text("secret", encoding="utf-8")

    manager = _build_manager(tmp_path, [FsScope(path=str(allowed), read=True, write=False)])
    tools = create_default_tools(manager)

    result = asyncio.run(tools["read_file"]({"path": str(target)}))
    assert result["ok"] is False
    assert result["needs_approval"] is True
    request_id = result["request_id"]
    assert request_id

    assert manager.approve(request_id, always=True) is True

    result2 = asyncio.run(tools["read_file"]({"path": str(target)}))
    assert result2["ok"] is True
    persisted = json.loads((tmp_path / "permissions.json").read_text(encoding="utf-8"))
    assert any(Path(s["path"]).resolve() == target.resolve() for s in persisted["fs_scopes"])


def test_list_glob_grep_workflow(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    f1 = tmp_path / "src" / "demo.py"
    f2 = tmp_path / "src" / "readme.md"
    f1.write_text("print('hello')\nname='bob'\n", encoding="utf-8")
    f2.write_text("hello world\n", encoding="utf-8")

    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    list_result = asyncio.run(tools["list_dir"]({"path": str(tmp_path / "src")}))
    assert list_result["ok"] is True
    assert len(list_result["items"]) >= 2

    glob_result = asyncio.run(tools["glob"]({"base_dir": str(tmp_path), "pattern": "**/*.py"}))
    assert glob_result["ok"] is True
    assert any(str(f1) == p for p in glob_result["matches"])

    grep_result = asyncio.run(tools["grep"]({"path": str(tmp_path), "pattern": "name='bob'"}))
    assert grep_result["ok"] is True
    assert any(m["path"] == str(f1) for m in grep_result["matches"])


def test_run_command_blocked_by_policy(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    result = asyncio.run(tools["run_command"]({"command": "rm -rf /tmp/demo"}))
    assert result["ok"] is False
    assert "blocked" in result["error"].lower()


def test_run_command_needs_approval_then_passes(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    result = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    assert result["ok"] is False
    assert result["needs_approval"] is True
    request_id = result["request_id"]
    assert request_id

    assert manager.approve(request_id, always=False) is True
    result2 = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    assert result2["ok"] is True
    assert result2["exit_code"] == 0


def test_run_command_auto_approved(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    result = asyncio.run(tools["run_command"]({"command": "pwd"}))
    assert result["ok"] is True
    assert result["exit_code"] == 0


def test_run_command_python_route_works_without_shell_capability(tmp_path: Path) -> None:
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": False,
            "python": True,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        },
        fs_scopes=[FsScope(path=str(tmp_path), read=True, write=True)],
    )
    manager = PermissionManager(cfg, tmp_path / "permissions.json")
    tools = create_default_tools(manager)

    result = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    assert result["ok"] is True
    assert result["mode"] == "python"


def test_run_command_approve_always_persists_after_reload(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    first = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    assert first["ok"] is False
    assert first["needs_approval"] is True
    request_id = first["request_id"]

    assert manager.approve(request_id, always=True) is True

    config = json.loads((tmp_path / "permissions.json").read_text(encoding="utf-8"))
    loaded = PermissionsConfig(
        capabilities=config["capabilities"],
        fs_scopes=[FsScope(**scope) for scope in config["fs_scopes"]],
        shell_policy=config["shell_policy"],
    )
    reloaded_manager = PermissionManager(loaded, tmp_path / "permissions.json")
    reloaded_tools = create_default_tools(reloaded_manager)

    second = asyncio.run(reloaded_tools["run_command"]({"command": "python3 --version"}))
    assert second["ok"] is True
    assert second["exit_code"] == 0


def test_run_command_pending_request_is_deduplicated(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    first = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    second = asyncio.run(tools["run_command"]({"command": "python3 --version"}))

    assert first["ok"] is False and second["ok"] is False
    assert first["needs_approval"] is True and second["needs_approval"] is True
    assert first["request_id"] == second["request_id"]
    assert len(manager.list_pending()) == 1


def test_shell_capability_disabled_requires_approval_then_allows(tmp_path: Path) -> None:
    manager = _build_disabled_shell_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)

    first = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    assert first["ok"] is False
    assert first["needs_approval"] is True
    request_id = first["request_id"]
    assert request_id

    assert manager.approve(request_id, always=False) is True
    second = asyncio.run(tools["run_command"]({"command": "python3 --version"}))
    # python 命令会走 run_python 路径，审批通过后直接可执行
    assert second["ok"] is True
    assert second.get("mode") == "python"


def test_task_scoped_shell_approval_is_isolated(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    decision_a = manager.check_shell_command("python3 --version", prompt_id="task-A")
    decision_b = manager.check_shell_command("python3 --version", prompt_id="task-B")
    assert decision_a.allowed is False and decision_b.allowed is False
    assert decision_a.request_id and decision_b.request_id
    assert decision_a.request_id != decision_b.request_id

    assert manager.approve(decision_a.request_id, prompt_id="task-A") is True
    allowed_a = manager.check_shell_command("python3 --version", prompt_id="task-A")
    denied_b = manager.check_shell_command("python3 --version", prompt_id="task-B")
    assert allowed_a.allowed is True
    assert denied_b.allowed is False


def test_task_run_dir_is_used_by_run_command(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path, [FsScope(path=str(tmp_path), read=True, write=True)])
    tools = create_default_tools(manager)
    run_dir = tmp_path / "task-run-dir"
    run_dir.mkdir(parents=True, exist_ok=True)

    result = asyncio.run(
        tools["run_command"](
            {
                "command": "pwd",
                "_prompt_id": "task-1",
                "_prompt_run_dir": str(run_dir),
            }
        )
    )
    assert result["ok"] is True
    assert str(run_dir) in result.get("stdout", "")
