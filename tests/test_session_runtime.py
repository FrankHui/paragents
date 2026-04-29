from __future__ import annotations

from session_runtime import DefaultCompactionEngine, DefaultPromptAssembler, JsonlSessionStateStore


def test_prompt_assembler_places_current_user_input_last() -> None:
    assembler = DefaultPromptAssembler()
    snapshot = {
        "recent_turns": [
            {"role": "user", "content": "run_command：echo round-1"},
            {"role": "assistant", "content": "round-1"},
        ],
        "compact_notes": [],
    }

    messages = assembler.build_messages(
        system_prompt="system",
        user_input="run_command：echo round-2",
        context_snapshot=snapshot,
    )

    assert messages[-1] == {"role": "user", "content": "run_command：echo round-2"}


def test_compaction_engine_split_should_compact_and_compact() -> None:
    engine = DefaultCompactionEngine(max_recent_turns=2, max_compact_notes=8, max_memory_items=3)
    snapshot = {
        "recent_turns": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
        "compact_notes": [],
    }
    assert engine.should_compact(snapshot) is True
    compacted = engine.compact(snapshot)
    assert len(compacted["recent_turns"]) <= 2
    assert compacted["compact_notes"]
    assert engine.cap_memory_items([1, 2, 3, 4]) == [2, 3, 4]


def test_jsonl_session_state_store_persists_and_recovers(tmp_path) -> None:  # noqa: ANN001
    store = JsonlSessionStateStore(tmp_path / "session_runtime_state.jsonl")
    state = store.load("session-a")
    state.memory_summary = "first"
    store.save("session-a", state)
    store.merge_turn_delta(
        "session-a",
        context_snapshot={"recent_turns": [{"role": "user", "content": "hello"}], "compact_notes": ["n1"]},
        memory_items=[{"k": "v"}],
        memory_summary="summary-2",
    )
    store.save_checkpoint("session-a", {"ck": 1})

    store_reloaded = JsonlSessionStateStore(tmp_path / "session_runtime_state.jsonl")
    loaded = store_reloaded.load("session-a")
    assert loaded.memory_summary == "summary-2"
    assert loaded.recent_turns and loaded.recent_turns[0]["content"] == "hello"
    assert loaded.compact_notes == ["n1"]
    assert loaded.checkpoint == {"ck": 1}
