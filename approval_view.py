from __future__ import annotations

from permissions import ApprovalRequest


def format_approval_request(req: ApprovalRequest) -> str:
    short_request_id = req.request_ref
    if req.request_type == "fs_scope":
        mode = req.payload.get("mode", "read")
        path = req.payload.get("path", "")
        return f"{short_request_id} | fs_scope | mode={mode} | path={path}"
    if req.request_type == "shell_command":
        command = req.payload.get("command", "")
        return f"{short_request_id} | shell_command | command={command}"
    return f"{short_request_id} | {req.request_type} | payload={req.payload}"
