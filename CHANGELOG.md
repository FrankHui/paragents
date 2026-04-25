# Changelog

## v0.2.0

### Added
- Multi-task runtime with isolated `Task` execution loops and lifecycle controls.
- Approval-aware permission system with request queue, command approval, and file scope checks.
- Main/subagent tool profile split with `spawn` flow and restricted subagent toolset.
- MCP runtime registry with namespaced tool wrapping and server-level whitelist filtering.
- CLI scheduling capabilities: create/list/cancel delayed tasks.
- CLI interaction upgrades:
  - watch/hide/show task log workflow
  - aliases (`:s`, `:sv`, `:a`, `:h`, `:p`)
  - approvals index shortcuts (`approve #1`)
  - task-id to request-id approval mapping
  - startup panels, status line, prompt/footer styling
  - readline keybindings (`Ctrl+U`, `Ctrl+W`, `Ctrl+A`, `Ctrl+E`, `Ctrl+K`, `Ctrl+Y`)
  - pagination support for list/approvals/schedules
  - colored watch logs and pixel-style home icon

### Changed
- Tool execution now pauses task flow when `needs_approval` is returned, rather than treating it as normal completion.
- Approved requests can auto-resume paused tasks waiting on the same request id.

### Testing
- Adopted TDD-first workflow for ongoing changes.
- Full suite currently green with `uv run pytest -q`.
