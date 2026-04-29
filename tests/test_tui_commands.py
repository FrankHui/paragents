from __future__ import annotations

from tui_state import normalize_tui_command


def test_normalize_tui_command_aliases() -> None:
    assert normalize_tui_command(":s hello") == "prompt hello"
    assert normalize_tui_command(":sv hi") == "prompt hi"
    assert normalize_tui_command(":a") == "approvals"
    assert normalize_tui_command("/list") == "list"
    assert normalize_tui_command("写一段总结") == "写一段总结"
