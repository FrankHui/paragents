from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class FsScope:
    path: str
    read: bool = True
    write: bool = False


@dataclass
class PermissionsConfig:
    capabilities: dict[str, bool] = field(
        default_factory=lambda: {
            "filesystem": True,
            "shell": False,
            "git": False,
            "github": False,
            "web": False,
            "mcp": False,
        }
    )
    fs_scopes: list[FsScope] = field(default_factory=list)
    shell_policy: dict[str, list[str]] = field(
        default_factory=lambda: {
            "blocked": ["rm -rf /", "shutdown", "reboot"],
            "auto_approved": ["pwd", "ls"],
            "needs_approval": ["python", "python3", "git", "npm", "uv"],
        }
    )
    git_policy: dict[str, list[str]] = field(
        default_factory=lambda: {
            "auto_approved": ["status", "diff", "log"],
            "needs_approval": ["add", "commit", "push"],
        }
    )
    github_policy: dict[str, list[str]] = field(
        default_factory=lambda: {
            "auto_approved_methods": ["GET"],
            "needs_approval_methods": ["POST", "PUT", "PATCH", "DELETE"],
        }
    )


@dataclass
class ApprovalRequest:
    request_id: str
    request_type: str
    payload: dict[str, str]
    status: str = "pending"


@dataclass
class PermissionDecision:
    allowed: bool
    request_id: str | None = None
    reason: str | None = None


class PermissionManager:
    def __init__(self, config: PermissionsConfig, config_path: Path) -> None:
        self._config = config
        self._config_path = config_path
        self._pending: dict[str, ApprovalRequest] = {}
        self._pending_shell_by_command: dict[str, str] = {}
        self._pending_git_by_action: dict[str, str] = {}
        self._pending_github_by_action: dict[str, str] = {}
        self._session_approved_commands: set[str] = set()
        self._session_approved_git_actions: set[str] = set()
        self._session_approved_github_actions: set[str] = set()

    @classmethod
    def load_or_create(cls) -> "PermissionManager":
        path = Path.cwd() / "permissions.json"
        if not path.exists():
            workspace = str(Path.cwd().resolve())
            default_cfg = PermissionsConfig(fs_scopes=[FsScope(path=workspace, read=True, write=True)])
            mgr = cls(default_cfg, path)
            mgr.save()
            return mgr

        raw = json.loads(path.read_text(encoding="utf-8"))
        scopes = [
            FsScope(
                path=str(item.get("path", "")),
                read=bool(item.get("read", True)),
                write=bool(item.get("write", False)),
            )
            for item in raw.get("fs_scopes", [])
        ]
        config = PermissionsConfig(
            capabilities=dict(raw.get("capabilities", {})) or PermissionsConfig().capabilities,
            fs_scopes=scopes,
            shell_policy=dict(raw.get("shell_policy", {})) or PermissionsConfig().shell_policy,
            git_policy=dict(raw.get("git_policy", {})) or PermissionsConfig().git_policy,
            github_policy=dict(raw.get("github_policy", {})) or PermissionsConfig().github_policy,
        )
        return cls(config, path)

    def save(self) -> None:
        payload = {
            "capabilities": self._config.capabilities,
            "fs_scopes": [asdict(s) for s in self._config.fs_scopes],
            "shell_policy": self._config.shell_policy,
            "git_policy": self._config.git_policy,
            "github_policy": self._config.github_policy,
        }
        self._config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def check_capability(self, capability: str) -> bool:
        return bool(self._config.capabilities.get(capability, False))

    def check_fs_access(self, raw_path: str, mode: str) -> PermissionDecision:
        target = Path(raw_path).expanduser().resolve()
        for scope in self._config.fs_scopes:
            base = Path(scope.path).expanduser().resolve()
            try:
                inside_scope = target == base or target.is_relative_to(base)
            except Exception:
                inside_scope = False
            if not inside_scope:
                continue
            if mode == "read" and scope.read:
                return PermissionDecision(allowed=True)
            if mode == "write" and scope.write:
                return PermissionDecision(allowed=True)

        request_id = str(uuid.uuid4())
        self._pending[request_id] = ApprovalRequest(
            request_id=request_id,
            request_type="fs_scope",
            payload={"path": str(target), "mode": mode},
        )
        return PermissionDecision(
            allowed=False,
            request_id=request_id,
            reason=f"Path not in allowed scope for {mode}",
        )

    def check_shell_command(self, command: str) -> PermissionDecision:
        normalized = command.strip()
        lowered = normalized.lower()
        policy = self._config.shell_policy

        for blocked in policy.get("blocked", []):
            if blocked.lower() in lowered:
                return PermissionDecision(allowed=False, reason=f"Command blocked by policy: {blocked}")

        if normalized in self._session_approved_commands:
            return PermissionDecision(allowed=True)

        for auto in policy.get("auto_approved", []):
            if lowered.startswith(auto.lower()):
                return PermissionDecision(allowed=True)

        for need in policy.get("needs_approval", []):
            if lowered.startswith(need.lower()):
                existing_request_id = self._pending_shell_by_command.get(normalized)
                if existing_request_id:
                    existing_req = self._pending.get(existing_request_id)
                    if existing_req is not None and existing_req.status == "pending":
                        return PermissionDecision(
                            allowed=False,
                            request_id=existing_request_id,
                            reason=f"Command requires approval: {normalized}",
                        )
                request_id = str(uuid.uuid4())
                self._pending[request_id] = ApprovalRequest(
                    request_id=request_id,
                    request_type="shell_command",
                    payload={"command": normalized},
                )
                self._pending_shell_by_command[normalized] = request_id
                return PermissionDecision(
                    allowed=False,
                    request_id=request_id,
                    reason=f"Command requires approval: {normalized}",
                )

        return PermissionDecision(allowed=True)

    def check_git_action(self, action: str) -> PermissionDecision:
        if not self.check_capability("git"):
            return PermissionDecision(allowed=False, reason="git capability disabled")
        normalized = action.strip().lower()
        if normalized in self._session_approved_git_actions:
            return PermissionDecision(allowed=True)
        policy = self._config.git_policy
        if normalized in [a.lower() for a in policy.get("auto_approved", [])]:
            return PermissionDecision(allowed=True)
        if normalized in [a.lower() for a in policy.get("needs_approval", [])]:
            existing = self._pending_git_by_action.get(normalized)
            if existing and self._pending.get(existing) and self._pending[existing].status == "pending":
                return PermissionDecision(False, request_id=existing, reason=f"Git action requires approval: {normalized}")
            request_id = str(uuid.uuid4())
            self._pending[request_id] = ApprovalRequest(
                request_id=request_id, request_type="git_action", payload={"action": normalized}
            )
            self._pending_git_by_action[normalized] = request_id
            return PermissionDecision(False, request_id=request_id, reason=f"Git action requires approval: {normalized}")
        return PermissionDecision(allowed=True)

    def check_github_request(self, method: str, path: str) -> PermissionDecision:
        if not self.check_capability("github"):
            return PermissionDecision(allowed=False, reason="github capability disabled")
        normalized_method = method.strip().upper()
        key = f"{normalized_method}:{path.strip()}"
        if key in self._session_approved_github_actions:
            return PermissionDecision(allowed=True)
        policy = self._config.github_policy
        if normalized_method in [m.upper() for m in policy.get("auto_approved_methods", [])]:
            return PermissionDecision(allowed=True)
        if normalized_method in [m.upper() for m in policy.get("needs_approval_methods", [])]:
            existing = self._pending_github_by_action.get(key)
            if existing and self._pending.get(existing) and self._pending[existing].status == "pending":
                return PermissionDecision(False, request_id=existing, reason=f"GitHub request needs approval: {key}")
            request_id = str(uuid.uuid4())
            self._pending[request_id] = ApprovalRequest(
                request_id=request_id, request_type="github_action", payload={"action": key}
            )
            self._pending_github_by_action[key] = request_id
            return PermissionDecision(False, request_id=request_id, reason=f"GitHub request needs approval: {key}")
        return PermissionDecision(allowed=True)

    def list_pending(self) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.status == "pending"]

    def approve(self, request_id: str, always: bool = False) -> bool:
        req = self._pending.get(request_id)
        if req is None or req.status != "pending":
            return False
        req.status = "approved"
        if req.request_type == "fs_scope":
            path = req.payload["path"]
            mode = req.payload["mode"]
            self._grant_fs_scope(path, mode)
            if always:
                self.save()
        if req.request_type == "shell_command":
            command = req.payload["command"]
            self._session_approved_commands.add(command)
            self._pending_shell_by_command.pop(command, None)
            if always:
                self._grant_shell_auto_approval(command)
                self.save()
        if req.request_type == "git_action":
            action = req.payload["action"]
            self._session_approved_git_actions.add(action)
            self._pending_git_by_action.pop(action, None)
            if always:
                self._grant_git_auto_approval(action)
                self.save()
        if req.request_type == "github_action":
            action = req.payload["action"]
            self._session_approved_github_actions.add(action)
            self._pending_github_by_action.pop(action, None)
            if always:
                self.save()
        return True

    def deny(self, request_id: str) -> bool:
        req = self._pending.get(request_id)
        if req is None or req.status != "pending":
            return False
        req.status = "denied"
        if req.request_type == "shell_command":
            command = req.payload.get("command")
            if command:
                self._pending_shell_by_command.pop(command, None)
        if req.request_type == "git_action":
            action = req.payload.get("action")
            if action:
                self._pending_git_by_action.pop(action, None)
        if req.request_type == "github_action":
            action = req.payload.get("action")
            if action:
                self._pending_github_by_action.pop(action, None)
        return True

    def _grant_fs_scope(self, path: str, mode: str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        existing = None
        for scope in self._config.fs_scopes:
            if str(Path(scope.path).expanduser().resolve()) == resolved:
                existing = scope
                break
        if existing is None:
            existing = FsScope(path=resolved, read=False, write=False)
            self._config.fs_scopes.append(existing)
        if mode == "read":
            existing.read = True
        if mode == "write":
            existing.write = True

    def _grant_shell_auto_approval(self, command: str) -> None:
        auto = self._config.shell_policy.setdefault("auto_approved", [])
        if command not in auto:
            auto.append(command)

    def _grant_git_auto_approval(self, action: str) -> None:
        auto = self._config.git_policy.setdefault("auto_approved", [])
        if action not in auto:
            auto.append(action)

    def describe(self) -> dict[str, object]:
        return {
            "config_path": str(self._config_path),
            "capabilities": dict(self._config.capabilities),
            "fs_scopes": [asdict(s) for s in self._config.fs_scopes],
        }
