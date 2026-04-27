from __future__ import annotations

from cli_approvals import handle_approval_command
from permissions import ApprovalRequest, FsScope, PermissionManager, PermissionsConfig


def _build_manager(tmp_path):
    cfg = PermissionsConfig(
        capabilities={
            "filesystem": True,
            "shell": True,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        },
        fs_scopes=[FsScope(path=str(tmp_path), read=True, write=True)],
    )
    return PermissionManager(cfg, tmp_path / "permissions.json")


def test_approvals_command_formats_pending(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    req = ApprovalRequest(
        request_id="r1",
        request_type="shell_command",
        payload={"command": "python3 --version"},
    )
    manager._pending["r1"] = req  # test fixture setup
    handled, lines = handle_approval_command("approvals", manager)
    assert handled is True
    assert any("shell_command" in line for line in lines)
    assert any("#1" in line for line in lines)


def test_approve_and_deny_commands(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager._pending["r1"] = ApprovalRequest("r1", "shell_command", {"command": "python3 --version"})
    manager._pending["r2"] = ApprovalRequest("r2", "fs_scope", {"path": "/tmp/a", "mode": "read"})

    handled1, lines1 = handle_approval_command("approve r1", manager)
    handled2, lines2 = handle_approval_command("deny r2", manager)
    assert handled1 is True and handled2 is True
    assert any("approved r1" in line for line in lines1)
    assert any("denied" in line for line in lines2)


def test_approve_with_index_shortcut(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager._pending["r1"] = ApprovalRequest("r1", "shell_command", {"command": "python3 --version"})
    handled1, _ = handle_approval_command("approvals", manager)
    handled2, lines2 = handle_approval_command("approve #1 always", manager)
    assert handled1 is True and handled2 is True
    assert any("approved r1" in line for line in lines2)


def test_approvals_pagination(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    for i in range(1, 6):
        rid = f"r{i}"
        manager._pending[rid] = ApprovalRequest(rid, "shell_command", {"command": f"python3 -c {i}"})
    handled, lines = handle_approval_command("approvals --page 2 --size 2", manager)
    assert handled is True
    assert len(lines) == 2


def test_non_approval_command_not_handled(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    handled, lines = handle_approval_command("list", manager)
    assert handled is False
    assert lines == []


def test_approve_same_request_twice_reports_not_found(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager._pending["r1"] = ApprovalRequest("r1", "shell_command", {"command": "python3 --version"})
    handled1, lines1 = handle_approval_command("approve r1", manager)
    handled2, lines2 = handle_approval_command("approve r1", manager)
    assert handled1 is True and handled2 is True
    assert any("approved r1" in line for line in lines1)
    assert lines2 == ["approval request not found"]


def test_approve_with_short_prefix_request_id(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    manager._pending["abc12345-1111-2222-3333-444444444444"] = ApprovalRequest(
        "abc12345-1111-2222-3333-444444444444",
        "shell_command",
        {"command": "python3 --version"},
    )
    handled, lines = handle_approval_command("approve abc123", manager)
    assert handled is True
    assert any("approved abc123" in line for line in lines)


def test_deny_missing_id_returns_error_line(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    handled, lines = handle_approval_command("deny ", manager)
    assert handled is True
    assert lines == ["missing request id"]
