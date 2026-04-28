from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from id_refs import default_short_ref, generate_short_ref


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
            "python": True,
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
    python_policy: dict[str, list[str]] = field(
        default_factory=lambda: {
            "blocked": [],
            "auto_approved": ["python", "python3"],
            "needs_approval": [],
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
    request_ref: str = ""
    owner_prompt_id: str | None = None
    owner_session_id: str | None = None
    status: str = "pending"

    def __post_init__(self) -> None:
        if not self.request_ref:
            self.request_ref = default_short_ref(self.request_id, length=6)


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
        self._pending_shell_by_command: dict[tuple[str, str], str] = {}
        self._pending_python_by_command: dict[tuple[str, str], str] = {}
        self._pending_git_by_action: dict[tuple[str, str], str] = {}
        self._pending_github_by_action: dict[tuple[str, str], str] = {}
        self._pending_capability_by_name: dict[tuple[str, str], str] = {}
        self._session_approved_commands: dict[str, set[str]] = {}
        self._session_approved_python_commands: dict[str, set[str]] = {}
        self._session_approved_git_actions: dict[str, set[str]] = {}
        self._session_approved_github_actions: dict[str, set[str]] = {}
        self._session_enabled_capabilities: dict[str, set[str]] = {}

    def _scope(self, prompt_id: str | None = None, session_id: str | None = None) -> str:
        if session_id:
            return f"session:{session_id}"
        if prompt_id:
            return f"prompt:{prompt_id}"
        return "__global__"

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
            python_policy=dict(raw.get("python_policy", {})) or PermissionsConfig().python_policy,
            git_policy=dict(raw.get("git_policy", {})) or PermissionsConfig().git_policy,
            github_policy=dict(raw.get("github_policy", {})) or PermissionsConfig().github_policy,
        )
        return cls(config, path)

    def save(self) -> None:
        payload = {
            "capabilities": self._config.capabilities,
            "fs_scopes": [asdict(s) for s in self._config.fs_scopes],
            "shell_policy": self._config.shell_policy,
            "python_policy": self._config.python_policy,
            "git_policy": self._config.git_policy,
            "github_policy": self._config.github_policy,
        }
        self._config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def check_capability(
        self, capability: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> bool:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        if capability in self._session_enabled_capabilities.get(scope, set()):
            return True
        return bool(self._config.capabilities.get(capability, False))

    def check_capability_decision(
        self, capability: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> PermissionDecision:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        if self.check_capability(capability, prompt_id=prompt_id, session_id=session_id):
            return PermissionDecision(allowed=True)
        existing = self._pending_capability_by_name.get((scope, capability))
        if existing and self._pending.get(existing) and self._pending[existing].status == "pending":
            return PermissionDecision(
                allowed=False,
                request_id=existing,
                reason=f"{capability} capability waiting for approval",
            )
        request_id = str(uuid.uuid4())
        existing_refs = {r.request_ref for r in self._pending.values()}
        request_ref = generate_short_ref(existing_refs, length=6)
        self._pending[request_id] = ApprovalRequest(
            request_id=request_id,
            request_ref=request_ref,
            request_type="capability_enable",
            payload={"capability": capability},
            owner_prompt_id=prompt_id,
            owner_session_id=session_id,
        )
        self._pending_capability_by_name[(scope, capability)] = request_id
        return PermissionDecision(
            allowed=False,
            request_id=request_id,
            reason=f"{capability} capability waiting for approval",
        )

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
        existing_refs = {r.request_ref for r in self._pending.values()}
        request_ref = generate_short_ref(existing_refs, length=6)
        self._pending[request_id] = ApprovalRequest(
            request_id=request_id,
            request_ref=request_ref,
            request_type="fs_scope",
            payload={"path": str(target), "mode": mode},
        )
        return PermissionDecision(
            allowed=False,
            request_id=request_id,
            reason=f"Path not in allowed scope for {mode}",
        )

    def check_shell_command(
        self, command: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> PermissionDecision:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        normalized = command.strip()
        lowered = normalized.lower()
        policy = self._config.shell_policy

        for blocked in policy.get("blocked", []):
            if blocked.lower() in lowered:
                return PermissionDecision(allowed=False, reason=f"Command blocked by policy: {blocked}")

        if normalized in self._session_approved_commands.get(scope, set()):
            return PermissionDecision(allowed=True)

        for auto in policy.get("auto_approved", []):
            if lowered.startswith(auto.lower()):
                return PermissionDecision(allowed=True)

        for need in policy.get("needs_approval", []):
            if lowered.startswith(need.lower()):
                existing_request_id = self._pending_shell_by_command.get((scope, normalized))
                if existing_request_id:
                    existing_req = self._pending.get(existing_request_id)
                    if existing_req is not None and existing_req.status == "pending":
                        return PermissionDecision(
                            allowed=False,
                            request_id=existing_request_id,
                            reason=f"Command requires approval: {normalized}",
                        )
                request_id = str(uuid.uuid4())
                existing_refs = {r.request_ref for r in self._pending.values()}
                request_ref = generate_short_ref(existing_refs, length=6)
                self._pending[request_id] = ApprovalRequest(
                    request_id=request_id,
                    request_ref=request_ref,
                    request_type="shell_command",
                    payload={"command": normalized},
                    owner_prompt_id=prompt_id,
                    owner_session_id=session_id,
                )
                self._pending_shell_by_command[(scope, normalized)] = request_id
                return PermissionDecision(
                    allowed=False,
                    request_id=request_id,
                    reason=f"Command requires approval: {normalized}",
                )

        return PermissionDecision(allowed=True)

    def check_python_command(
        self, command: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> PermissionDecision:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        cap_decision = self.check_capability_decision("python", prompt_id=prompt_id, session_id=session_id)
        if not cap_decision.allowed:
            return cap_decision

        normalized = command.strip()
        lowered = normalized.lower()
        policy = self._config.python_policy

        for blocked in policy.get("blocked", []):
            if blocked.lower() in lowered:
                return PermissionDecision(allowed=False, reason=f"Python command blocked by policy: {blocked}")

        if normalized in self._session_approved_python_commands.get(scope, set()):
            return PermissionDecision(allowed=True)

        for auto in policy.get("auto_approved", []):
            if lowered.startswith(auto.lower()):
                return PermissionDecision(allowed=True)

        for need in policy.get("needs_approval", []):
            if lowered.startswith(need.lower()):
                existing_request_id = self._pending_python_by_command.get((scope, normalized))
                if existing_request_id:
                    existing_req = self._pending.get(existing_request_id)
                    if existing_req is not None and existing_req.status == "pending":
                        return PermissionDecision(
                            allowed=False,
                            request_id=existing_request_id,
                            reason=f"Python command requires approval: {normalized}",
                        )
                request_id = str(uuid.uuid4())
                existing_refs = {r.request_ref for r in self._pending.values()}
                request_ref = generate_short_ref(existing_refs, length=6)
                self._pending[request_id] = ApprovalRequest(
                    request_id=request_id,
                    request_ref=request_ref,
                    request_type="python_command",
                    payload={"command": normalized},
                    owner_prompt_id=prompt_id,
                    owner_session_id=session_id,
                )
                self._pending_python_by_command[(scope, normalized)] = request_id
                return PermissionDecision(
                    allowed=False,
                    request_id=request_id,
                    reason=f"Python command requires approval: {normalized}",
                )

        return PermissionDecision(allowed=True)

    def check_git_action(
        self, action: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> PermissionDecision:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        cap_decision = self.check_capability_decision("git", prompt_id=prompt_id, session_id=session_id)
        if not cap_decision.allowed:
            return cap_decision
        normalized = action.strip().lower()
        if normalized in self._session_approved_git_actions.get(scope, set()):
            return PermissionDecision(allowed=True)
        policy = self._config.git_policy
        if normalized in [a.lower() for a in policy.get("auto_approved", [])]:
            return PermissionDecision(allowed=True)
        if normalized in [a.lower() for a in policy.get("needs_approval", [])]:
            existing = self._pending_git_by_action.get((scope, normalized))
            if existing and self._pending.get(existing) and self._pending[existing].status == "pending":
                return PermissionDecision(False, request_id=existing, reason=f"Git action requires approval: {normalized}")
            request_id = str(uuid.uuid4())
            existing_refs = {r.request_ref for r in self._pending.values()}
            request_ref = generate_short_ref(existing_refs, length=6)
            self._pending[request_id] = ApprovalRequest(
                request_id=request_id,
                request_ref=request_ref,
                request_type="git_action",
                payload={"action": normalized},
                owner_prompt_id=prompt_id,
                owner_session_id=session_id,
            )
            self._pending_git_by_action[(scope, normalized)] = request_id
            return PermissionDecision(False, request_id=request_id, reason=f"Git action requires approval: {normalized}")
        return PermissionDecision(allowed=True)

    def check_github_request(
        self, method: str, path: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> PermissionDecision:
        scope = self._scope(prompt_id=prompt_id, session_id=session_id)
        cap_decision = self.check_capability_decision("github", prompt_id=prompt_id, session_id=session_id)
        if not cap_decision.allowed:
            return cap_decision
        normalized_method = method.strip().upper()
        key = f"{normalized_method}:{path.strip()}"
        if key in self._session_approved_github_actions.get(scope, set()):
            return PermissionDecision(allowed=True)
        policy = self._config.github_policy
        if normalized_method in [m.upper() for m in policy.get("auto_approved_methods", [])]:
            return PermissionDecision(allowed=True)
        if normalized_method in [m.upper() for m in policy.get("needs_approval_methods", [])]:
            existing = self._pending_github_by_action.get((scope, key))
            if existing and self._pending.get(existing) and self._pending[existing].status == "pending":
                return PermissionDecision(False, request_id=existing, reason=f"GitHub request needs approval: {key}")
            request_id = str(uuid.uuid4())
            existing_refs = {r.request_ref for r in self._pending.values()}
            request_ref = generate_short_ref(existing_refs, length=6)
            self._pending[request_id] = ApprovalRequest(
                request_id=request_id,
                request_ref=request_ref,
                request_type="github_action",
                payload={"action": key},
                owner_prompt_id=prompt_id,
                owner_session_id=session_id,
            )
            self._pending_github_by_action[(scope, key)] = request_id
            return PermissionDecision(False, request_id=request_id, reason=f"GitHub request needs approval: {key}")
        return PermissionDecision(allowed=True)

    def list_pending(self, prompt_id: str | None = None, session_id: str | None = None) -> list[ApprovalRequest]:
        if prompt_id is None and session_id is None:
            return [r for r in self._pending.values() if r.status == "pending"]
        return [
            r
            for r in self._pending.values()
            if r.status == "pending"
            and (prompt_id is None or r.owner_prompt_id == prompt_id)
            and (session_id is None or r.owner_session_id == session_id)
        ]

    def get_pending(
        self, request_id: str, prompt_id: str | None = None, session_id: str | None = None
    ) -> ApprovalRequest | None:
        req = self._pending.get(request_id)
        if req is None or req.status != "pending":
            return None
        if prompt_id is not None and req.owner_prompt_id != prompt_id:
            return None
        if session_id is not None and req.owner_session_id != session_id:
            return None
        return req

    def approve(
        self,
        request_id: str,
        always: bool = False,
        prompt_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        req = self._pending.get(request_id)
        if req is None or req.status != "pending":
            return False
        if prompt_id is not None and req.owner_prompt_id != prompt_id:
            return False
        if session_id is not None and req.owner_session_id != session_id:
            return False
        scope = self._scope(prompt_id=req.owner_prompt_id, session_id=req.owner_session_id)
        req.status = "approved"
        if req.request_type == "fs_scope":
            path = req.payload["path"]
            mode = req.payload["mode"]
            self._grant_fs_scope(path, mode)
            if always:
                self.save()
        if req.request_type == "shell_command":
            command = req.payload["command"]
            self._session_approved_commands.setdefault(scope, set()).add(command)
            self._pending_shell_by_command.pop((scope, command), None)
            if always:
                self._grant_shell_auto_approval(command)
                self.save()
        if req.request_type == "python_command":
            command = req.payload["command"]
            self._session_approved_python_commands.setdefault(scope, set()).add(command)
            self._pending_python_by_command.pop((scope, command), None)
            if always:
                self._grant_python_auto_approval(command)
                self.save()
        if req.request_type == "git_action":
            action = req.payload["action"]
            self._session_approved_git_actions.setdefault(scope, set()).add(action)
            self._pending_git_by_action.pop((scope, action), None)
            if always:
                self._grant_git_auto_approval(action)
                self.save()
        if req.request_type == "github_action":
            action = req.payload["action"]
            self._session_approved_github_actions.setdefault(scope, set()).add(action)
            self._pending_github_by_action.pop((scope, action), None)
            if always:
                self.save()
        if req.request_type == "capability_enable":
            capability = req.payload["capability"]
            self._session_enabled_capabilities.setdefault(scope, set()).add(capability)
            self._pending_capability_by_name.pop((scope, capability), None)
            if always:
                self._config.capabilities[capability] = True
                self.save()
        return True

    def deny(self, request_id: str, prompt_id: str | None = None, session_id: str | None = None) -> bool:
        req = self._pending.get(request_id)
        if req is None or req.status != "pending":
            return False
        if prompt_id is not None and req.owner_prompt_id != prompt_id:
            return False
        if session_id is not None and req.owner_session_id != session_id:
            return False
        scope = self._scope(prompt_id=req.owner_prompt_id, session_id=req.owner_session_id)
        req.status = "denied"
        if req.request_type == "shell_command":
            command = req.payload.get("command")
            if command:
                self._pending_shell_by_command.pop((scope, command), None)
        if req.request_type == "python_command":
            command = req.payload.get("command")
            if command:
                self._pending_python_by_command.pop((scope, command), None)
        if req.request_type == "git_action":
            action = req.payload.get("action")
            if action:
                self._pending_git_by_action.pop((scope, action), None)
        if req.request_type == "github_action":
            action = req.payload.get("action")
            if action:
                self._pending_github_by_action.pop((scope, action), None)
        if req.request_type == "capability_enable":
            capability = req.payload.get("capability")
            if capability:
                self._pending_capability_by_name.pop((scope, capability), None)
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

    def _grant_python_auto_approval(self, command: str) -> None:
        auto = self._config.python_policy.setdefault("auto_approved", [])
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
