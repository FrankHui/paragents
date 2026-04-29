from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

from agent_instance import AgentInstance
from hook_runtime import HookRuntime
from id_refs import generate_short_ref
from llm_client import LLMClient
from preflight_intent import infer_preflight_intent_with_llm
from task import Prompt
from tools import ToolRegistry


class Scheduler:
    def __init__(self, llm_client: LLMClient | None, max_in_flight: int = 4) -> None:
        self._llm_client = llm_client
        self._max_in_flight = max_in_flight

        self._pending_queue: asyncio.Queue[str] = asyncio.Queue()
        self._prompts: dict[str, Prompt] = {}
        self._task_tools: dict[str, dict[str, Any]] = {}
        self._task_to_session: dict[str, str] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self._session_root_task: dict[str, str] = {}
        self._session_refs: dict[str, str] = {}
        self._session_preflight_resources: dict[str, set[str]] = {}
        self._session_declared_outputs: dict[str, set[str]] = {}
        self._session_observed_outputs: dict[str, set[str]] = {}
        self._output_locks: dict[str, str] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._task_logs: dict[str, list[str]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}

        self._dispatch_task: asyncio.Task[None] | None = None
        self._scheduled_handles: dict[str, asyncio.Task[None]] = {}
        self._scheduled_meta: dict[str, float] = {}
        self._runs_root = Path.cwd() / ".paragents" / "runs"
        self._debug_enabled = os.getenv("PARAGENTS_TUI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._hook_runtime = HookRuntime.load_from_path(Path.cwd() / ".paragents" / "hooks.json")

    @property
    def prompts(self) -> dict[str, Prompt]:
        return self._prompts

    def set_llm_client(self, llm_client: LLMClient) -> None:
        # 仅影响新启动的任务；已在运行中的任务保持原客户端
        self._llm_client = llm_client

    def get_prompt_session_id(self, prompt_id: str) -> str | None:
        return self._task_to_session.get(prompt_id)

    def list_sessions(self, active_only: bool = False) -> list[str]:
        session_ids = list(self._session_tasks.keys())
        if not active_only:
            return session_ids
        active: list[str] = []
        for session_id in session_ids:
            task_ids = self._session_tasks.get(session_id, set())
            if not task_ids:
                continue
            for task_id in task_ids:
                prompt = self._prompts.get(task_id)
                if prompt is not None and prompt.status in {"pending", "running", "paused"}:
                    active.append(session_id)
                    break
        return active

    def get_session_task_ids(self, session_id: str) -> list[str]:
        return sorted(self._session_tasks.get(session_id, set()))

    def get_session_ref(self, session_id: str) -> str:
        return self._session_refs.get(session_id, session_id[:6])

    def get_session_active_task_id(self, session_id: str) -> str | None:
        task_ids = self._session_tasks.get(session_id, set())
        if not task_ids:
            return None
        prioritized = ["running", "paused", "pending", "failed", "completed", "cancelled"]
        for status in prioritized:
            candidates = [
                self._prompts[task_id]
                for task_id in task_ids
                if task_id in self._prompts and self._prompts[task_id].status == status
            ]
            if candidates:
                candidates.sort(key=lambda t: t.updated_at, reverse=True)
                return candidates[0].prompt_id
        return None

    def _resolve_session_id(self, session_id: str | None, task_id: str) -> str:
        if session_id:
            return session_id
        return task_id

    def _assign_session_ref(self, session_id: str) -> str:
        if session_id in self._session_refs:
            return self._session_refs[session_id]
        existing_refs = set(self._session_refs.values())
        session_ref = generate_short_ref(existing_refs, length=6)
        self._session_refs[session_id] = session_ref
        return session_ref

    def _extract_resource_keys(self, text: str) -> set[str]:
        lowered = text.lower()
        keys: set[str] = set()
        for output_path in self._extract_output_paths(text):
            keys.add(f"out:{output_path}")
        for match in set(Path(part).name for part in text.split() if "." in part and "/" not in part):
            if match:
                keys.add(f"file:{match}")
        if "git push" in lowered:
            keys.add("git:push")
        if "git commit" in lowered:
            keys.add("git:commit")
        return keys

    def _extract_output_paths(self, text: str) -> set[str]:
        outputs: set[str] = set()
        tokens = self._safe_split(text)
        for match in re.findall(r"(?:^|\s)(?:>>|>)\s*([^\s|;&]+)", text):
            normalized = self._normalize_output_key(match)
            if normalized:
                outputs.add(normalized)
        for idx, token in enumerate(tokens):
            if token != "tee":
                continue
            cursor = idx + 1
            while cursor < len(tokens) and tokens[cursor].startswith("-"):
                cursor += 1
            if cursor < len(tokens):
                normalized = self._normalize_output_key(tokens[cursor])
                if normalized:
                    outputs.add(normalized)
        write_verbs = {"touch", "mkdir", "cp", "mv"}
        for idx, token in enumerate(tokens):
            if token not in write_verbs or idx + 1 >= len(tokens):
                continue
            if token == "mkdir" and tokens[idx + 1] in {"-p", "-v"} and idx + 2 < len(tokens):
                normalized_mkdir = self._normalize_output_key(tokens[idx + 2])
                if normalized_mkdir:
                    outputs.add(normalized_mkdir)
                continue
            if token in {"cp", "mv"} and idx + 2 < len(tokens):
                cursor = idx + 1
                while cursor < len(tokens) and tokens[cursor].startswith("-"):
                    cursor += 1
                if cursor + 1 < len(tokens):
                    normalized_dst = self._normalize_output_key(tokens[cursor + 1])
                    if normalized_dst:
                        outputs.add(normalized_dst)
                continue
            normalized = self._normalize_output_key(tokens[idx + 1])
            if normalized:
                outputs.add(normalized)
        return outputs

    def _safe_split(self, text: str) -> list[str]:
        try:
            return [tok.strip().lower() for tok in shlex.split(text) if tok.strip()]
        except ValueError:
            return [tok.strip().lower() for tok in text.split() if tok.strip()]

    def _normalize_output_key(self, raw: str) -> str:
        text = raw.strip().strip("'\"").rstrip(",;")
        if not text:
            return ""
        output = Path(text).as_posix()
        if output.startswith("./"):
            output = output[2:]
        return output

    async def _build_preflight_resource_keys(self, text: str) -> tuple[set[str], dict[str, object]]:
        intent = await infer_preflight_intent_with_llm(text, self._llm_client)
        keys = self._extract_resource_keys(text)
        keys.update(intent.to_resource_keys())
        return keys, intent.to_state()

    def _output_conflict_keys(self, keys: set[str]) -> set[str]:
        return {key for key in keys if key.startswith("out:")}

    def _preflight_conflicts(self, session_id: str, resource_keys: set[str]) -> tuple[list[str], dict[str, list[str]]]:
        current_output_keys = self._output_conflict_keys(resource_keys)
        if not current_output_keys:
            return [], {}
        conflicts: list[str] = []
        details: dict[str, list[str]] = {}
        for other_session, other_keys in self._session_preflight_resources.items():
            if other_session == session_id:
                continue
            other_output_keys = self._output_conflict_keys(other_keys)
            overlap = sorted(current_output_keys.intersection(other_output_keys))
            if overlap:
                conflicts.append(other_session)
                details[other_session] = overlap
        return conflicts, details

    async def schedule_task_in(self, delay_s: float, user_input: str, tools: dict[str, Any]) -> str:
        scheduled_id = str(uuid.uuid4())
        run_at = time.time() + max(0.0, delay_s)
        self._scheduled_meta[scheduled_id] = run_at

        async def _delayed_submit() -> None:
            await asyncio.sleep(max(0.0, delay_s))
            if scheduled_id not in self._scheduled_meta:
                return
            await self.submit(user_input, tools)
            self._scheduled_meta.pop(scheduled_id, None)
            self._scheduled_handles.pop(scheduled_id, None)

        self._scheduled_handles[scheduled_id] = asyncio.create_task(_delayed_submit())
        return scheduled_id

    def list_scheduled_tasks(self) -> dict[str, float]:
        return dict(self._scheduled_meta)

    async def cancel_scheduled_task(self, scheduled_id: str) -> bool:
        handle = self._scheduled_handles.pop(scheduled_id, None)
        if handle is None:
            return False
        self._scheduled_meta.pop(scheduled_id, None)
        handle.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await handle
        return True

    def get_prompt_logs(self, prompt_id: str, limit: int = 200) -> list[str]:
        logs = self._task_logs.get(prompt_id, [])
        return logs[-limit:]

    def subscribe_task_logs(self, task_id: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe_task_logs(self, task_id: str, queue: asyncio.Queue[str]) -> None:
        queues = self._subscribers.get(task_id, [])
        self._subscribers[task_id] = [q for q in queues if q is not queue]
        if not self._subscribers[task_id]:
            self._subscribers.pop(task_id, None)

    def _publish_log(self, task_id: str, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self._task_logs.setdefault(task_id, []).append(line)
        for queue in self._subscribers.get(task_id, []):
            queue.put_nowait(line)

    async def start(self) -> None:
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def submit(
        self,
        user_input: str,
        tools: dict[str, Any],
        session_id: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        session_id = self._resolve_session_id(session_id, task_id)
        session_ref = self._assign_session_ref(session_id)
        existing_refs = {p.prompt_ref for p in self._prompts.values()}
        prompt_ref = generate_short_ref(existing_refs, length=6)
        run_dir = self._runs_root / prompt_ref
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = Prompt(
            prompt_id=task_id,
            input=user_input,
            prompt_ref=prompt_ref,
            session_id=session_id,
            run_dir=str(run_dir),
            status="pending",
        )
        preflight_keys, preflight_intent = await self._build_preflight_resource_keys(user_input)
        conflict_sessions, conflict_details = self._preflight_conflicts(session_id, preflight_keys)
        declared_outputs = sorted(key[4:] for key in preflight_keys if key.startswith("out:"))
        prompt.local_state = {
            **prompt.local_state,
            "run_dir": prompt.run_dir,
            "session_id": session_id,
            "session_ref": session_ref,
            "preflight_intent": preflight_intent,
            "preflight_resource_keys": sorted(preflight_keys),
            "preflight_conflict_with_sessions": conflict_sessions,
            "preflight_conflict_keys_by_session": conflict_details,
            "preflight_declared_outputs": declared_outputs,
            "preflight_decision": "serialize" if conflict_sessions else "allow",
            "preflight_user_override": False,
            "preflight_decision_required": bool(conflict_sessions),
            "preflight_blocking_notice_emitted": False,
        }
        prompt.local_state.setdefault("initial_input", user_input)
        self._prompts[task_id] = prompt
        self._task_tools[task_id] = tools
        self._task_to_session[task_id] = session_id
        self._session_tasks.setdefault(session_id, set()).add(task_id)
        self._session_root_task.setdefault(session_id, task_id)
        self._session_preflight_resources.setdefault(session_id, set()).update(preflight_keys)
        self._session_declared_outputs.setdefault(session_id, set()).update(declared_outputs)
        self._publish_log(task_id, "submitted")
        await self._pending_queue.put(task_id)
        return task_id

    async def continue_task(self, task_id: str, user_input: str) -> bool:
        prompt = self._prompts.get(task_id)
        if prompt is None:
            return False
        if prompt.status in {"running", "pending", "paused"}:
            return False
        prompt.local_state.setdefault("initial_input", prompt.input)
        prompt.input = user_input
        prompt.result = None
        prompt.error = None
        preflight_keys, preflight_intent = await self._build_preflight_resource_keys(user_input)
        conflict_sessions, conflict_details = self._preflight_conflicts(prompt.session_id, preflight_keys)
        declared_outputs = sorted(key[4:] for key in preflight_keys if key.startswith("out:"))
        prompt.local_state["preflight_intent"] = preflight_intent
        prompt.local_state["preflight_resource_keys"] = sorted(preflight_keys)
        prompt.local_state["preflight_conflict_with_sessions"] = conflict_sessions
        prompt.local_state["preflight_conflict_keys_by_session"] = conflict_details
        prompt.local_state["preflight_declared_outputs"] = declared_outputs
        prompt.local_state["preflight_decision"] = "serialize" if conflict_sessions else "allow"
        prompt.local_state["preflight_decision_required"] = bool(conflict_sessions)
        prompt.local_state["preflight_blocking_notice_emitted"] = False
        self._session_preflight_resources.setdefault(prompt.session_id, set()).update(preflight_keys)
        self._session_declared_outputs.setdefault(prompt.session_id, set()).update(declared_outputs)
        prompt.status = "pending"
        prompt.touch()
        if self._debug_enabled:
            self._publish_log(task_id, f"continued: {user_input}")
        await self._pending_queue.put(task_id)
        return True

    async def cancel(self, task_id: str) -> None:
        prompt = self._prompts.get(task_id)
        if prompt is None:
            return
        prompt.status = "cancelled"
        prompt.touch()
        self._publish_log(task_id, "cancel requested")
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()

    async def pause(self, task_id: str) -> None:
        prompt = self._prompts.get(task_id)
        if prompt is None or prompt.status != "running":
            return
        prompt.status = "paused"
        prompt.touch()
        self._publish_log(task_id, "paused")
        event = self._pause_events.get(task_id)
        if event is not None:
            event.set()

    async def resume(self, task_id: str) -> None:
        prompt = self._prompts.get(task_id)
        if prompt is None or prompt.status != "paused":
            return
        # 如果任务还在运行协程中（普通 pause），直接清 pause_event；
        # 如果已退出协程（例如等待审批），则重新入队。
        if task_id in self._running:
            prompt.status = "running"
            prompt.touch()
            self._publish_log(task_id, "resumed")
            event = self._pause_events.get(task_id)
            if event is not None:
                event.clear()
            return

        prompt.status = "pending"
        prompt.touch()
        self._publish_log(task_id, "approval granted, re-queued")
        await self._pending_queue.put(task_id)

    async def retry(self, task_id: str) -> None:
        prompt = self._prompts.get(task_id)
        if prompt is None:
            return
        if prompt.status not in ("failed", "cancelled"):
            return
        if prompt.retries >= prompt.max_retries:
            return

        prompt.retries += 1
        prompt.status = "pending"
        prompt.error = None
        prompt.result = None
        prompt.touch()
        self._publish_log(task_id, f"retry queued ({prompt.retries}/{prompt.max_retries})")
        await self._pending_queue.put(task_id)

    async def _dispatch_loop(self) -> None:
        while True:
            if len(self._running) >= self._max_in_flight:
                await asyncio.sleep(0.05)
                continue

            task_id = await self._pending_queue.get()
            prompt = self._prompts.get(task_id)
            if prompt is None:
                continue

            if prompt.status in ("completed", "cancelled", "running"):
                continue

            session_id = self._task_to_session.get(task_id)
            blockers = set(prompt.local_state.get("preflight_conflict_with_sessions", []))
            declared_outputs = list(prompt.local_state.get("preflight_declared_outputs", []))
            allow_override = bool(prompt.local_state.get("preflight_user_override", False))
            needs_user_decision = bool(prompt.local_state.get("preflight_decision_required", False))
            if needs_user_decision and not allow_override:
                if not bool(prompt.local_state.get("preflight_blocking_notice_emitted", False)):
                    session_refs = ", ".join(sorted(blockers)) if blockers else "(unknown)"
                    self._publish_log(
                        task_id,
                        f"conflict decision pending: waiting on serialize conflicts: {session_refs}; use /override or /cancel",
                    )
                    prompt.local_state["preflight_blocking_notice_emitted"] = True
                await self._pending_queue.put(task_id)
                await asyncio.sleep(0.05)
                continue
            if session_id and blockers and not allow_override:
                blocking = False
                current_keys = self._output_conflict_keys(set(prompt.local_state.get("preflight_resource_keys", [])))
                for other_task_id, other_task in self._prompts.items():
                    other_session_id = self._task_to_session.get(other_task_id)
                    other_keys = self._output_conflict_keys(set(other_task.local_state.get("preflight_resource_keys", [])))
                    if (
                        other_session_id in blockers
                        and other_task.status in {"running", "paused"}
                        and bool(current_keys.intersection(other_keys))
                    ):
                        blocking = True
                        break
                if blocking:
                    await self._pending_queue.put(task_id)
                    await asyncio.sleep(0.05)
                    continue
            acquired_outputs: list[str] = []
            if session_id and declared_outputs and not allow_override:
                locked_by_other = False
                for output_key in declared_outputs:
                    holder = self._output_locks.get(output_key)
                    if holder is not None and holder != session_id:
                        locked_by_other = True
                        break
                if locked_by_other:
                    await self._pending_queue.put(task_id)
                    await asyncio.sleep(0.05)
                    continue
                for output_key in declared_outputs:
                    if output_key not in self._output_locks:
                        self._output_locks[output_key] = session_id
                        acquired_outputs.append(output_key)

            prompt.status = "running"
            prompt.touch()
            self._publish_log(task_id, "started")

            cancel_event = asyncio.Event()
            pause_event = asyncio.Event()
            self._cancel_events[task_id] = cancel_event
            self._pause_events[task_id] = pause_event

            raw_tools = self._task_tools.get(task_id, {})
            run_dir = prompt.run_dir
            session_id = prompt.session_id
            bound_tools: dict[str, Any] = {}
            for name, tool in raw_tools.items():
                async def _bound(
                    args: dict[str, Any],
                    tool=tool,
                    _prompt_id=task_id,
                    _run_dir=run_dir,
                    _session_id=session_id,
                ) -> dict[str, Any]:
                    merged = dict(args)
                    merged.setdefault("_prompt_id", _prompt_id)
                    if _session_id:
                        merged.setdefault("_session_id", _session_id)
                    if _run_dir:
                        merged.setdefault("_prompt_run_dir", _run_dir)
                    return await tool(merged)

                bound_tools[name] = _bound
            tools = ToolRegistry(bound_tools)
            if self._llm_client is None:
                prompt.status = "failed"
                prompt.error = "llm client is unavailable"
                self._publish_log(task_id, "failed: llm client is unavailable")
                for output_key in acquired_outputs:
                    holder = self._output_locks.get(output_key)
                    if holder == session_id:
                        self._output_locks.pop(output_key, None)
                self._cancel_events.pop(task_id, None)
                self._pause_events.pop(task_id, None)
                continue

            agent = AgentInstance(
                task=prompt,
                llm_client=self._llm_client,
                tools=tools,
                event_callback=lambda msg, tid=task_id: self._publish_log(tid, msg),
                hook_runtime=self._hook_runtime,
            )
            self._running[task_id] = asyncio.create_task(self._run_agent(task_id, agent))

    async def _run_agent(self, task_id: str, agent: AgentInstance) -> None:
        prompt = self._prompts[task_id]
        cancel_event = self._cancel_events[task_id]
        pause_event = self._pause_events[task_id]
        session_id = self._task_to_session.get(task_id)
        try:
            await agent.run(cancel_event=cancel_event, pause_event=pause_event)
            declared_outputs = list(prompt.local_state.get("preflight_declared_outputs", []))
            if session_id and declared_outputs:
                self._session_observed_outputs.setdefault(session_id, set()).update(declared_outputs)
            if prompt.status == "completed":
                result_text = "" if prompt.result is None else str(prompt.result)
                if result_text:
                    preview = result_text if len(result_text) <= 4000 else f"{result_text[:4000]}...<truncated>"
                    self._publish_log(task_id, f"result: {preview}")
                self._publish_log(task_id, "finished successfully")
            elif prompt.status == "cancelled":
                self._publish_log(task_id, "finished with cancellation")
        except Exception as exc:  # noqa: BLE001
            prompt.status = "failed"
            prompt.error = str(exc)
            prompt.touch()
            self._publish_log(task_id, f"failed: {exc}")
        finally:
            declared_outputs = list(prompt.local_state.get("preflight_declared_outputs", []))
            if session_id and declared_outputs:
                for output_key in declared_outputs:
                    holder = self._output_locks.get(output_key)
                    if holder == session_id:
                        self._output_locks.pop(output_key, None)
            self._running.pop(task_id, None)
