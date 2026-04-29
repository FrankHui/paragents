from __future__ import annotations

import asyncio

from scheduler import Scheduler


def test_debug_sequential_prompt_ids(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PARAGENTS_DEBUG_SEQUENTIAL_PROMPT_IDS", "1")
    scheduler = Scheduler(llm_client=None)

    first_id = asyncio.run(scheduler.create_session_prompt("echo 1", tools={}, session_id="session-a"))
    second_id = asyncio.run(scheduler.create_session_prompt("echo 2", tools={}, session_id="session-a"))

    assert first_id == "debug-prompt-000001"
    assert second_id == "debug-prompt-000002"
