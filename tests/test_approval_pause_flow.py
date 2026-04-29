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
        return {"type": "turn_done", "content": "done"}


class _SchedulerFakeLLM:
    def __init__(self) -> None:
        self._calls = 0

    async def infer(self, messages):  # noqa: ANN001
        _ = messages
        self._calls += 1
        if self._calls == 2:
            return {"type": "tool", "tool_name": "read_file", "args": {"path": "/tmp/x.txt"}}
        return {"type": "turn_done", "content": "done"}


class _FakeStreamLLM:
    def __init__(self) -> None:
        self._calls = 0

    async def infer(self, messages):  # noqa: ANN001
        self._calls += 1
        if self._calls == 1:
            return {"type": "tool", "tool_name": "run_python", "args": {"command": "python3 --version"}}
        return {"type": "turn_done", "content": "done"}


async def _needs_approval_tool(args):  # noqa: ANN001
    return {
        "ok": False,
        "needs_approval": True,
        "request_id": "req-1",
        "error": "Path not in allowed scope",
    }


def test_scheduler_session_worker_can_resume_approval_paused_prompt(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_SchedulerFakeLLM(), max_in_flight=1)

    async def _wait_status(prompt_id: str, expected: set[str], timeout_s: float = 3.0) -> str:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            status = scheduler.prompts[prompt_id].status
            if status in expected:
                return status
            await asyncio.sleep(0.02)
        raise AssertionError(f"prompt {prompt_id} not in expected status {expected}")

    async def _run() -> None:
        await scheduler.start()
        prompt_id = await scheduler.create_session_prompt(
            "need approval then resume",
            tools={"read_file": _needs_approval_tool},
            session_id="session-approval",
        )
        paused_status = await _wait_status(prompt_id, {"paused", "failed"})
        assert paused_status == "paused"
        assert scheduler.prompts[prompt_id].local_state.get("pending_approval_request_id") == "req-1"

        await scheduler.resume(prompt_id)
        final_status = await _wait_status(prompt_id, {"completed", "failed", "cancelled"})
        assert final_status == "completed"

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())


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
