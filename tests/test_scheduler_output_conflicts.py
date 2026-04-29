from __future__ import annotations

import asyncio
from pathlib import Path

from scheduler import Scheduler


def test_extract_output_paths_handles_redirection_and_write_verbs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=None)
    text = "python build.py > out/result.json && tee logs/run.log && cp src/a.py dist/a.py && mkdir -p tmp/cache"
    outputs = scheduler._extract_output_paths(text)

    assert "out/result.json" in outputs
    assert "logs/run.log" in outputs
    assert "dist/a.py" in outputs
    assert "tmp/cache" in outputs


def test_extract_output_paths_handles_tee_and_cp_mv_with_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=None)
    text = "echo hi | tee -a logs/run.log && cp -r src_dir dst_dir && mv -f old.txt new.txt"
    outputs = scheduler._extract_output_paths(text)

    assert "logs/run.log" in outputs
    assert "dst_dir" in outputs
    assert "new.txt" in outputs
    assert "-a" not in outputs
    assert "src_dir" not in outputs


def test_submit_records_output_conflict_details_and_declared_outputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=None)

    first_id = asyncio.run(scheduler.submit("python a.py > reports/result.json", tools={}, session_id="session-A"))
    second_id = asyncio.run(scheduler.submit("python b.py > reports/result.json", tools={}, session_id="session-B"))

    first = scheduler.prompts[first_id]
    second = scheduler.prompts[second_id]

    assert first.local_state["preflight_decision"] == "allow"
    assert second.local_state["preflight_decision"] == "serialize"
    assert second.local_state["preflight_conflict_with_sessions"] == ["session-A"]
    assert second.local_state["preflight_declared_outputs"] == ["reports/result.json"]

    conflict_details = second.local_state.get("preflight_conflict_keys_by_session", {})
    assert "out:reports/result.json" in conflict_details.get("session-A", [])

    assert "reports/result.json" in scheduler._session_declared_outputs["session-A"]  # noqa: SLF001
    assert "reports/result.json" in scheduler._session_declared_outputs["session-B"]  # noqa: SLF001


def test_output_lock_released_when_startup_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scheduler = Scheduler(llm_client=None)

    async def _run() -> None:
        await scheduler.start()
        prompt_id = await scheduler.submit("python a.py > reports/result.json", tools={}, session_id="session-A")
        await asyncio.sleep(0.2)
        prompt = scheduler.prompts[prompt_id]
        assert prompt.status == "failed"
        assert scheduler._output_locks == {}  # noqa: SLF001
        assert prompt_id not in scheduler._cancel_events  # noqa: SLF001
        assert prompt_id not in scheduler._pause_events  # noqa: SLF001
        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())
