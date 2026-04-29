from __future__ import annotations

import asyncio
from pathlib import Path

from scheduler import Scheduler


class _OneToolThenFinalLLM:
    async def infer(self, messages):  # noqa: ANN001
        for message in messages:
            if "Tool observation for run_command" in str(message.get("content", "")):
                return {"type": "turn_done", "content": "done"}
        return {"type": "tool", "tool_name": "run_command", "args": {"command": "echo hi"}}


class _AlwaysFinalLLM:
    async def infer(self, messages):  # noqa: ANN001
        _ = messages
        return {"type": "turn_done", "content": "turn done"}


async def _ok_tool(args):  # noqa: ANN001
    _ = args
    return {"ok": True, "stdout": "hello", "stderr": "", "exit_code": 0}


def test_session_context_and_memory_are_continuous_across_continue_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_OneToolThenFinalLLM(), max_in_flight=1)

    async def _wait_completed(prompt_id: str, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            status = scheduler.prompts[prompt_id].status
            if status in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"prompt {prompt_id} not completed in time")

    async def _run() -> None:
        await scheduler.start()
        prompt_id = await scheduler.create_session_prompt(
            "run command 1",
            tools={"run_command": _ok_tool},
            session_id="session-ctx",
        )
        await _wait_completed(prompt_id)

        for idx in range(2, 7):
            ok = await scheduler.continue_session_prompt(prompt_id, f"run command {idx}")
            assert ok is True
            await _wait_completed(prompt_id)

        state = scheduler._session_state_store.load("session-ctx")  # noqa: SLF001
        assert state.compact_notes, "expected compact notes after multiple continues"
        assert "run_command:ok" in state.memory_summary
        assert any("Tool observation for run_command" in str(turn.get("content", "")) for turn in state.recent_turns) or any(
            "Tool observation for run_command" in note for note in state.compact_notes
        )

        latest_prompt = scheduler.prompts[prompt_id]
        snapshot = latest_prompt.local_state.get("context_snapshot", {})
        assert snapshot.get("compact_notes", [])
        assert "run_command:ok" in str(latest_prompt.local_state.get("memory_summary", ""))

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())


def test_compact_can_trigger_without_tool_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_AlwaysFinalLLM(), max_in_flight=1)

    async def _wait_completed(prompt_id: str, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            status = scheduler.prompts[prompt_id].status
            if status in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"prompt {prompt_id} not completed in time")

    async def _run() -> None:
        await scheduler.start()
        prompt_id = await scheduler.create_session_prompt(
            "just respond",
            tools={},
            session_id="session-final-only",
        )
        await _wait_completed(prompt_id)

        for idx in range(2, 8):
            ok = await scheduler.continue_session_prompt(prompt_id, f"just respond {idx}")
            assert ok is True
            await _wait_completed(prompt_id)

        state = scheduler._session_state_store.load("session-final-only")  # noqa: SLF001
        assert state.compact_notes, "compact should trigger based on session context size, not tool usage only"
        assert any(turn.get("role") == "assistant" for turn in state.recent_turns)

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())


def test_continue_reuses_agent_instance_without_restarting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_AlwaysFinalLLM(), max_in_flight=1)

    async def _wait_completed(prompt_id: str, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            status = scheduler.prompts[prompt_id].status
            if status in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"prompt {prompt_id} not completed in time")

    async def _run() -> None:
        await scheduler.start()
        prompt_id = await scheduler.create_session_prompt("first", tools={}, session_id="session-reuse")
        await _wait_completed(prompt_id)
        assert await scheduler.continue_session_prompt(prompt_id, "second")
        await _wait_completed(prompt_id)
        assert await scheduler.continue_session_prompt(prompt_id, "third")
        await _wait_completed(prompt_id)

        logs = scheduler.get_prompt_logs(prompt_id, limit=400)
        started_count = sum(1 for line in logs if "agent started" in line)
        resumed_count = sum(1 for line in logs if "agent resumed turn" in line)
        assert started_count == 1
        assert resumed_count >= 1

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())


def test_same_session_new_prompt_reuses_single_agent_instance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_AlwaysFinalLLM(), max_in_flight=1)

    async def _wait_completed(prompt_id: str, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            status = scheduler.prompts[prompt_id].status
            if status in {"completed", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"prompt {prompt_id} not completed in time")

    async def _run() -> None:
        await scheduler.start()
        first_id = await scheduler.create_session_prompt("first prompt", tools={}, session_id="session-reuse-2")
        await _wait_completed(first_id)
        second_id = await scheduler.create_session_prompt("second prompt", tools={}, session_id="session-reuse-2")
        await _wait_completed(second_id)

        logs_first = scheduler.get_prompt_logs(first_id, limit=200)
        logs_second = scheduler.get_prompt_logs(second_id, limit=200)
        started_count = sum(1 for line in logs_first + logs_second if "agent started" in line)
        resumed_count = sum(1 for line in logs_first + logs_second if "agent resumed turn" in line)
        assert started_count == 1
        assert resumed_count >= 1

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())
