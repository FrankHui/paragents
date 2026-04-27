from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Awaitable, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from scheduler import Scheduler
from tui_state import build_task_views, normalize_tui_command


@dataclass
class TUILayoutParts:
    root: FloatContainer
    log_panel: Window
    submit_panel_1: Window
    submit_panel_2: Window
    submit_panel_3: Window
    submit_panel_4: Window
    command_prefix: Window
    command_bar: Window
    options_popup: Window
    popup_float: Float

    def __str__(self) -> str:
        return "log_panel submit_panels command_bar"


def build_tui_layout(
    show_options_filter: Condition | bool = False,
    submit_count_provider: Callable[[], int] | None = None,
) -> TUILayoutParts:
    get_submit_count = submit_count_provider or (lambda: 0)
    log_panel = Window(
        FormattedTextControl("log_panel"),
        wrap_lines=True,
        width=Dimension(weight=7, min=72),
        style="class:panel.main",
    )
    submit_panel_1 = Window(FormattedTextControl("submit_panel_1"), wrap_lines=True, style="class:panel.main")
    submit_panel_2 = Window(FormattedTextControl("submit_panel_2"), wrap_lines=True, style="class:panel.main")
    submit_panel_3 = Window(FormattedTextControl("submit_panel_3"), wrap_lines=True, style="class:panel.main")
    submit_panel_4 = Window(FormattedTextControl("submit_panel_4"), wrap_lines=True, style="class:panel.main")
    command_prefix = Window(
        FormattedTextControl("You: "),
        width=5,
        dont_extend_width=True,
        height=1,
        style="class:panel.command",
    )
    command_bar = Window(FormattedTextControl("command_bar"), height=1, style="class:panel.command")

    submit_single = ConditionalContainer(
        content=submit_panel_1,
        filter=Condition(lambda: get_submit_count() <= 1),
    )
    submit_two = ConditionalContainer(
        content=HSplit(
            [
                submit_panel_1,
                Window(height=1, char="─", style="class:separator"),
                submit_panel_2,
            ]
        ),
        filter=Condition(lambda: get_submit_count() == 2),
    )
    submit_three = ConditionalContainer(
        content=HSplit(
            [
                submit_panel_1,
                Window(height=1, char="─", style="class:separator"),
                VSplit([submit_panel_2, Window(width=1, char="│", style="class:separator"), submit_panel_3]),
            ]
        ),
        filter=Condition(lambda: get_submit_count() == 3),
    )
    submit_four = ConditionalContainer(
        content=HSplit(
            [
                VSplit([submit_panel_1, Window(width=1, char="│", style="class:separator"), submit_panel_2]),
                Window(height=1, char="─", style="class:separator"),
                VSplit([submit_panel_3, Window(width=1, char="│", style="class:separator"), submit_panel_4]),
            ]
        ),
        filter=Condition(lambda: get_submit_count() >= 4),
    )
    right_col = HSplit(
        [submit_single, submit_two, submit_three, submit_four],
        width=Dimension(weight=3, min=52),
    )
    main_area = VSplit([log_panel, Window(width=1, char="│", style="class:separator"), right_col])
    cmd_row = VSplit([command_prefix, command_bar])
    base_root = HSplit([main_area, Window(height=1, char="─", style="class:separator"), cmd_row], style="class:root")
    options_popup = Window(FormattedTextControl("options_popup"), wrap_lines=False, style="class:panel.popup")
    popup_float = Float(
        content=ConditionalContainer(content=options_popup, filter=show_options_filter),
        left=4,
        bottom=1,
        width=58,
        height=8,
        hide_when_covering_content=False,
    )
    root = FloatContainer(
        content=base_root,
        floats=[popup_float],
    )
    return TUILayoutParts(
        root=root,
        log_panel=log_panel,
        submit_panel_1=submit_panel_1,
        submit_panel_2=submit_panel_2,
        submit_panel_3=submit_panel_3,
        submit_panel_4=submit_panel_4,
        command_prefix=command_prefix,
        command_bar=command_bar,
        options_popup=options_popup,
        popup_float=popup_float,
    )


class ParagentsTUI:
    def __init__(
        self,
        scheduler: Scheduler,
        pending_approvals_provider: Callable[[], int],
        command_handler: Callable[[str], Awaitable[list[str]]],
        watching_task_id_provider: Callable[[], str | None] | None = None,
        submit_slot_ids_provider: Callable[[], list[str]] | None = None,
        ack_pending_task_ids_provider: Callable[[], list[str]] | None = None,
        show_candidate_task_ids_provider: Callable[[], list[str]] | None = None,
        approve_candidate_request_refs_provider: Callable[[], list[str]] | None = None,
        deny_candidate_request_refs_provider: Callable[[], list[str]] | None = None,
        request_ref_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.pending_approvals_provider = pending_approvals_provider
        self.command_handler = command_handler
        self.watching_task_id_provider = watching_task_id_provider
        self.submit_slot_ids_provider = submit_slot_ids_provider
        self.ack_pending_task_ids_provider = ack_pending_task_ids_provider
        self.show_candidate_task_ids_provider = show_candidate_task_ids_provider
        self.approve_candidate_request_refs_provider = approve_candidate_request_refs_provider
        self.deny_candidate_request_refs_provider = deny_candidate_request_refs_provider
        self.request_ref_provider = request_ref_provider
        self.command_history = InMemoryHistory()
        self.command_completer = WordCompleter(
            [
                "/run",
                "/list",
                "/show",
                "/hide",
                "/approvals",
                "/approve",
                "/deny",
                "/pause",
                "/resume",
                "/cancel",
                "/quit",
                "/exit",
            ],
            ignore_case=True,
            sentence=True,
        )
        self.input_buffer = Buffer(
            history=self.command_history,
            completer=self.command_completer,
            complete_while_typing=False,
            on_text_changed=self._on_input_changed,
        )
        self.watching_task_id: str | None = None
        self.watch_source: str | None = None  # run | show | None
        self.selected_task_id: str | None = None
        self.show_welcome = True
        self.show_options = False
        self.context_popup_mode: str | None = None  # ack | show | approve | deny | None
        self.context_candidates: list[tuple[str, str]] = []
        self.context_selected = 0
        self.option_items: list[tuple[str, str]] = [
            ("/run <task>", "/run "),
            ("/list", "/list"),
            ("/approvals", "/approvals"),
            ("/show <task_id>", "/show "),
            ("/hide", "/hide"),
            ("/approve <id>", "/approve "),
            ("/deny <id>", "/deny "),
            ("/pause <task_id>", "/pause "),
            ("/resume <task_id>", "/resume "),
            ("/cancel <task_id>", "/cancel "),
        ]
        self.option_selected = 0
        self.logs: list[str] = ["Paragents TUI ready."]
        self._watch_seen_count: dict[str, int] = {}
        self._watch_phase_by_task: dict[str, str] = {}
        self._pending_approval_by_task: dict[str, str] = {}
        self._side_log_task_id: str | None = None
        self._side_log_back_offset = 0
        self._side_log_page_size = 14
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._side_log_max_width = 88
        self._blink_on = True
        self._submit_slot_ids: list[str] = []
        self.parts = build_tui_layout(
            show_options_filter=Condition(lambda: self.show_options or self.context_popup_mode is not None),
            submit_count_provider=lambda: len(self._submit_task_views()),
        )
        self._install_controls()
        self.app = Application(
            layout=Layout(self.parts.root),
            key_bindings=self._build_key_bindings(),
            full_screen=True,
            refresh_interval=0.3,
            style=self._style(),
        )
        self.app.layout.focus(self.parts.command_bar)

    def _refresh_external_state(self) -> None:
        if self.watching_task_id_provider is not None:
            current_watch = self.watching_task_id_provider()
            if current_watch != self.watching_task_id and self.watching_task_id:
                self._pending_approval_by_task.pop(self.watching_task_id, None)
            if current_watch and current_watch not in self._watch_seen_count:
                self._watch_seen_count[current_watch] = 0
                self._watch_phase_by_task[current_watch] = "thinking..."
            self.watching_task_id = current_watch
            if self.watching_task_id is None:
                self.watch_source = None
            elif self.watch_source is None:
                self.watch_source = "show"
        if self.submit_slot_ids_provider is not None:
            next_slots = [tid for tid in self.submit_slot_ids_provider() if tid in self.scheduler.tasks]
            if self.watching_task_id:
                next_slots = [tid for tid in next_slots if tid != self.watching_task_id]
            self._submit_slot_ids = next_slots[:4]

    def _task_ref(self, task_id: str) -> str:
        task = self.scheduler.tasks.get(task_id)
        if task is None:
            return task_id[:6]
        return str(getattr(task, "task_ref", task_id[:6]))

    def _request_ref(self, request_id: str) -> str:
        rid = request_id.strip()
        if not rid:
            return ""
        if self.request_ref_provider is not None:
            resolved = self.request_ref_provider(rid).strip()
            if resolved:
                return resolved
        return rid[:6]

    def _style(self) -> Style:
        return Style.from_dict(
            {
                "": "bg:#0b1020 #c7d2fe",
                "root": "bg:#0b1020 #c7d2fe",
                "panel.main": "bg:#0b1020 #c7d2fe",
                "panel.command": "bg:#070b16 #dbe4ff",
                "panel.popup": "bg:#1a1436 #f5ddff",
                "separator": "bg:#0b1020 #3b82f6",
                "status.approval": "fg:#fbbf24 bold",
                "status.paused": "fg:#f59e0b",
                "status.failed": "fg:#ef4444 bold",
                "status.completed_ack": "fg:#22d3ee bold",
            }
        )

    def _install_controls(self) -> None:
        self.parts.log_panel.content = FormattedTextControl(self._log_panel_formatted)
        self.parts.submit_panel_1.content = FormattedTextControl(lambda: self._submit_panel_formatted(0))
        self.parts.submit_panel_2.content = FormattedTextControl(lambda: self._submit_panel_formatted(1))
        self.parts.submit_panel_3.content = FormattedTextControl(lambda: self._submit_panel_formatted(2))
        self.parts.submit_panel_4.content = FormattedTextControl(lambda: self._submit_panel_formatted(3))
        self.parts.command_bar.content = BufferControl(buffer=self.input_buffer, focusable=True)
        self.parts.command_prefix.content = FormattedTextControl("You: ")
        self.parts.options_popup.content = FormattedTextControl(self._popup_text)

    def _on_input_changed(self, _buffer: Buffer) -> None:
        if self.show_options:
            return
        self._refresh_context_popup()

    def _refresh_context_popup(self) -> None:
        raw = self.input_buffer.text.strip()
        lower = raw.lower()
        mode: str | None = None
        if re.fullmatch(r"/?ack(?:\s.*)?", lower):
            mode = "ack"
        elif re.fullmatch(r"/?show(?:\s.*)?", lower):
            mode = "show"
        elif re.fullmatch(r"/?approve(?:\s.*)?", lower):
            mode = "approve"
        elif re.fullmatch(r"/?deny(?:\s.*)?", lower):
            mode = "deny"
        if mode is None:
            self.context_popup_mode = None
            self.context_candidates = []
            self.context_selected = 0
            return
        candidates = self._context_candidate_ids(mode)
        self.context_popup_mode = mode
        self.context_candidates = candidates
        if not candidates:
            self.context_selected = 0
            return
        self.context_selected = max(0, min(self.context_selected, len(candidates) - 1))

    def _context_candidate_ids(self, mode: str) -> list[tuple[str, str]]:
        if mode == "ack":
            provider = self.ack_pending_task_ids_provider
            if provider is None:
                return []
            result: list[tuple[str, str]] = []
            for task_id in provider():
                task = self.scheduler.tasks.get(task_id)
                if task is None:
                    continue
                status = str(getattr(task, "status", ""))
                logs = self.scheduler.get_task_logs(task_id, limit=1)
                latest = self._compact_ids_in_text(logs[-1]) if logs else ""
                result.append((self._task_ref(task_id), f"{self._task_ref(task_id)} [{status}] {latest}"))
            return result
        if mode == "show":
            provider = self.show_candidate_task_ids_provider
            if provider is None:
                return []
            result: list[tuple[str, str]] = []
            for task_id in provider():
                task = self.scheduler.tasks.get(task_id)
                if task is None:
                    continue
                status = str(getattr(task, "status", ""))
                logs = self.scheduler.get_task_logs(task_id, limit=1)
                latest = self._compact_ids_in_text(logs[-1]) if logs else ""
                result.append((self._task_ref(task_id), f"{self._task_ref(task_id)} [{status}] {latest}"))
            return result
        request_provider = (
            self.approve_candidate_request_refs_provider
            if mode == "approve"
            else self.deny_candidate_request_refs_provider
        )
        if request_provider is None:
            return []
        return [(request_ref, f"{request_ref} [pending approval]") for request_ref in request_provider()]

    def _popup_text(self) -> str:
        if self.show_options:
            self.parts.popup_float.height = len(self.option_items) + 5
            return self._options_popup_text()
        lines = self._context_popup_text().splitlines()
        self.parts.popup_float.height = max(5, len(lines))
        return self._context_popup_text()

    def _context_popup_text(self) -> str:
        mode = self.context_popup_mode
        if mode is None:
            return ""
        inner_width = 52
        title_map = {
            "ack": " ACK Candidates ",
            "show": " SHOW Candidates ",
            "approve": " APPROVE Candidates ",
            "deny": " DENY Candidates ",
        }
        title = title_map.get(mode, " Candidates ")

        def _pad_display(text: str) -> str:
            width = get_cwidth(text)
            if width >= inner_width:
                return text
            return text + (" " * (inner_width - width))

        title_left = max(0, (inner_width - get_cwidth(title)) // 2)
        title_right = max(0, inner_width - get_cwidth(title) - title_left)
        lines = [
            f"┌{'─' * title_left}{title}{'─' * title_right}┐",
            f"│{_pad_display(' Up/Down 选择  Enter 回填命令（不执行）')}│",
            f"├{'─' * inner_width}┤",
        ]
        if not self.context_candidates:
            lines.append(f"│{_pad_display('(no candidates)')}│")
            lines.append(f"└{'─' * inner_width}┘")
            return "\n".join(lines)
        for idx, (_, label) in enumerate(self.context_candidates):
            pointer = ">" if idx == self.context_selected else " "
            text = f" {pointer} {label}"
            if get_cwidth(text) > inner_width:
                text = text[: inner_width - 3] + "..."
            lines.append(f"│{_pad_display(text)}│")
        lines.append(f"└{'─' * inner_width}┘")
        return "\n".join(lines)

    def _apply_context_selection(self) -> None:
        if self.context_popup_mode is None or not self.context_candidates:
            return
        idx = max(0, min(self.context_selected, len(self.context_candidates) - 1))
        token, _ = self.context_candidates[idx]
        prefix_map = {
            "ack": "/ack ",
            "show": "/show ",
            "approve": "/approve ",
            "deny": "/deny ",
        }
        prefix = prefix_map.get(self.context_popup_mode, "")
        self.input_buffer.text = prefix + token
        self.input_buffer.cursor_position = len(self.input_buffer.text)
        self.context_popup_mode = None
        self.context_candidates = []
        self.context_selected = 0

    def _log_line_limit(self) -> int:
        # Keep enough trailing lines to fill current viewport.
        # This avoids the old fixed tail(30) behavior that made bottom rows look unusable.
        default_rows = 36
        try:
            rows = int(self.app.output.get_size().rows)
        except Exception:
            rows = default_rows
        # Reserve rows for command bar/separators/spinner headroom.
        return max(30, rows - 4)

    def _log_panel_text(self) -> str:
        self._refresh_external_state()
        self._blink_on = not self._blink_on
        if self.show_welcome:
            return self._welcome_text()
        self._append_watch_logs()
        lines = self.logs[-self._log_line_limit() :]
        if self.watch_source is not None and self.watching_task_id and self.watching_task_id in self.scheduler.tasks:
            task = self.scheduler.tasks[self.watching_task_id]
            task_status = getattr(task, "status", "")
            if task_status in {"running", "pending", "paused"}:
                self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
                phase = self._watch_phase_by_task.get(self.watching_task_id, "thinking...")
                task_ref = self._task_ref(self.watching_task_id)
                lines.append(f"{self._spinner_frames[self._spinner_idx]} assistant({task_ref}): {phase}")
        return "\n".join(lines)

    def _log_panel_formatted(self) -> list[tuple[str, str]]:
        text = self._log_panel_text()
        fragments: list[tuple[str, str]] = []
        for line in text.splitlines():
            task_short_id = self._extract_task_short_id(line)
            if task_short_id:
                style = self._task_prefix_style(task_short_id)
                fragments.append((style, f"[{task_short_id}] "))
                fragments.append(("", line + "\n"))
            else:
                fragments.append(("", line + "\n"))
        if not fragments:
            fragments.append(("", ""))
        return fragments

    def _extract_task_short_id(self, line: str) -> str | None:
        m = re.search(r"assistant\(([0-9a-f]{6})\)", line)
        if m:
            return m.group(1)
        m = re.search(r"\[watch:([0-9a-f]{6})\]", line)
        if m:
            return m.group(1)
        return None

    def _task_prefix_style(self, task_short_id: str) -> str:
        digest = hashlib.md5(task_short_id.encode("utf-8")).hexdigest()
        # 取哈希前 6 位作为基础色，并抬亮避免暗背景下不清晰
        r = int(digest[0:2], 16)
        g = int(digest[2:4], 16)
        b = int(digest[4:6], 16)
        r = max(90, r)
        g = max(90, g)
        b = max(90, b)
        return f"fg:#{r:02x}{g:02x}{b:02x} bold"

    def _append_watch_logs(self) -> None:
        if self.watch_source is None:
            return
        if not self.watching_task_id:
            return
        if self.watching_task_id not in self.scheduler.tasks:
            return
        watch_logs = self.scheduler.get_task_logs(self.watching_task_id, limit=200)
        seen = self._watch_seen_count.get(self.watching_task_id, 0)
        if seen >= len(watch_logs):
            return
        for line in watch_logs[seen:]:
            self._ingest_watch_line(self.watching_task_id, line)
        self._watch_seen_count[self.watching_task_id] = len(watch_logs)

    def _compact_ids_in_text(self, text: str) -> str:
        def _replace_request_id(match: re.Match[str]) -> str:
            request_id = match.group(2)
            return f"{match.group(1)}{self._request_ref(request_id)}"

        compacted = re.sub(r"(request_id=)([0-9a-fA-F-]{6,})", _replace_request_id, text)
        compacted = re.sub(r"(task_id=)([0-9a-fA-F-]{6})[0-9a-fA-F-]*", r"\1\2", compacted)
        compacted = re.sub(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", lambda m: m.group(1)[:6], compacted)
        return compacted

    def _clip_side_log_line(self, text: str) -> str:
        if len(text) <= self._side_log_max_width:
            return text
        return text[: self._side_log_max_width - 3] + "..."

    def _ingest_watch_line(self, task_id: str, line: str) -> None:
        line = self._compact_ids_in_text(line)
        if "llm infer" in line:
            self._watch_phase_by_task[task_id] = "thinking..."
            return
        if "tool call" in line:
            self._watch_phase_by_task[task_id] = "calling tools..."
            return
        if "tool observation received" in line:
            self._watch_phase_by_task[task_id] = "processing..."
            return
        if "result:" in line:
            result = line.split("result:", 1)[1].strip()
            self.logs.append(f"assistant({self._task_ref(task_id)}): {result}")
            return
        if "failed:" in line:
            msg = line.split("failed:", 1)[1].strip()
            self.logs.append(f"[X FAILED] assistant({self._task_ref(task_id)}) error: {msg}")
            return
        if "finished successfully" in line:
            self.logs.append(f"assistant({self._task_ref(task_id)}): done")
            return
        if "needs approval" in line:
            self.logs.append(f"[! APPROVAL] assistant({self._task_ref(task_id)}): waiting for approval")
            return
        if "waiting for approval request_id=" in line:
            request_id = line.split("request_id=", 1)[1].strip()
            detail = ""
            if " detail=" in request_id:
                request_id, detail = request_id.split(" detail=", 1)
                detail = detail.strip()
            if detail:
                self.logs.append(f"[! APPROVAL] assistant({self._task_ref(task_id)}): {detail}")
            self.logs.append(
                f"[! APPROVAL] assistant({self._task_ref(task_id)}): 权限请求 {self._request_ref(request_id)}，输入 y/n 确认"
            )
            self._watch_phase_by_task[task_id] = "waiting approval..."
            self._pending_approval_by_task[task_id] = self._request_ref(request_id)
            return
        if "paused" in line and "approval" not in line.lower():
            self.logs.append(f"[! PAUSED] [watch:{self._task_ref(task_id)}] {line}")
            return
        self.logs.append(f"[watch:{self._task_ref(task_id)}] {line}")

    def _resolve_task_id_token(self, token: str) -> str | None:
        raw = token.strip()
        if not raw:
            return None
        if raw in self.scheduler.tasks:
            return raw
        by_ref = [task_id for task_id, task in self.scheduler.tasks.items() if str(getattr(task, "task_ref", "")) == raw]
        if len(by_ref) == 1:
            return by_ref[0]
        by_ref_prefix = [
            task_id for task_id, task in self.scheduler.tasks.items() if str(getattr(task, "task_ref", "")).startswith(raw)
        ]
        if len(by_ref_prefix) == 1:
            return by_ref_prefix[0]
        matched = [task_id for task_id in self.scheduler.tasks.keys() if task_id.startswith(raw)]
        if len(matched) == 1:
            return matched[0]
        return None

    def _welcome_text(self) -> str:
        return (
            "╔══════════════════════════════════════════════════════════╗\n"
            "║                    PARAGENTS TERMINAL                    ║\n"
            "║                     Claude-like TUI                      ║\n"
            "╠══════════════════════════════════════════════════════════╣\n"
            "║ Welcome. Type command and press Enter.                   ║\n"
            "║ Capacity: submit<=4, foreground-watch<=1 (4+1)           ║\n"
            "║ /show <id> -> foreground watch | /hide -> background run ║\n"
            "║ Tab complete | Up/Down history | Ctrl+O menu             ║\n"
            "║ Ctrl+C exit | Esc close welcome                          ║\n"
            "╚══════════════════════════════════════════════════════════╝"
        )

    def _options_popup_text(self) -> str:
        inner_width = 45

        def _pad_display(text: str) -> str:
            width = get_cwidth(text)
            if width >= inner_width:
                return text
            return text + (" " * (inner_width - width))

        title = " Command Palette "
        title_left = max(0, (inner_width - get_cwidth(title)) // 2)
        title_right = max(0, inner_width - get_cwidth(title) - title_left)
        lines = [
            f"┌{'─' * title_left}{title}{'─' * title_right}┐",
            f"│{_pad_display(' Up/Down 选择  Enter 回填  Esc 关闭')}│",
            f"├{'─' * inner_width}┤",
        ]
        for idx, (label, _) in enumerate(self.option_items, start=1):
            pointer = ">" if idx - 1 == self.option_selected else " "
            num = "0" if idx == 10 else str(idx)
            content = f" {pointer} {num}) {label}"
            lines.append(f"│{_pad_display(content)}│")
        lines.append(f"└{'─' * inner_width}┘")
        return "\n".join(lines)

    def _submit_task_views(self) -> list[Any]:
        self._refresh_external_state()
        logs_by_task = {tid: self.scheduler.get_task_logs(tid, limit=2000) for tid in self.scheduler.tasks}
        views = build_task_views(self.scheduler.tasks, logs_by_task)
        if self.watching_task_id:
            views = [v for v in views if v.task_id != self.watching_task_id]
        views = [v for v in views if v.task_id in self._submit_slot_ids]
        return views[:4]

    def _submit_panel_layout_hint(self, count: int, idx: int) -> str:
        if count <= 1:
            return "single"
        if count == 2:
            return "stacked"
        if count == 3:
            return "triple"
        return "quad"

    def _attention_marker(self, status: str, latest_log: str, pending_request_id: str = "") -> tuple[str, str]:
        lower = latest_log.lower()
        # Status should be the source of truth; logs are only hints.
        if status in {"failed", "cancelled"}:
            return ("[X FAILED]", "class:status.failed" if self._blink_on else "")
        if status == "completed":
            return ("[✓ ACK]", "class:status.completed_ack" if self._blink_on else "")
        if status == "paused":
            if pending_request_id or "approval" in lower or "request_id=" in lower:
                return ("[! APPROVAL]", "class:status.approval" if self._blink_on else "")
            return ("[! PAUSED]", "class:status.paused" if self._blink_on else "")
        if status == "pending":
            return ("[PENDING]", "")
        if status == "running":
            return ("[RUNNING]", "")
        return ("[RUNNING]", "")

    def _submit_panel_text(self, idx: int) -> str:
        views = self._submit_task_views()
        if idx >= len(views):
            return "Submit\n(no task)"
        v = views[idx]
        marker, _ = self._attention_marker(v.status, v.latest_log, v.pending_approval_request_id)
        layout_hint = self._submit_panel_layout_hint(len(views), idx)
        task_name = self.scheduler.tasks.get(v.task_id).input if self.scheduler.tasks.get(v.task_id) else ""
        task_name = task_name.strip().replace("\n", " ")
        if len(task_name) > 26:
            task_name = task_name[:23] + "..."
        def _clip_submit_line(text: str) -> str:
            if len(text) > 72:
                return text[:69] + "..."
            return text

        key_logs = [_clip_submit_line(self._compact_ids_in_text(line.strip())) for line in v.key_logs if line.strip()]
        key_logs_text = "\n".join(key_logs) if key_logs else _clip_submit_line(self._compact_ids_in_text((v.latest_log or "").strip()))
        return (
            f"Submit-{idx + 1} ({layout_hint}) {task_name or '(unnamed)'}\n"
            f"{marker} {self._task_ref(v.task_id)}  r={v.retries}\n"
            f"status={v.status}\n"
            f"{key_logs_text or '(no logs)'}"
        )

    def _submit_panel_formatted(self, idx: int) -> list[tuple[str, str]]:
        views = self._submit_task_views()
        if idx >= len(views):
            return [("", "Submit\n(no task)")]
        v = views[idx]
        _, style = self._attention_marker(v.status, v.latest_log, v.pending_approval_request_id)
        text = self._submit_panel_text(idx)
        lines = text.splitlines()
        fragments: list[tuple[str, str]] = []
        request_line_style = ""
        if v.status == "paused" and v.pending_approval_request_id:
            request_line_style = "class:status.approval" if self._blink_on else ""
        for line_idx, line in enumerate(lines):
            if line_idx == 1 and style:
                fragments.append((style, line + "\n"))
            elif "request_id=" in line and request_line_style:
                fragments.append((request_line_style, line + "\n"))
            else:
                fragments.append(("", line + "\n"))
        return fragments

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-c")
        def _exit(event) -> None:  # noqa: ANN001
            event.app.exit()

        @kb.add("tab")
        def _cycle_focus(event) -> None:  # noqa: ANN001
            if event.app.layout.has_focus(self.parts.command_bar):
                self.input_buffer.start_completion(select_first=False)
            else:
                event.app.layout.focus_next()

        @kb.add("c-o")
        def _toggle_options(event) -> None:  # noqa: ANN001
            self.show_options = not self.show_options
            if self.show_options:
                self.show_welcome = False
                self.context_popup_mode = None
                self.context_candidates = []
                self.context_selected = 0
            else:
                self._refresh_context_popup()

        @kb.add("escape")
        def _hide_overlays(event) -> None:  # noqa: ANN001
            self.show_welcome = False
            self.show_options = False
            self.context_popup_mode = None
            self.context_candidates = []
            self.context_selected = 0
            event.app.layout.focus(self.parts.command_bar)

        @kb.add("up")
        def _up(event) -> None:  # noqa: ANN001
            if self.show_options:
                self.option_selected = (self.option_selected - 1) % len(self.option_items)
                return
            if self.context_popup_mode is not None and self.context_candidates:
                self.context_selected = (self.context_selected - 1) % len(self.context_candidates)
                return
            if event.app.layout.has_focus(self.parts.command_bar):
                self.input_buffer.auto_up()
                return

        @kb.add("down")
        def _down(event) -> None:  # noqa: ANN001
            if self.show_options:
                self.option_selected = (self.option_selected + 1) % len(self.option_items)
                return
            if self.context_popup_mode is not None and self.context_candidates:
                self.context_selected = (self.context_selected + 1) % len(self.context_candidates)
                return
            if event.app.layout.has_focus(self.parts.command_bar):
                self.input_buffer.auto_down()
                return

        @kb.add("pageup")
        def _page_up(event) -> None:  # noqa: ANN001, ARG001
            active_task_id = self.watching_task_id
            if not active_task_id:
                return
            total = len(self.scheduler.get_task_logs(active_task_id, limit=5000))
            if total <= 0:
                return
            page_size = max(5, self._side_log_page_size)
            max_back_offset = max(0, total - page_size)
            self._side_log_back_offset = min(max_back_offset, self._side_log_back_offset + page_size)
            self._side_log_task_id = active_task_id

        @kb.add("pagedown")
        def _page_down(event) -> None:  # noqa: ANN001, ARG001
            active_task_id = self.watching_task_id
            if not active_task_id:
                return
            self._side_log_back_offset = max(0, self._side_log_back_offset - max(5, self._side_log_page_size))
            self._side_log_task_id = active_task_id

        @kb.add("enter")
        def _enter(event) -> None:  # noqa: ANN001
            if self.show_options:
                self._apply_selected_option()
                return
            if self.context_popup_mode is not None:
                self._apply_context_selection()
                event.app.layout.focus(self.parts.command_bar)
                return
            if not event.app.layout.has_focus(self.parts.command_bar):
                event.app.layout.focus(self.parts.command_bar)
                return
            raw = self.input_buffer.text.strip()
            if raw.isdigit() or re.fullmatch(r"\d+(?:/\d+)+", raw):
                cmd = raw
            else:
                cmd = raw.lower() if raw.lower() in {"y", "n"} else normalize_tui_command(raw)
            self.show_welcome = False
            if cmd:
                display = raw if raw else cmd
                self.logs.append(f"You: {display}")
                if cmd in {"quit", "exit"}:
                    event.app.exit()
                else:
                    event.app.create_background_task(self._run_command(cmd))
            self.input_buffer.text = ""
            event.app.layout.focus(self.parts.command_bar)

        @kb.add("1")
        def _opt_1(event) -> None:  # noqa: ANN001
            self._handle_option_digit("1")

        @kb.add("2")
        def _opt_2(event) -> None:  # noqa: ANN001
            self._handle_option_digit("2")

        @kb.add("3")
        def _opt_3(event) -> None:  # noqa: ANN001
            self._handle_option_digit("3")

        @kb.add("4")
        def _opt_4(event) -> None:  # noqa: ANN001
            self._handle_option_digit("4")

        @kb.add("5")
        def _opt_5(event) -> None:  # noqa: ANN001
            self._handle_option_digit("5")

        @kb.add("6")
        def _opt_6(event) -> None:  # noqa: ANN001
            self._handle_option_digit("6")

        @kb.add("7")
        def _opt_7(event) -> None:  # noqa: ANN001
            self._handle_option_digit("7")

        @kb.add("8")
        def _opt_8(event) -> None:  # noqa: ANN001
            self._handle_option_digit("8")

        @kb.add("9")
        def _opt_9(event) -> None:  # noqa: ANN001
            self._handle_option_digit("9")

        @kb.add("0")
        def _opt_0(event) -> None:  # noqa: ANN001
            self._handle_option_digit("0")

        return kb

    async def _run_command(self, cmd: str) -> None:
        prev_watching_task_id = self.watching_task_id
        if cmd.startswith("submit ") and self.watch_source == "run":
            # submit 模式默认不应继续占用主面板自动 watch
            self.watching_task_id = None
            self.watch_source = None
        output = await self.command_handler(cmd)
        if self.watching_task_id_provider is not None or self.submit_slot_ids_provider is not None:
            self._refresh_external_state()
        else:
            self._update_watch_state(cmd, output)
            self._sync_submit_slots_from_output(cmd, output, prev_watching_task_id)
        if cmd in {"y", "n"} and self.watching_task_id:
            self._pending_approval_by_task.pop(self.watching_task_id, None)
        if output and output[0] == "unknown command":
            output = [
                f"不支持的指令: {cmd}",
                "可用命令: /run, /list, /show, /hide, /approvals, /approve, /deny, /pause, /resume, /cancel, /quit",
                "提示: 普通语句会自动当作 /run <语句>。",
            ]
        if cmd.startswith("run "):
            self.logs.extend(self._shorten_ids(output if output else ["(no output)"]))
            self.app.layout.focus(self.parts.command_bar)
            return
        self.logs.extend(self._shorten_ids(output if output else ["(no output)"]))
        self.app.layout.focus(self.parts.command_bar)

    def _sync_submit_slots_from_output(self, cmd: str, output: list[str], prev_watching_task_id: str | None) -> None:
        for task_id in list(self._submit_slot_ids):
            if task_id not in self.scheduler.tasks:
                self._submit_slot_ids.remove(task_id)
        if cmd.startswith("submit "):
            for line in output:
                if line.startswith("submitted: "):
                    parts = line.split()
                    if len(parts) >= 2:
                        task_id = self._resolve_task_id_token(parts[1]) or parts[1]
                        if task_id in self.scheduler.tasks and task_id not in self._submit_slot_ids:
                            self._submit_slot_ids.append(task_id)
        if cmd.startswith("run ") or cmd.startswith("show "):
            if self.watching_task_id and self.watching_task_id in self._submit_slot_ids:
                self._submit_slot_ids.remove(self.watching_task_id)
        if cmd in {"hide", "h"}:
            if prev_watching_task_id and prev_watching_task_id in self.scheduler.tasks and prev_watching_task_id not in self._submit_slot_ids:
                task = self.scheduler.tasks[prev_watching_task_id]
                if getattr(task, "status", "") not in {"completed", "failed", "cancelled"}:
                    self._submit_slot_ids.append(prev_watching_task_id)
        for line in output:
            m = re.search(r"已知悉任务 ([0-9a-f]{6}(?:,\s*[0-9a-f]{6})*)", line)
            if not m:
                continue
            shorts = [x.strip() for x in m.group(1).split(",")]
            self._submit_slot_ids = [tid for tid in self._submit_slot_ids if self._task_ref(tid) not in shorts]

    def _update_watch_state(self, cmd: str, output: list[str]) -> None:
        if cmd.startswith("run "):
            for line in output:
                if line.startswith("submitted: "):
                    parts = line.split()
                    if len(parts) >= 2:
                        resolved_task_id = self._resolve_task_id_token(parts[1])
                        if not resolved_task_id:
                            continue
                        self.watching_task_id = resolved_task_id
                        self.watch_source = "run"
                        self._watch_seen_count[self.watching_task_id] = 0
                        self._watch_phase_by_task[self.watching_task_id] = "thinking..."
                        self._pending_approval_by_task.pop(self.watching_task_id, None)
                        return
        if cmd.startswith("show "):
            resolved_task_id = self._resolve_task_id_token(cmd.split(" ", 1)[1].strip())
            if not resolved_task_id:
                return
            self.watching_task_id = resolved_task_id
            self.watch_source = "show"
            self._watch_seen_count[self.watching_task_id] = 0
            return
        if cmd == "show" and self.watching_task_id and output:
            return
        if cmd in {"hide", "h"}:
            if self.watching_task_id:
                self._pending_approval_by_task.pop(self.watching_task_id, None)
            self.watching_task_id = None
            self.watch_source = None

    def _handle_option_digit(self, digit: str) -> None:
        if not self.show_options:
            self.input_buffer.insert_text(digit)
            return
        if digit == "0":
            self.option_selected = 9
        elif digit.isdigit():
            idx = int(digit) - 1
            if 0 <= idx < len(self.option_items):
                self.option_selected = idx
        self._apply_selected_option()

    def _apply_selected_option(self) -> None:
        _, template = self.option_items[self.option_selected]
        self.input_buffer.text = template
        self.input_buffer.cursor_position = len(template)
        self.show_options = False

    def _shorten_ids(self, lines: list[str]) -> list[str]:
        return [re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27}\b", lambda m: m.group(0)[:6], line) for line in lines]

    async def run_async(self) -> None:
        await self.app.run_async()


async def run_tui(
    scheduler: Scheduler,
    pending_approvals_provider: Callable[[], int],
    command_handler: Callable[[str], Awaitable[list[str]]],
    watching_task_id_provider: Callable[[], str | None] | None = None,
    submit_slot_ids_provider: Callable[[], list[str]] | None = None,
    ack_pending_task_ids_provider: Callable[[], list[str]] | None = None,
    show_candidate_task_ids_provider: Callable[[], list[str]] | None = None,
    approve_candidate_request_refs_provider: Callable[[], list[str]] | None = None,
    deny_candidate_request_refs_provider: Callable[[], list[str]] | None = None,
    request_ref_provider: Callable[[str], str] | None = None,
) -> None:
    await ParagentsTUI(
        scheduler=scheduler,
        pending_approvals_provider=pending_approvals_provider,
        command_handler=command_handler,
        watching_task_id_provider=watching_task_id_provider,
        submit_slot_ids_provider=submit_slot_ids_provider,
        ack_pending_task_ids_provider=ack_pending_task_ids_provider,
        show_candidate_task_ids_provider=show_candidate_task_ids_provider,
        approve_candidate_request_refs_provider=approve_candidate_request_refs_provider,
        deny_candidate_request_refs_provider=deny_candidate_request_refs_provider,
        request_ref_provider=request_ref_provider,
    ).run_async()
