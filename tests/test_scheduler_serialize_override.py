from __future__ import annotations

import asyncio
from pathlib import Path

from scheduler import Scheduler


class _SleepLLM:
    async def infer(self, messages):  # noqa: ANN001
        await asyncio.sleep(0.2)
        return {"type": "final", "content": "ok"}


def test_conflict_prompt_can_continue_after_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_SleepLLM(), max_in_flight=2)

    async def _run() -> None:
        await scheduler.start()
        first_id = await scheduler.submit("python a.py > reports/result.json", tools={}, session_id="session-A")
        second_id = await scheduler.submit("python b.py > reports/result.json", tools={}, session_id="session-B")

        await asyncio.sleep(0.08)
        first = scheduler.prompts[first_id]
        second = scheduler.prompts[second_id]
        assert first.status in {"running", "completed"}
        assert second.status == "pending"

        second.local_state["preflight_user_override"] = True
        await asyncio.sleep(0.08)
        assert second.status in {"running", "completed"}

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())


def test_conflict_prompt_does_not_auto_run_after_blocker_finishes_without_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=_SleepLLM(), max_in_flight=2)

    async def _run() -> None:
        await scheduler.start()
        await scheduler.submit("python a.py > reports/result.json", tools={}, session_id="session-A")
        second_id = await scheduler.submit("python b.py > reports/result.json", tools={}, session_id="session-B")
        await asyncio.sleep(0.35)
        second = scheduler.prompts[second_id]
        assert second.status == "pending"
        assert second.local_state.get("preflight_decision_required") is True
        assert second.local_state.get("preflight_user_override") is False

        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())
