from __future__ import annotations

from approval_view import format_approval_request
from permissions import ApprovalRequest


def test_format_fs_scope_request() -> None:
    req = ApprovalRequest(
        request_id="r1",
        request_type="fs_scope",
        payload={"path": "/tmp/a.txt", "mode": "read"},
    )
    text = format_approval_request(req)
    assert "r1" in text
    assert "fs_scope" in text
    assert "read" in text
    assert "/tmp/a.txt" in text


def test_format_shell_command_request() -> None:
    req = ApprovalRequest(
        request_id="r2",
        request_type="shell_command",
        payload={"command": "python3 --version"},
    )
    text = format_approval_request(req)
    assert "r2" in text
    assert "shell_command" in text
    assert "python3 --version" in text
