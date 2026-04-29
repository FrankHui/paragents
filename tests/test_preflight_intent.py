from __future__ import annotations

import asyncio

from preflight_intent import infer_preflight_intent, infer_preflight_intent_with_llm
from scheduler import Scheduler


class _SleepLLM:
    async def infer(self, messages):  # noqa: ANN001
        await asyncio.sleep(0.2)
        return {"type": "turn_done", "content": "ok"}


class _PreflightLLM:
    async def infer(self, messages):  # noqa: ANN001
        _ = messages
        return {
            "content": (
                '{"capabilities":["python"],"actions":[],"output_paths":["reports/llm-out.txt"],'
                '"confidence":0.91,"notes":["llm inferred output"]}'
            )
        }


def test_infer_preflight_intent_detects_implicit_git_and_output() -> None:
    text = "请把这次代码提交，并输出到 reports/result.json"
    intent = infer_preflight_intent(text)
    keys = intent.to_resource_keys()

    assert "perm:git" in keys
    assert "git:commit" in keys
    assert "out:reports/result.json" in keys
    assert intent.confidence >= 0.6


def test_submit_persists_preflight_intent_in_prompt_local_state() -> None:
    scheduler = Scheduler(llm_client=None)
    prompt_id = asyncio.run(
        scheduler.create_session_prompt(
            "把这次代码提交，并输出到 reports/result.json",
            tools={},
            session_id="session-a",
        )
    )
    prompt = scheduler.prompts[prompt_id]
    intent_state = prompt.local_state.get("preflight_intent", {})

    assert isinstance(intent_state, dict)
    assert "git" in intent_state.get("capabilities", [])
    assert "git:commit" in prompt.local_state.get("preflight_resource_keys", [])
    assert "out:reports/result.json" in prompt.local_state.get("preflight_resource_keys", [])


def test_continue_task_recomputes_preflight_intent() -> None:
    scheduler = Scheduler(llm_client=None)
    prompt_id = asyncio.run(
        scheduler.create_session_prompt(
            "写一段普通文本",
            tools={},
            session_id="session-a",
        )
    )
    prompt = scheduler.prompts[prompt_id]
    prompt.status = "completed"
    ok = asyncio.run(scheduler.continue_session_prompt(prompt_id, "把这次代码提交，并输出到 reports/new.json"))
    assert ok is True

    intent_state = prompt.local_state.get("preflight_intent", {})
    keys = prompt.local_state.get("preflight_resource_keys", [])
    assert "git" in intent_state.get("capabilities", [])
    assert "git:commit" in keys
    assert "out:reports/new.json" in keys


def test_infer_preflight_intent_with_llm_augments_rule_result() -> None:
    intent = asyncio.run(infer_preflight_intent_with_llm("普通一句话", _PreflightLLM()))
    keys = intent.to_resource_keys()
    assert "perm:python" in keys
    assert "out:reports/llm-out.txt" in keys
    assert intent.confidence >= 0.9


def test_dispatch_not_blocked_by_unrelated_running_prompt_in_blocker_session() -> None:
    scheduler = Scheduler(llm_client=_SleepLLM(), max_in_flight=2)

    async def _run() -> None:
        old_id = await scheduler.create_session_prompt("git commit -m 'x'", tools={}, session_id="session-A")
        scheduler.prompts[old_id].status = "completed"
        await scheduler.create_session_prompt("echo hello", tools={}, session_id="session-A")
        target_id = await scheduler.create_session_prompt("把这次代码提交", tools={}, session_id="session-B")
        await scheduler.start()
        await asyncio.sleep(0.1)
        target_status = scheduler.prompts[target_id].status
        assert target_status in {"running", "completed"}
        if scheduler._dispatch_task is not None:  # noqa: SLF001
            scheduler._dispatch_task.cancel()  # noqa: SLF001

    asyncio.run(_run())
