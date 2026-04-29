from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any

from hook_runtime import HookRuntime
from llm_client import LLMClient
from memory import LayeredContext, LocalMemory, summarize_local_memory
from session_runtime import CompactionEngine, DefaultCompactionEngine, DefaultPromptAssembler, PromptAssembler
from task import Prompt
from tools import ToolRegistry


class AgentInstance:
    def __init__(
        self,
        task: Prompt,
        llm_client: LLMClient,
        tools: ToolRegistry,
        event_callback: Callable[[str], None] | None = None,
        hook_runtime: HookRuntime | None = None,
        context_seed: dict[str, Any] | None = None,
        memory_seed: list[Any] | None = None,
        prompt_assembler: PromptAssembler | None = None,
        compaction_engine: CompactionEngine | None = None,
        state_update_callback: Callable[[dict[str, Any], list[Any], str], None] | None = None,
    ) -> None:
        self.task = task
        self.llm_client = llm_client
        self.tools = tools
        self._event_callback = event_callback
        self.hook_runtime = hook_runtime
        self._state_update_callback = state_update_callback
        self.memory = LocalMemory(initial_items=memory_seed)
        tool_names = self.tools.list_tool_names()
        self._system_prompt = (
            "You are an autonomous agent.\n"
            "You must respond with strict JSON only.\n"
            "Allowed output schema:\n"
            '1) {"type":"turn_done","content":"..."}\n'
            '2) {"type":"tool","tool_name":"...","args":{...}}\n'
            f"Allowed tools: {tool_names}\n"
            "If you can answer directly, use turn_done.\n"
            "Do not output markdown fences."
        )
        snapshot = context_seed or {}
        self.layered_context = LayeredContext(
            self._system_prompt,
            task.input,
            max_recent_turns=8,
            initial_recent_turns=snapshot.get("recent_turns", []),
            initial_compact_notes=snapshot.get("compact_notes", []),
        )
        self._prompt_assembler: PromptAssembler = prompt_assembler or DefaultPromptAssembler()
        self._compaction_engine: CompactionEngine = compaction_engine or DefaultCompactionEngine()
        self._debug_enabled = os.getenv("PARAGENTS_TUI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._started_once = False

    def bind_turn(
        self,
        *,
        task: Prompt,
        llm_client: LLMClient,
        tools: ToolRegistry,
        event_callback: Callable[[str], None] | None = None,
        state_update_callback: Callable[[dict[str, Any], list[Any], str], None] | None = None,
    ) -> None:
        self.task = task
        self.llm_client = llm_client
        self.tools = tools
        self._event_callback = event_callback
        self._state_update_callback = state_update_callback

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

    def _emit_context_debug(self) -> None:
        if not self._debug_enabled:
            return
        snapshot = self.layered_context.snapshot()
        compact_notes = snapshot.get("compact_notes", [])
        recent_turns = snapshot.get("recent_turns", [])
        self._emit("debug.context.compact_notes=" + json.dumps(compact_notes, ensure_ascii=True))
        self._emit("debug.memory_summary=" + str(self.task.local_state.get("memory_summary", "")))
        self._emit("debug.context.recent_turns=" + json.dumps(recent_turns[-4:], ensure_ascii=True))

    def _apply_stop_hook(self, reason: str) -> None:
        if self.hook_runtime is None:
            return
        decision = self.hook_runtime.on_stop(
            {"prompt_id": self.task.prompt_id, "session_id": self.task.session_id, "status": self.task.status, "reason": reason}
        )
        if decision.action == "warn":
            self._emit(f"hook warning(Stop): {decision.message}")
        if decision.action == "block":
            self.task.status = "paused"
            self.task.local_state["hook_blocked"] = True
            self.task.local_state["hook_stage"] = "Stop"
            self.task.local_state["hook_message"] = decision.message
            self.task.touch()
            self._emit(f"blocked by hook(Stop): {decision.message}")

    async def _sync_runtime_state(self) -> None:
        memory_items = self._compaction_engine.cap_memory_items(await self.memory.snapshot())
        snapshot = self.layered_context.snapshot()
        if self._compaction_engine.should_compact(snapshot):
            snapshot = self._compaction_engine.compact(snapshot)
            self.layered_context.restore(snapshot)
        compacted_snapshot = self.layered_context.snapshot()
        self.task.local_state["memory_summary"] = summarize_local_memory(memory_items)
        self.task.local_state["context_snapshot"] = compacted_snapshot
        if self._state_update_callback is not None:
            self._state_update_callback(
                self.task.local_state["context_snapshot"],
                memory_items,
                self.task.local_state["memory_summary"],
            )
        self._emit_context_debug()

    async def run(self, cancel_event: asyncio.Event, pause_event: asyncio.Event | None = None) -> None:
        max_steps = 10
        if not self._started_once:
            self._emit("agent started")
            self._started_once = True
        else:
            self._emit("agent resumed turn")
        if self.hook_runtime is not None:
            submit_decision = self.hook_runtime.on_user_prompt_submit(
                {"prompt_id": self.task.prompt_id, "session_id": self.task.session_id, "input": self.task.input}
            )
            if submit_decision.action == "block":
                self.task.status = "paused"
                self.task.local_state["hook_blocked"] = True
                self.task.local_state["hook_stage"] = "UserPromptSubmit"
                self.task.local_state["hook_message"] = submit_decision.message
                self.task.touch()
                self._emit(f"blocked by hook(UserPromptSubmit): {submit_decision.message}")
                return

        for step in range(max_steps):
            if cancel_event.is_set():
                self.task.status = "cancelled"
                self.task.touch()
                self._apply_stop_hook("cancelled")
                self._emit("task cancelled")
                return

            if pause_event is not None:
                while pause_event.is_set() and not cancel_event.is_set():
                    await asyncio.sleep(0.1)

            self._emit(f"step={step + 1}: llm infer")
            await self._sync_runtime_state()
            llm_input = self._prompt_assembler.build_messages(
                system_prompt=self._system_prompt,
                user_input=self.task.input,
                context_snapshot=self.layered_context.snapshot(),
            )
            llm_output = await self.llm_client.infer(llm_input)
            output_type = llm_output.get("type")

            if output_type in {"turn_done", "final"}:
                if output_type == "final":
                    self._emit("deprecated output type: final; treat as turn_done")
                self.task.result = llm_output.get("content")
                # Treat turn_done as current turn completion, not session termination context.
                self.layered_context.append_turn("user", self.task.input)
                self.layered_context.append_turn("assistant", str(self.task.result or ""))
                await self._sync_runtime_state()
                self.task.status = "completed"
                self.task.touch()
                self._apply_stop_hook("completed")
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
                if self.hook_runtime is not None:
                    pre_decision = self.hook_runtime.on_pre_tool_use(
                        {"tool_name": tool_name, "args": args, "prompt_id": self.task.prompt_id}
                    )
                    if pre_decision.action == "block":
                        self.task.status = "paused"
                        self.task.local_state["hook_blocked"] = True
                        self.task.local_state["hook_stage"] = "PreToolUse"
                        self.task.local_state["hook_message"] = pre_decision.message
                        self.task.touch()
                        self._emit(f"blocked by hook(PreToolUse): {pre_decision.message}")
                        return
                observation = await self.tools.call(tool_name, args)
                await self.memory.append({"tool": tool_name, "args": args, "observation": observation})
                self.task.local_state["last_observation"] = observation
                self.task.local_state["memory_summary"] = summarize_local_memory(await self.memory.snapshot())
                self._emit_stream_preview(tool_name, observation)
                if self.hook_runtime is not None:
                    post_decision = self.hook_runtime.on_post_tool_use(
                        {"tool_name": tool_name, "args": args, "observation": observation}
                    )
                    if post_decision.action == "block":
                        self.task.status = "paused"
                        self.task.local_state["hook_blocked"] = True
                        self.task.local_state["hook_stage"] = "PostToolUse"
                        self.task.local_state["hook_message"] = post_decision.message
                        self.task.touch()
                        self._emit(f"blocked by hook(PostToolUse): {post_decision.message}")
                        return
                    if post_decision.action == "warn":
                        self._emit(f"hook warning(PostToolUse): {post_decision.message}")
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
                await self._sync_runtime_state()
                self.task.touch()
                self._emit(f"step={step + 1}: tool observation received")
                continue

            self.task.status = "failed"
            self.task.error = f"Unknown LLM output type: {output_type}"
            self.task.touch()
            self._apply_stop_hook("unknown_output_type")
            self._emit(f"task failed: unknown output type {output_type}")
            return

        self.task.status = "failed"
        self.task.error = "Max steps exceeded"
        self.task.touch()
        self._apply_stop_hook("max_steps_exceeded")
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
