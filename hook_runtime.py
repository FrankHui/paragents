from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class HookDecision:
    action: str = "allow"  # allow | warn | block
    message: str = ""


class HookRuntime:
    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self._rules = rules or {}

    @classmethod
    def load_from_path(cls, path: str | Path) -> "HookRuntime":
        p = Path(path)
        if not p.exists():
            return cls({})
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        return cls(raw if isinstance(raw, dict) else {})

    def on_user_prompt_submit(self, payload: dict[str, Any]) -> HookDecision:
        text = str(payload.get("input", "")).lower()
        patterns = self._rules.get("block_prompt_patterns", [])
        for pattern in patterns:
            token = str(pattern).strip().lower()
            if token and token in text:
                return HookDecision(action="block", message=f"blocked by prompt rule: {token}")
        return HookDecision()

    def on_pre_tool_use(self, payload: dict[str, Any]) -> HookDecision:
        tool_name = str(payload.get("tool_name", "")).strip()
        blocked = {str(x).strip() for x in self._rules.get("block_tools", [])}
        if tool_name and tool_name in blocked:
            return HookDecision(action="block", message=f"blocked tool: {tool_name}")
        return HookDecision()

    def on_post_tool_use(self, payload: dict[str, Any]) -> HookDecision:
        tool_name = str(payload.get("tool_name", "")).strip()
        blocked = {str(x).strip() for x in self._rules.get("block_post_tools", [])}
        if tool_name and tool_name in blocked:
            return HookDecision(action="block", message=f"blocked post tool: {tool_name}")
        observation = payload.get("observation")
        if isinstance(observation, dict) and observation.get("ok") is False:
            warn_on_error = bool(self._rules.get("warn_on_tool_error", False))
            if warn_on_error:
                return HookDecision(action="warn", message="tool returned non-ok observation")
        return HookDecision()

    def on_stop(self, payload: dict[str, Any]) -> HookDecision:
        reason = str(payload.get("reason", "")).strip()
        blocked = {str(x).strip() for x in self._rules.get("block_stop_reasons", [])}
        if reason and reason in blocked:
            return HookDecision(action="block", message=f"blocked stop: {reason}")
        return HookDecision()
