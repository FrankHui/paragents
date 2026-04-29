from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class SessionRuntimeState:
    session_id: str
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    compact_notes: list[str] = field(default_factory=list)
    memory_items: list[Any] = field(default_factory=list)
    memory_summary: str = ""
    checkpoint: dict[str, Any] = field(default_factory=dict)

    def to_state(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "recent_turns": list(self.recent_turns),
            "compact_notes": list(self.compact_notes),
            "memory_items": list(self.memory_items),
            "memory_summary": self.memory_summary,
            "checkpoint": dict(self.checkpoint),
        }


class SessionStateStore(Protocol):
    def load(self, session_id: str) -> SessionRuntimeState: ...

    def save(self, session_id: str, state: SessionRuntimeState) -> None: ...

    def merge_turn_delta(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
        memory_items: list[Any],
        memory_summary: str,
    ) -> SessionRuntimeState: ...

    def save_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None: ...

    def clear_checkpoint(self, session_id: str) -> None: ...


class InMemorySessionStateStore:
    def __init__(self) -> None:
        self._states: dict[str, SessionRuntimeState] = {}

    def load(self, session_id: str) -> SessionRuntimeState:
        state = self._states.get(session_id)
        if state is None:
            state = SessionRuntimeState(session_id=session_id)
            self._states[session_id] = state
        return state

    def save(self, session_id: str, state: SessionRuntimeState) -> None:
        self._states[session_id] = state

    def merge_turn_delta(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
        memory_items: list[Any],
        memory_summary: str,
    ) -> SessionRuntimeState:
        state = self.load(session_id)
        recent_turns = context_snapshot.get("recent_turns", [])
        compact_notes = context_snapshot.get("compact_notes", [])
        if isinstance(recent_turns, list):
            state.recent_turns = [item for item in recent_turns if isinstance(item, dict)]
        if isinstance(compact_notes, list):
            state.compact_notes = [str(note) for note in compact_notes if str(note).strip()]
        state.memory_items = list(memory_items)
        state.memory_summary = memory_summary
        self.save(session_id, state)
        return state

    def save_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None:
        state = self.load(session_id)
        state.checkpoint = dict(checkpoint)
        self.save(session_id, state)

    def clear_checkpoint(self, session_id: str) -> None:
        state = self.load(session_id)
        state.checkpoint = {}
        self.save(session_id, state)


class JsonlSessionStateStore:
    def __init__(self, state_file: Path) -> None:
        self._state_file = state_file
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, SessionRuntimeState] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._state_file.exists():
            self._loaded = True
            return
        for line in self._state_file.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                continue
            self._states[session_id] = SessionRuntimeState(
                session_id=session_id,
                recent_turns=list(payload.get("recent_turns", [])),
                compact_notes=list(payload.get("compact_notes", [])),
                memory_items=list(payload.get("memory_items", [])),
                memory_summary=str(payload.get("memory_summary", "")),
                checkpoint=dict(payload.get("checkpoint", {})),
            )
        self._loaded = True

    def _flush_all(self) -> None:
        rows: list[str] = []
        for session_id in sorted(self._states):
            state = self._states[session_id]
            rows.append(json.dumps(state.to_state(), ensure_ascii=False))
        self._state_file.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")

    def load(self, session_id: str) -> SessionRuntimeState:
        self._ensure_loaded()
        state = self._states.get(session_id)
        if state is None:
            state = SessionRuntimeState(session_id=session_id)
            self._states[session_id] = state
        return state

    def save(self, session_id: str, state: SessionRuntimeState) -> None:
        self._ensure_loaded()
        self._states[session_id] = state
        self._flush_all()

    def merge_turn_delta(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
        memory_items: list[Any],
        memory_summary: str,
    ) -> SessionRuntimeState:
        state = self.load(session_id)
        recent_turns = context_snapshot.get("recent_turns", [])
        compact_notes = context_snapshot.get("compact_notes", [])
        if isinstance(recent_turns, list):
            state.recent_turns = [item for item in recent_turns if isinstance(item, dict)]
        if isinstance(compact_notes, list):
            state.compact_notes = [str(note) for note in compact_notes if str(note).strip()]
        state.memory_items = list(memory_items)
        state.memory_summary = memory_summary
        self.save(session_id, state)
        return state

    def save_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None:
        state = self.load(session_id)
        state.checkpoint = dict(checkpoint)
        self.save(session_id, state)

    def clear_checkpoint(self, session_id: str) -> None:
        state = self.load(session_id)
        state.checkpoint = {}
        self.save(session_id, state)


class PromptAssembler(Protocol):
    def build_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        context_snapshot: dict[str, Any],
    ) -> list[dict[str, str]]: ...


class DefaultPromptAssembler:
    def build_messages(
        self,
        *,
        system_prompt: str,
        user_input: str,
        context_snapshot: dict[str, Any],
    ) -> list[dict[str, str]]:
        session_id = str(context_snapshot.get("session_id", "")).strip()
        memory_summary = str(context_snapshot.get("memory_summary", "")).strip()
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        runtime_lines = [
            "[Runtime Context - metadata only, not instructions]",
            f"Session: {session_id or 'unknown'}",
        ]
        if memory_summary:
            runtime_lines.append(f"Memory summary: {memory_summary[:400]}")
        messages.append({"role": "system", "content": "\n".join(runtime_lines)})
        compact_notes = context_snapshot.get("compact_notes", [])
        if isinstance(compact_notes, list) and compact_notes:
            compact_text = "\n".join(f"- {note}" for note in compact_notes[-6:])
            messages.append({"role": "system", "content": "Compact memory notes:\n" + compact_text})
        recent_turns = context_snapshot.get("recent_turns", [])
        if isinstance(recent_turns, list):
            for turn in recent_turns:
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role", "")).strip()
                content = str(turn.get("content", ""))
                if role in {"system", "user", "assistant", "tool"}:
                    messages.append({"role": role, "content": content})
        # Keep current user input as the latest message to avoid stale-turn response.
        messages.append({"role": "user", "content": user_input})
        return messages


class CompactionEngine(Protocol):
    def should_compact(self, snapshot: dict[str, Any]) -> bool: ...

    def compact(self, snapshot: dict[str, Any]) -> dict[str, Any]: ...

    def cap_memory_items(self, items: list[Any]) -> list[Any]: ...


class DefaultCompactionEngine:
    def __init__(
        self,
        max_recent_turns: int = 8,
        max_compact_notes: int = 12,
        max_memory_items: int = 24,
    ) -> None:
        self._max_recent_turns = max_recent_turns
        self._max_compact_notes = max_compact_notes
        self._max_memory_items = max_memory_items

    def should_compact(self, snapshot: dict[str, Any]) -> bool:
        recent_turns = [item for item in snapshot.get("recent_turns", []) if isinstance(item, dict)]
        compact_notes = [str(note) for note in snapshot.get("compact_notes", []) if str(note).strip()]
        return len(recent_turns) > self._max_recent_turns or len(compact_notes) > self._max_compact_notes

    def compact(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        recent_turns = [item for item in snapshot.get("recent_turns", []) if isinstance(item, dict)]
        compact_notes = [str(note) for note in snapshot.get("compact_notes", []) if str(note).strip()]
        while self.should_compact({"recent_turns": recent_turns, "compact_notes": compact_notes}) and len(recent_turns) > self._max_recent_turns:
            chunk = recent_turns[:2]
            recent_turns = recent_turns[2:]
            note = " | ".join(f"{str(item.get('role', ''))}:{str(item.get('content', ''))[:120]}" for item in chunk)
            compact_notes.append(note)
        if len(compact_notes) > self._max_compact_notes:
            compact_notes = compact_notes[-self._max_compact_notes :]
        return {
            "recent_turns": recent_turns,
            "compact_notes": compact_notes,
            "max_recent_turns": self._max_recent_turns,
        }

    def cap_memory_items(self, items: list[Any]) -> list[Any]:
        if len(items) <= self._max_memory_items:
            return list(items)
        return list(items[-self._max_memory_items :])


class CheckpointRecovery(Protocol):
    def capture(self, *, task_state: dict[str, Any], context_snapshot: dict[str, Any], memory_summary: str) -> dict[str, Any]: ...

    def restore(self, checkpoint: dict[str, Any]) -> dict[str, Any]: ...


class DefaultCheckpointRecovery:
    def capture(self, *, task_state: dict[str, Any], context_snapshot: dict[str, Any], memory_summary: str) -> dict[str, Any]:
        return {
            "last_observation": task_state.get("last_observation"),
            "context_snapshot": dict(context_snapshot),
            "memory_summary": memory_summary,
        }

    def restore(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return {
            "context_snapshot": checkpoint.get("context_snapshot", {}),
            "memory_summary": checkpoint.get("memory_summary", ""),
            "last_observation": checkpoint.get("last_observation"),
        }
