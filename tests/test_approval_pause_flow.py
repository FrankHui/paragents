from __future__ import annotations

import asyncio

from agent_instance import AgentInstance
from scheduler import Scheduler
from task import Prompt
def Task(**kwargs):  # type: ignore[misc]
    if "task_id" in kwargs:
        kwargs["prompt_id"] = kwargs.pop("task_id")
    if "task_ref" in kwargs:
        kwargs["prompt_ref"] = kwargs.pop("task_ref")
    p = Prompt(**kwargs)
    p.task_id = p.prompt_id  # type: ignore[attr-defined]
    p.task_ref = p.prompt_ref  # type: ignore[attr-defined]
    return p

from tools import ToolRegistry


class _FakeLLM:
    def __init__(self) -> None:
        self._calls = 0

    async def infer(self, messages):  # noqa: ANN001
        self._calls += 1
        if self._calls == 1:
            return {"type": "tool", "tool_name": "read_file", "args": {"path": "/tmp/x.txt"}}
        return {"type": "final", "content": "done"}


class _FakeStreamLLM:
    def __init__(self) -> None:
        self._calls = 0

    async def infer(self, messages):  # noqa: ANN001
        self._calls += 1
        if self._calls == 1:
            return {"type": "tool", "tool_name": "run_python", "args": {"command": "python3 --version"}}
        return {"type": "final", "content": "done"}


async def _needs_approval_tool(args):  # noqa: ANN001
    return {
        "ok": False,
        "needs_approval": True,
        "request_id": "req-1",
        "error": "Path not in allowed scope",
    }


def test_agent_pauses_on_needs_approval() -> None:
    task = Task(task_id="t1", input="read /tmp/x.txt")
    agent = AgentInstance(
        task=task,
        llm_client=_FakeLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"read_file": _needs_approval_tool}),
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert task.status == "paused"
    assert task.local_state.get("pending_approval_request_id") == "req-1"


def test_scheduler_resume_requeues_approval_paused_task() -> None:
    scheduler = Scheduler(llm_client=None, max_in_flight=1)
    task = Task(task_id="t1", input="x", status="paused")
    task.local_state["pending_approval_request_id"] = "req-1"
    scheduler.prompts["t1"] = task

    asyncio.run(scheduler.resume("t1"))
    assert scheduler.prompts["t1"].status == "pending"


async def _stream_tool(args):  # noqa: ANN001
    return {"ok": True, "stdout": "line-a\nline-b", "stderr": "warn-1\nwarn-2"}


def test_agent_emits_stdout_stderr_preview_for_run_python() -> None:
    task = Task(task_id="t2", input="run py")
    events: list[str] = []
    agent = AgentInstance(
        task=task,
        llm_client=_FakeStreamLLM(),  # type: ignore[arg-type]
        tools=ToolRegistry({"run_python": _stream_tool}),
        event_callback=events.append,
    )
    asyncio.run(agent.run(cancel_event=asyncio.Event()))
    assert any("run_python stdout:" in e for e in events)
    assert any("run_python stderr:" in e for e in events)
