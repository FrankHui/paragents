from __future__ import annotations

import asyncio
import ast
import fnmatch
import operator
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp_runtime import MCPRegistry
from permissions import PermissionManager

ToolFunc = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
SubmitSubagentFunc = Callable[[str, dict[str, ToolFunc]], Awaitable[str]]
ScheduleFunc = Callable[[float, str, dict[str, ToolFunc]], Awaitable[str]]
CancelScheduledFunc = Callable[[str], Awaitable[bool]]
ListScheduledFunc = Callable[[], dict[str, float]]


def _approval_meta(permission_manager: PermissionManager, request_id: str | None) -> dict[str, Any]:
    if not request_id:
        return {}
    req = permission_manager.get_pending(request_id)
    if req is None:
        return {}
    return {
        "approval_type": req.request_type,
        "approval_payload": dict(req.payload),
    }


class ToolRegistry:
    def __init__(self, allowed_tools: dict[str, ToolFunc]) -> None:
        self._allowed_tools = allowed_tools

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self._allowed_tools:
            raise PermissionError(f"Tool '{name}' is not allowed for this agent.")
        return await self._allowed_tools[name](args)

    def list_tool_names(self) -> list[str]:
        return list(self._allowed_tools.keys())


async def calculator_tool(args: dict[str, Any]) -> dict[str, Any]:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return {"ok": False, "error": "Missing expression"}

    try:
        value = _safe_eval(expression)
        return {"ok": True, "value": value}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def create_main_tools(
    permission_manager: PermissionManager,
    submit_subagent: SubmitSubagentFunc | None,
    mcp_registry: MCPRegistry | None,
    mcp_enabled_tools: dict[str, list[str]] | None = None,
    schedule_task_in: ScheduleFunc | None = None,
    cancel_scheduled_task: CancelScheduledFunc | None = None,
    list_scheduled_tasks: ListScheduledFunc | None = None,
) -> dict[str, ToolFunc]:
    tools = {
        "calculator": calculator_tool,
        "read_file": _build_read_file_tool(permission_manager),
        "list_dir": _build_list_dir_tool(permission_manager),
        "glob": _build_glob_tool(permission_manager),
        "grep": _build_grep_tool(permission_manager),
        "run_command": _build_run_command_tool(permission_manager),
        "run_python": _build_run_python_tool(permission_manager),
        "spawn": _build_spawn_tool(permission_manager, submit_subagent),
        "git_status": _build_git_tool(permission_manager, "status"),
        "git_diff": _build_git_tool(permission_manager, "diff"),
        "git_log": _build_git_tool(permission_manager, "log"),
        "git_add": _build_git_tool(permission_manager, "add"),
        "git_commit": _build_git_tool(permission_manager, "commit"),
        "git_push": _build_git_tool(permission_manager, "push"),
        "github_api": _build_github_api_tool(permission_manager),
        "schedule_task": _build_schedule_task_tool(schedule_task_in),
        "list_scheduled_tasks": _build_list_scheduled_tool(list_scheduled_tasks),
        "cancel_scheduled_task": _build_cancel_scheduled_tool(cancel_scheduled_task),
    }
    if permission_manager.check_capability("mcp") and mcp_registry is not None:
        tools.update(mcp_registry.build_wrapped_tools(mcp_enabled_tools))
    return tools


def create_subagent_tools(permission_manager: PermissionManager) -> dict[str, ToolFunc]:
    return {
        "calculator": calculator_tool,
        "read_file": _build_read_file_tool(permission_manager),
        "list_dir": _build_list_dir_tool(permission_manager),
        "glob": _build_glob_tool(permission_manager),
        "grep": _build_grep_tool(permission_manager),
        "run_command": _build_run_command_tool(permission_manager),
        "run_python": _build_run_python_tool(permission_manager),
    }


def create_default_tools(permission_manager: PermissionManager) -> dict[str, ToolFunc]:
    return create_main_tools(permission_manager, submit_subagent=None, mcp_registry=None)


def _build_read_file_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await read_file_tool(args, permission_manager)

    return _tool


def _build_list_dir_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await list_dir_tool(args, permission_manager)

    return _tool


def _build_glob_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await glob_tool(args, permission_manager)

    return _tool


def _build_grep_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await grep_tool(args, permission_manager)

    return _tool


def _build_run_command_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await run_command_tool(args, permission_manager)

    return _tool


def _build_run_python_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        return await run_python_tool(args, permission_manager)

    return _tool


def _build_spawn_tool(permission_manager: PermissionManager, submit_subagent: SubmitSubagentFunc | None) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        if submit_subagent is None:
            return {"ok": False, "error": "spawn submitter unavailable"}
        input_text = str(args.get("input", "")).strip()
        if not input_text:
            return {"ok": False, "error": "Missing input"}
        sub_tools = create_subagent_tools(permission_manager)
        task_id = await submit_subagent(input_text, sub_tools)
        return {"ok": True, "task_id": task_id}

    return _tool


def _build_git_tool(permission_manager: PermissionManager, action: str) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        decision = permission_manager.check_git_action(action)
        if not decision.allowed:
            if decision.request_id:
                return {
                    "ok": False,
                    "needs_approval": True,
                    "request_id": decision.request_id,
                    "error": decision.reason,
                    **_approval_meta(permission_manager, decision.request_id),
                }
            return {"ok": False, "error": decision.reason or "git action denied"}
        if action == "push":
            remote = str(args.get("remote", "origin"))
            branch = str(args.get("branch", "main"))
            command = f"git push {remote} {branch}"
            return await run_command_tool({"command": command}, permission_manager)
        if action == "status":
            return await run_command_tool({"command": "git status --short"}, permission_manager)
        if action == "diff":
            return await run_command_tool({"command": "git diff"}, permission_manager)
        if action == "log":
            return await run_command_tool({"command": "git log --oneline -n 20"}, permission_manager)
        if action == "add":
            pathspec = str(args.get("pathspec", "."))
            return await run_command_tool({"command": f"git add {pathspec}"}, permission_manager)
        if action == "commit":
            message = str(args.get("message", "")).strip()
            if not message:
                return {"ok": False, "error": "Missing commit message"}
            return await run_command_tool({"command": f"git commit -m \"{message}\""}, permission_manager)
        return {"ok": False, "error": f"Unsupported git action: {action}"}

    return _tool


def _build_github_api_tool(permission_manager: PermissionManager) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        method = str(args.get("method", "GET")).upper()
        path = str(args.get("path", "")).strip()
        if not path:
            return {"ok": False, "error": "Missing path"}
        decision = permission_manager.check_github_request(method, path)
        if not decision.allowed:
            if decision.request_id:
                return {
                    "ok": False,
                    "needs_approval": True,
                    "request_id": decision.request_id,
                    "error": decision.reason,
                    **_approval_meta(permission_manager, decision.request_id),
                }
            return {"ok": False, "error": decision.reason or "github request denied"}
        # MVP: 先返回模拟成功结果，后续可接真实 GitHub API
        return {"ok": True, "method": method, "path": path, "body": args.get("body")}

    return _tool


def _build_schedule_task_tool(schedule_task_in: ScheduleFunc | None) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        if schedule_task_in is None:
            return {"ok": False, "error": "scheduler unavailable"}
        delay_s = float(args.get("delay_s", 60) or 60)
        input_text = str(args.get("input", "")).strip()
        if not input_text:
            return {"ok": False, "error": "Missing input"}
        task_id = await schedule_task_in(delay_s, input_text, {})
        return {"ok": True, "scheduled_task_id": task_id}

    return _tool


def _build_list_scheduled_tool(list_scheduled_tasks: ListScheduledFunc | None) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        if list_scheduled_tasks is None:
            return {"ok": False, "error": "scheduler unavailable"}
        return {"ok": True, "items": list_scheduled_tasks()}

    return _tool


def _build_cancel_scheduled_tool(cancel_scheduled_task: CancelScheduledFunc | None) -> ToolFunc:
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        if cancel_scheduled_task is None:
            return {"ok": False, "error": "scheduler unavailable"}
        task_id = str(args.get("task_id", "")).strip()
        if not task_id:
            return {"ok": False, "error": "Missing task_id"}
        ok = await cancel_scheduled_task(task_id)
        return {"ok": ok}

    return _tool


async def read_file_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    cap_decision = permission_manager.check_capability_decision("filesystem")
    if not cap_decision.allowed:
        return {
            "ok": False,
            "error": cap_decision.reason or "filesystem capability disabled",
            "needs_approval": True,
            "request_id": cap_decision.request_id,
        }

    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return {"ok": False, "error": "Missing path"}

    max_chars = int(args.get("max_chars", 4000) or 4000)
    max_chars = max(200, min(max_chars, 20000))

    try:
        path = Path(raw_path).expanduser().resolve()
        decision = permission_manager.check_fs_access(str(path), "read")
        if not decision.allowed:
            return {
                "ok": False,
                "error": decision.reason,
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }
        if not path.exists():
            return {"ok": False, "error": f"File not found: {path}"}
        if not path.is_file():
            return {"ok": False, "error": f"Not a file: {path}"}

        content = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]

        return {
            "ok": True,
            "path": str(path),
            "truncated": truncated,
            "content": content,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def list_dir_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    cap_decision = permission_manager.check_capability_decision("filesystem")
    if not cap_decision.allowed:
        return {
            "ok": False,
            "error": cap_decision.reason or "filesystem capability disabled",
            "needs_approval": True,
            "request_id": cap_decision.request_id,
        }

    raw_path = str(args.get("path", ".")).strip() or "."
    recursive = bool(args.get("recursive", False))
    max_entries = int(args.get("max_entries", 200) or 200)
    max_entries = max(1, min(max_entries, 2000))

    try:
        path = Path(raw_path).expanduser().resolve()
        decision = permission_manager.check_fs_access(str(path), "read")
        if not decision.allowed:
            return {
                "ok": False,
                "error": decision.reason,
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }
        if not path.exists():
            return {"ok": False, "error": f"Path not found: {path}"}
        if not path.is_dir():
            return {"ok": False, "error": f"Not a directory: {path}"}

        items: list[dict[str, Any]] = []
        iterator = path.rglob("*") if recursive else path.iterdir()
        for entry in iterator:
            if len(items) >= max_entries:
                break
            entry_type = "dir" if entry.is_dir() else "file"
            size = entry.stat().st_size if entry.is_file() else None
            items.append({"path": str(entry), "type": entry_type, "size": size})
        return {"ok": True, "path": str(path), "items": items, "truncated": len(items) >= max_entries}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def glob_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    cap_decision = permission_manager.check_capability_decision("filesystem")
    if not cap_decision.allowed:
        return {
            "ok": False,
            "error": cap_decision.reason or "filesystem capability disabled",
            "needs_approval": True,
            "request_id": cap_decision.request_id,
        }

    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return {"ok": False, "error": "Missing pattern"}
    base_dir_raw = str(args.get("base_dir", ".")).strip() or "."
    max_results = int(args.get("max_results", 200) or 200)
    max_results = max(1, min(max_results, 2000))

    try:
        base_dir = Path(base_dir_raw).expanduser().resolve()
        decision = permission_manager.check_fs_access(str(base_dir), "read")
        if not decision.allowed:
            return {
                "ok": False,
                "error": decision.reason,
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }
        if not base_dir.exists() or not base_dir.is_dir():
            return {"ok": False, "error": f"Invalid base_dir: {base_dir}"}

        matches: list[str] = []
        for entry in base_dir.rglob("*"):
            rel = entry.relative_to(base_dir).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(entry.name, pattern):
                matches.append(str(entry))
            if len(matches) >= max_results:
                break
        return {"ok": True, "matches": matches, "truncated": len(matches) >= max_results}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def grep_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    cap_decision = permission_manager.check_capability_decision("filesystem")
    if not cap_decision.allowed:
        return {
            "ok": False,
            "error": cap_decision.reason or "filesystem capability disabled",
            "needs_approval": True,
            "request_id": cap_decision.request_id,
        }

    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return {"ok": False, "error": "Missing pattern"}
    path_raw = str(args.get("path", ".")).strip() or "."
    file_glob = str(args.get("file_glob", "*")).strip() or "*"
    max_matches = int(args.get("max_matches", 100) or 100)
    max_matches = max(1, min(max_matches, 2000))

    try:
        root = Path(path_raw).expanduser().resolve()
        decision = permission_manager.check_fs_access(str(root), "read")
        if not decision.allowed:
            return {
                "ok": False,
                "error": decision.reason,
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }

        regex = re.compile(pattern)
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        results: list[dict[str, Any]] = []
        for file_path in files:
            if not fnmatch.fnmatch(file_path.name, file_glob):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for idx, line in enumerate(lines, start=1):
                if regex.search(line):
                    results.append({"path": str(file_path), "line": idx, "content": line})
                    if len(results) >= max_matches:
                        return {"ok": True, "matches": results, "truncated": True}
        return {"ok": True, "matches": results, "truncated": False}
    except re.error as exc:
        return {"ok": False, "error": f"Invalid regex: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def run_command_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        return {"ok": False, "error": "Missing command"}

    # 对 python/python3 命令走独立工具，避免额外 shell 权限步骤。
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    if tokens and tokens[0] in {"python", "python3"}:
        return await run_python_tool({"command": command, "timeout_s": args.get("timeout_s", 10)}, permission_manager)

    cap_decision = permission_manager.check_capability_decision("shell")
    if not cap_decision.allowed:
        return {
            "ok": False,
            "error": cap_decision.reason or "shell capability disabled",
            "needs_approval": True,
            "request_id": cap_decision.request_id,
            **_approval_meta(permission_manager, cap_decision.request_id),
        }

    decision = permission_manager.check_shell_command(command)
    if not decision.allowed:
        if decision.request_id:
            return {
                "ok": False,
                "error": decision.reason or "Command needs approval",
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }
        return {"ok": False, "error": decision.reason or "Command blocked"}

    timeout_s = float(args.get("timeout_s", 10) or 10)
    timeout_s = max(1, min(timeout_s, 120))
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": f"Command timeout after {timeout_s}s"}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode or 0),
            "stdout": out[:8000],
            "stderr": err[:8000],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def run_python_tool(args: dict[str, Any], permission_manager: PermissionManager) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        script = str(args.get("script", "")).strip()
        if not script:
            return {"ok": False, "error": "Missing python command or script"}
        py = str(args.get("python_bin", "python3")).strip() or "python3"
        extra_args = args.get("args", [])
        if isinstance(extra_args, list):
            safe_args = [str(x) for x in extra_args]
        else:
            safe_args = []
        command = " ".join([py, script, *safe_args]).strip()

    decision = permission_manager.check_python_command(command)
    if not decision.allowed:
        if decision.request_id:
            return {
                "ok": False,
                "error": decision.reason or "Python command needs approval",
                "needs_approval": True,
                "request_id": decision.request_id,
                **_approval_meta(permission_manager, decision.request_id),
            }
        return {"ok": False, "error": decision.reason or "Python command blocked"}

    timeout_s = float(args.get("timeout_s", 10) or 10)
    timeout_s = max(1, min(timeout_s, 120))

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return {"ok": False, "error": f"Invalid python command: {exc}"}
    if not tokens:
        return {"ok": False, "error": "Invalid python command"}
    if tokens[0] not in {"python", "python3"}:
        return {"ok": False, "error": "run_python only supports python/python3 entrypoint"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *tokens,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": f"Python command timeout after {timeout_s}s"}

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode or 0),
            "stdout": out[:8000],
            "stderr": err[:8000],
            "mode": "python",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(expression: str) -> float:
    node = ast.parse(expression, mode="eval")
    return float(_eval_node(node.body))


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BIN_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _ALLOWED_BIN_OPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _eval_node(node.operand)
        return _ALLOWED_UNARY_OPS[op_type](operand)

    raise ValueError(f"Unsafe expression node: {type(node).__name__}")
