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
        request_id = parts[0] if parts else ""
        if request_id.startswith("#") and request_id[1:].isdigit():
            request_id = _last_index_map.get(int(request_id[1:]), "")
        always = len(parts) > 1 and parts[1].lower() == "always"
        if not request_id:
            return True, ["missing request id"]
        ok = permission_manager.approve(request_id, always=always)
        if ok:
            return True, [f"approved {request_id} {'(persisted)' if always else '(session)'}"]
        return True, ["approval request not found"]

    if cmd.startswith("deny "):
        request_id = cmd.split(" ", 1)[1].strip()
        if request_id.startswith("#") and request_id[1:].isdigit():
            request_id = _last_index_map.get(int(request_id[1:]), "")
        if not request_id:
            return True, ["missing request id"]
        ok = permission_manager.deny(request_id)
        return True, ["denied" if ok else "approval request not found"]

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
