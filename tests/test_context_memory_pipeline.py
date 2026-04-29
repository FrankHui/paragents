from __future__ import annotations

import asyncio

from agent_instance import AgentInstance
from memory import LayeredContext, summarize_local_memory
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


class _TwoToolThenFinalLLM:
    def __init__(self) -> None:
        self._calls = 0

    async def infer(self, messages):  # noqa: ANN001
        self._calls += 1
        if self._calls <= 2:
            return {"type": "tool", "tool_name": "run_command", "args": {"command": "echo hi"}}
        return {"type": "final", "content": "done"}


async def _ok_tool(args):  # noqa: ANN001
    return {"ok": True, "stdout": "hello", "stderr": ""}


def test_layered_context_micro_compact() -> None:
    ctx = LayeredContext("system", "user-input", max_recent_turns=2)
    ctx.append_turn("assistant", "a1")
    ctx.append_turn("user", "u1")
    ctx.append_turn("assistant", "a2")
    snapshot = ctx.snapshot()
    assert len(snapshot["compact_notes"]) >= 1
    exported = ctx.export_for_infer()
    assert any("Compact memory notes" in item["content"] for item in exported if item["role"] == "system")


def test_agent_writes_memory_summary_and_context_snapshot() -> None:
    task = Task(task_id="t-memory", input="run")
    agent = AgentInstance(
        task=task,
        llm_client=_TwoToolThenFinalLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_command": _ok_tool}),
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "completed"
    assert "memory_summary" in task.local_state
    assert "context_snapshot" in task.local_state
    assert "run_command:ok" in task.local_state["memory_summary"]
    snapshot = task.local_state["context_snapshot"]
    assert any(
        "Tool observation for run_command" in turn.get("content", "")
        for turn in snapshot.get("recent_turns", [])
    )


def test_summarize_local_memory_uses_latest_items() -> None:
    items = [
        {"tool": "a", "observation": {"ok": True}},
        {"tool": "b", "observation": {"ok": False}},
    ]
    summary = summarize_local_memory(items, limit=2)
    assert "a:ok" in summary
    assert "b:non-ok" in summary
