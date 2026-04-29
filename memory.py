from __future__ import annotations

import json
from typing import Any


class LocalMemory:
    def __init__(self) -> None:
        self._items: list[Any] = []

    async def append(self, item: Any) -> None:
        self._items.append(item)

    async def snapshot(self) -> list[Any]:
        return list(self._items)


class SharedReadonlyMemory:
    def __init__(self, docs: list[str] | None = None) -> None:
        self._docs = docs or []

    async def query(self, keyword: str) -> list[str]:
        keyword_lower = keyword.lower()
        return [d for d in self._docs if keyword_lower in d.lower()]


class LayeredContext:
    def __init__(self, system_prompt: str, user_input: str, max_recent_turns: int = 8) -> None:
        self._system_prompt = system_prompt
        self._user_input = user_input
        self._max_recent_turns = max_recent_turns
        self._recent_turns: list[dict[str, str]] = []
        self._compact_notes: list[str] = []

    def append_turn(self, role: str, content: str) -> None:
        self._recent_turns.append({"role": role, "content": content})
        self._micro_compact_if_needed()

    def _micro_compact_if_needed(self) -> None:
        # 每次超过窗口时，把最早的两轮摘要成一句 compact note。
        while len(self._recent_turns) > self._max_recent_turns:
            chunk = self._recent_turns[:2]
            self._recent_turns = self._recent_turns[2:]
            note = " | ".join(f"{item['role']}:{item['content'][:120]}" for item in chunk)
            self._compact_notes.append(note)
            if len(self._compact_notes) > 12:
                self._compact_notes = self._compact_notes[-12:]

    def export_for_infer(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt}]
        if self._compact_notes:
            compact_text = "\n".join(f"- {note}" for note in self._compact_notes[-6:])
            messages.append(
                {
                    "role": "system",
                    "content": "Compact memory notes:\n" + compact_text,
                }
            )
        messages.append({"role": "user", "content": self._user_input})
        messages.extend(self._recent_turns)
        return messages

    def snapshot(self) -> dict[str, Any]:
        return {
            "recent_turns": list(self._recent_turns),
            "compact_notes": list(self._compact_notes),
            "max_recent_turns": self._max_recent_turns,
        }


def summarize_local_memory(items: list[Any], limit: int = 6) -> str:
    selected = items[-limit:]
    lines: list[str] = []
    for item in selected:
        if isinstance(item, dict):
            tool = str(item.get("tool", ""))
            obs = item.get("observation", {})
            if isinstance(obs, dict):
                status = "ok" if obs.get("ok") else "non-ok"
                lines.append(f"{tool}:{status}")
            else:
                lines.append(f"{tool}:seen")
        else:
            lines.append(str(item)[:60])
    return json.dumps({"memory_summary": lines}, ensure_ascii=True)
