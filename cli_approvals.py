from __future__ import annotations

from approval_view import format_approval_request
from permissions import PermissionManager
from ui_cli import paginate_lines

_last_index_map: dict[int, str] = {}


def handle_approval_command(cmd: str, permission_manager: PermissionManager) -> tuple[bool, list[str]]:
    if cmd == "approvals" or cmd.startswith("approvals "):
        page, size = _parse_page_size(cmd)
        pending = permission_manager.list_pending()
        if not pending:
            return True, ["(no pending approvals)"]
        _last_index_map.clear()
        lines: list[str] = []
        for idx, req in enumerate(pending, start=1):
            _last_index_map[idx] = req.request_id
            lines.append(f"#{idx} {format_approval_request(req)}")
        return True, paginate_lines(lines, page=page, size=size)

    if cmd.startswith("approve "):
        rest = cmd.split(" ", 1)[1].strip()
        parts = rest.split()
        request_id_input = parts[0] if parts else ""
        request_id = _resolve_request_id(request_id_input, permission_manager)
        always = len(parts) > 1 and parts[1].lower() == "always"
        if not request_id_input:
            return True, ["missing request id"]
        if not request_id:
            return True, ["approval request not found"]
        ok = permission_manager.approve(request_id, always=always)
        if ok:
            req = permission_manager._pending.get(request_id)  # test/runtime output helper
            shown = req.request_ref if req is not None else request_id[:6]
            return True, [f"approved {shown} {'(persisted)' if always else '(session)'}"]
        return True, ["approval request not found"]

    if cmd.startswith("deny "):
        request_id_input = cmd.split(" ", 1)[1].strip()
        request_id = _resolve_request_id(request_id_input, permission_manager)
        if not request_id_input:
            return True, ["missing request id"]
        if not request_id:
            return True, ["approval request not found"]
        req = permission_manager._pending.get(request_id)  # test/runtime output helper
        shown = req.request_ref if req is not None else request_id[:6]
        ok = permission_manager.deny(request_id)
        if not ok:
            return True, ["approval request not found"]
        return True, [f"denied {shown}"]

    return False, []


def _parse_page_size(cmd: str) -> tuple[int, int]:
    page = 1
    size = 20
    tokens = cmd.split()
    for i, token in enumerate(tokens):
        if token == "--page" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            page = int(tokens[i + 1])
        if token == "--size" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            size = int(tokens[i + 1])
    return page, size


def _resolve_request_id(request_id_input: str, permission_manager: PermissionManager) -> str:
    raw = request_id_input.strip()
    if not raw:
        return ""
    if raw.startswith("#") and raw[1:].isdigit():
        return _last_index_map.get(int(raw[1:]), "")
    pending = permission_manager.list_pending()
    pending_ids = [req.request_id for req in pending]
    if raw in pending_ids:
        return raw
    ref_matched = [req.request_id for req in pending if req.request_ref == raw]
    if len(ref_matched) == 1:
        return ref_matched[0]
    matched = [rid for rid in pending_ids if rid.startswith(raw)]
    if len(matched) == 1:
        return matched[0]
    ref_prefix = [req.request_id for req in pending if req.request_ref.startswith(raw)]
    if len(ref_prefix) == 1:
        return ref_prefix[0]
    return ""
