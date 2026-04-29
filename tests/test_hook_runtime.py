from __future__ import annotations

import asyncio

from agent_instance import AgentInstance
from hook_runtime import HookRuntime
from task import Prompt
from tools import ToolRegistry


def Task(**kwargs):  # type: ignore[misc]
    if "task_id" in kwargs:
        kwargs["prompt_id"] = kwargs.pop("task_id")
    if "task_ref" in kwargs:
        kwargs["prompt_ref"] = kwargs.pop("task_ref")
    p = Prompt(**kwargs)
    p.task_id = p.prompt_id  # type: ignore[attr-defined]
    p.task_ref = p.prompt_ref  # type: ignore[attr-defined]
    return p


class _FakeToolLLM:
    async def infer(self, messages):  # noqa: ANN001
        return {"type": "tool", "tool_name": "run_command", "args": {"command": "echo hi"}}


class _FakeFinalLLM:
    async def infer(self, messages):  # noqa: ANN001
        return {"type": "turn_done", "content": "ok"}


async def _ok_tool(args):  # noqa: ANN001
    return {"ok": True, "stdout": "ok"}


def test_hook_blocks_on_user_prompt_submit() -> None:
    task = Task(task_id="t1", input="请删除生产数据库")
    hook_runtime = HookRuntime({"block_prompt_patterns": ["删除生产数据库"]})
    agent = AgentInstance(
        task=task,
        llm_client=_FakeFinalLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_command": _ok_tool}),
        hook_runtime=hook_runtime,
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "paused"
    assert task.local_state.get("hook_stage") == "UserPromptSubmit"


def test_hook_blocks_on_pre_tool_use() -> None:
    task = Task(task_id="t2", input="run something")
    hook_runtime = HookRuntime({"block_tools": ["run_command"]})
    agent = AgentInstance(
        task=task,
        llm_client=_FakeToolLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_command": _ok_tool}),
        hook_runtime=hook_runtime,
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "paused"
    assert task.local_state.get("hook_stage") == "PreToolUse"


def test_hook_blocks_on_post_tool_use() -> None:
    task = Task(task_id="t3", input="run something")
    hook_runtime = HookRuntime({"block_post_tools": ["run_command"]})
    agent = AgentInstance(
        task=task,
        llm_client=_FakeToolLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_command": _ok_tool}),
        hook_runtime=hook_runtime,
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "paused"
    assert task.local_state.get("hook_stage") == "PostToolUse"


def test_stop_hook_called_on_completed_path() -> None:
    task = Task(task_id="t4", input="simple final")
    hook_runtime = HookRuntime({"block_stop_reasons": ["completed"]})
    agent = AgentInstance(
        task=task,
        llm_client=_FakeFinalLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_command": _ok_tool}),
        hook_runtime=hook_runtime,
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "paused"
    assert task.local_state.get("hook_stage") == "Stop"
