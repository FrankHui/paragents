from __future__ import annotations

from session_runtime import DefaultPromptAssembler


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
