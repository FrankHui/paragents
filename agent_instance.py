from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from llm_client import LLMClient
from memory import LocalMemory
from task import Task
from tools import ToolRegistry


class AgentInstance:
    def __init__(
        self,
        task: Task,
        llm_client: LLMClient,
        tools: ToolRegistry,
        event_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.task = task
        self.llm_client = llm_client
        self.tools = tools
        self._event_callback = event_callback
        self.memory = LocalMemory()
        tool_names = self.tools.list_tool_names()
        self.context: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are an autonomous agent.\n"
                    "You must respond with strict JSON only.\n"
                    "Allowed output schema:\n"
                    '1) {"type":"final","content":"..."}\n'
                    '2) {"type":"tool","tool_name":"...","args":{...}}\n'
                    f"Allowed tools: {tool_names}\n"
                    "If you can answer directly, use final.\n"
                    "Do not output markdown fences."
                ),
            },
            {"role": "user", "content": task.input},
        ]

    def _emit(self, message: str) -> None:
        if self._event_callback is not None:
            self._event_callback(message)

    async def run(self, cancel_event: asyncio.Event, pause_event: asyncio.Event | None = None) -> None:
        max_steps = 10
        self._emit("agent started")

        for step in range(max_steps):
            if cancel_event.is_set():
                self.task.status = "cancelled"
                self.task.touch()
                self._emit("task cancelled")
                return

            if pause_event is not None:
                while pause_event.is_set() and not cancel_event.is_set():
                    await asyncio.sleep(0.1)

            self._emit(f"step={step + 1}: llm infer")
            llm_output = await self.llm_client.infer(self.context)
            output_type = llm_output.get("type")

            if output_type == "final":
                self.task.result = llm_output.get("content")
                self.task.status = "completed"
                self.task.touch()
                self._emit("task completed")
                return

            if output_type == "tool":
                tool_name = str(llm_output["tool_name"])
                raw_args = llm_output.get("args", {})
                args = raw_args if isinstance(raw_args, dict) else {}
                if tool_name == "read_file" and not str(args.get("path", "")).strip():
                    inferred_path = self._infer_path_from_task_input()
                    if inferred_path:
                        args["path"] = inferred_path
                        self._emit(f"step={step + 1}: inferred read_file path -> {inferred_path}")
                self._emit(f"step={step + 1}: tool call -> {tool_name}")
                observation = await self.tools.call(tool_name, args)
                await self.memory.append({"tool": tool_name, "args": args, "observation": observation})
                self.task.local_state["last_observation"] = observation
                if isinstance(observation, dict) and observation.get("needs_approval"):
                    request_id = str(observation.get("request_id", ""))
                    self.task.status = "paused"
                    self.task.local_state["pending_approval_request_id"] = request_id
                    self.task.local_state["pending_approval_tool_name"] = tool_name
                    self.task.touch()
                    self._emit(f"waiting for approval request_id={request_id}")
                    return
                # 使用纯文本 assistant/user 轮次，避免 OpenAI tool role 的 tool_call_id 协议要求。
                self.context.append({"role": "assistant", "content": str(llm_output)})
                self.context.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool observation for {tool_name}: {observation}. "
                            "Continue and return JSON only."
                        ),
                    }
                )
                self.task.touch()
                self._emit(f"step={step + 1}: tool observation received")
                continue

            self.task.status = "failed"
            self.task.error = f"Unknown LLM output type: {output_type}"
            self.task.touch()
            self._emit(f"task failed: unknown output type {output_type}")
            return

        self.task.status = "failed"
        self.task.error = "Max steps exceeded"
        self.task.touch()
        self._emit("task failed: max steps exceeded")

    def _infer_path_from_task_input(self) -> str:
        text = self.task.input.strip()
        lowered = text.lower()
        if lowered.startswith("read "):
            candidate = text[5:].strip()
        elif lowered.startswith("cat "):
            candidate = text[4:].strip()
        else:
            return ""

        if (candidate.startswith('"') and candidate.endswith('"')) or (
            candidate.startswith("'") and candidate.endswith("'")
        ):
            candidate = candidate[1:-1].strip()
        return candidate
