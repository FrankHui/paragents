from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from llm_client import LLMClient
from memory import LayeredContext, LocalMemory, summarize_local_memory
from task import Prompt
from tools import ToolRegistry


class AgentInstance:
    def __init__(
        self,
        task: Prompt,
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
        self._system_prompt = (
            "You are an autonomous agent.\n"
            "You must respond with strict JSON only.\n"
            "Allowed output schema:\n"
            '1) {"type":"final","content":"..."}\n'
            '2) {"type":"tool","tool_name":"...","args":{...}}\n'
            f"Allowed tools: {tool_names}\n"
            "If you can answer directly, use final.\n"
            "Do not output markdown fences."
        )
        self.layered_context = LayeredContext(self._system_prompt, task.input, max_recent_turns=8)

    def _emit(self, message: str) -> None:
        if self._event_callback is not None:
            self._event_callback(message)

    def _emit_stream_preview(self, tool_name: str, observation: Any) -> None:
        if tool_name not in {"run_command", "run_python"}:
            return
        if not isinstance(observation, dict):
            return

        stdout_text = str(observation.get("stdout", "")).strip()
        stderr_text = str(observation.get("stderr", "")).strip()
        if not stdout_text and not stderr_text:
            return

        def _preview(text: str) -> str:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if not lines:
                return ""
            head = lines[:6]
            clipped = " | ".join(head)
            if len(lines) > 6:
                clipped += f" ...(+{len(lines) - 6} lines)"
            if len(clipped) > 400:
                clipped = clipped[:400] + "...<truncated>"
            return clipped

        out_preview = _preview(stdout_text)
        err_preview = _preview(stderr_text)
        if out_preview:
            self._emit(f"{tool_name} stdout: {out_preview}")
        if err_preview:
            self._emit(f"{tool_name} stderr: {err_preview}")

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
            llm_input = self.layered_context.export_for_infer()
            llm_output = await self.llm_client.infer(llm_input)
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
                memory_items = await self.memory.snapshot()
                self.task.local_state["memory_summary"] = summarize_local_memory(memory_items)
                self._emit_stream_preview(tool_name, observation)
                if isinstance(observation, dict) and observation.get("needs_approval"):
                    request_id = str(observation.get("request_id", ""))
                    approval_type = str(observation.get("approval_type", "")).strip()
                    approval_payload = observation.get("approval_payload", {})
                    approval_detail = ""
                    if approval_type:
                        approval_detail = f"type={approval_type} payload={approval_payload}"
                    elif observation.get("error"):
                        approval_detail = str(observation.get("error", ""))
                    self.task.status = "paused"
                    self.task.local_state["pending_approval_request_id"] = request_id
                    self.task.local_state["pending_approval_tool_name"] = tool_name
                    self.task.touch()
                    if approval_detail:
                        self._emit(f"waiting for approval request_id={request_id} detail={approval_detail}")
                    else:
                        self._emit(f"waiting for approval request_id={request_id}")
                    return
                # 使用纯文本 assistant/user 轮次，避免 OpenAI tool role 的 tool_call_id 协议要求。
                self.layered_context.append_turn("assistant", str(llm_output))
                self.layered_context.append_turn(
                    "user",
                    f"Tool observation for {tool_name}: {observation}. Continue and return JSON only.",
                )
                self.task.local_state["context_snapshot"] = self.layered_context.snapshot()
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
