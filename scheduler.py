from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from agent_instance import AgentInstance
from id_refs import generate_short_ref
from llm_client import LLMClient
from task import Task
from tools import ToolRegistry


class Scheduler:
    def __init__(self, llm_client: LLMClient | None, max_in_flight: int = 4) -> None:
        self._llm_client = llm_client
        self._max_in_flight = max_in_flight

        self._pending_queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: dict[str, Task] = {}
        self._task_tools: dict[str, dict[str, Any]] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._task_logs: dict[str, list[str]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[str]]] = {}

        self._dispatch_task: asyncio.Task[None] | None = None
        self._scheduled_handles: dict[str, asyncio.Task[None]] = {}
        self._scheduled_meta: dict[str, float] = {}

    @property
    def tasks(self) -> dict[str, Task]:
        return self._tasks

    def set_llm_client(self, llm_client: LLMClient) -> None:
        # 仅影响新启动的任务；已在运行中的任务保持原客户端
        self._llm_client = llm_client

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

    def get_task_logs(self, task_id: str, limit: int = 200) -> list[str]:
        logs = self._task_logs.get(task_id, [])
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

    async def submit(self, user_input: str, tools: dict[str, Any]) -> str:
        task_id = str(uuid.uuid4())
        existing_refs = {t.task_ref for t in self._tasks.values()}
        task_ref = generate_short_ref(existing_refs, length=6)
        task = Task(task_id=task_id, input=user_input, task_ref=task_ref, status="pending")
        self._tasks[task_id] = task
        self._task_tools[task_id] = tools
        self._publish_log(task_id, "submitted")
        await self._pending_queue.put(task_id)
        return task_id

    async def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "cancelled"
        task.touch()
        self._publish_log(task_id, "cancel requested")
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()

    async def pause(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.status != "running":
            return
        task.status = "paused"
        task.touch()
        self._publish_log(task_id, "paused")
        event = self._pause_events.get(task_id)
        if event is not None:
            event.set()

    async def resume(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.status != "paused":
            return
        # 如果任务还在运行协程中（普通 pause），直接清 pause_event；
        # 如果已退出协程（例如等待审批），则重新入队。
        if task_id in self._running:
            task.status = "running"
            task.touch()
            self._publish_log(task_id, "resumed")
            event = self._pause_events.get(task_id)
            if event is not None:
                event.clear()
            return

        task.status = "pending"
        task.touch()
        self._publish_log(task_id, "approval granted, re-queued")
        await self._pending_queue.put(task_id)

    async def retry(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        if task.status not in ("failed", "cancelled"):
            return
        if task.retries >= task.max_retries:
            return

        task.retries += 1
        task.status = "pending"
        task.error = None
        task.result = None
        task.touch()
        self._publish_log(task_id, f"retry queued ({task.retries}/{task.max_retries})")
        await self._pending_queue.put(task_id)

    async def _dispatch_loop(self) -> None:
        while True:
            if len(self._running) >= self._max_in_flight:
                await asyncio.sleep(0.05)
                continue

            task_id = await self._pending_queue.get()
            task = self._tasks.get(task_id)
            if task is None:
                continue

            if task.status in ("completed", "cancelled", "running"):
                continue

            task.status = "running"
            task.touch()
            self._publish_log(task_id, "started")

            cancel_event = asyncio.Event()
            pause_event = asyncio.Event()
            self._cancel_events[task_id] = cancel_event
            self._pause_events[task_id] = pause_event

            tools = ToolRegistry(self._task_tools.get(task_id, {}))
            if self._llm_client is None:
                task.status = "failed"
                task.error = "llm client is unavailable"
                self._publish_log(task_id, "failed: llm client is unavailable")
                continue

            agent = AgentInstance(
                task=task,
                llm_client=self._llm_client,
                tools=tools,
                event_callback=lambda msg, tid=task_id: self._publish_log(tid, msg),
            )
            self._running[task_id] = asyncio.create_task(self._run_agent(task_id, agent))

    async def _run_agent(self, task_id: str, agent: AgentInstance) -> None:
        task = self._tasks[task_id]
        cancel_event = self._cancel_events[task_id]
        pause_event = self._pause_events[task_id]
        try:
            await agent.run(cancel_event=cancel_event, pause_event=pause_event)
            if task.status == "completed":
                result_text = "" if task.result is None else str(task.result)
                if result_text:
                    preview = result_text if len(result_text) <= 4000 else f"{result_text[:4000]}...<truncated>"
                    self._publish_log(task_id, f"result: {preview}")
                self._publish_log(task_id, "finished successfully")
            elif task.status == "cancelled":
                self._publish_log(task_id, "finished with cancellation")
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = str(exc)
            task.touch()
            self._publish_log(task_id, f"failed: {exc}")
        finally:
            self._running.pop(task_id, None)
