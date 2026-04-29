from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class PreflightIntent:
    capabilities: set[str] = field(default_factory=set)
    actions: set[str] = field(default_factory=set)
    output_paths: set[str] = field(default_factory=set)
    confidence: float = 0.35
    notes: list[str] = field(default_factory=list)

    def to_resource_keys(self) -> set[str]:
        keys: set[str] = set()
        for cap in self.capabilities:
            keys.add(f"perm:{cap}")
        for action in self.actions:
            keys.add(action)
        for path in self.output_paths:
            keys.add(f"out:{path}")
        return keys

    def to_state(self) -> dict[str, object]:
        raw = asdict(self)
        return {
            "capabilities": sorted(raw["capabilities"]),
            "actions": sorted(raw["actions"]),
            "output_paths": sorted(raw["output_paths"]),
            "confidence": float(raw["confidence"]),
            "notes": list(raw["notes"]),
        }


def infer_preflight_intent(text: str) -> PreflightIntent:
    lowered = text.lower()
    intent = PreflightIntent()

    git_phrases = ("提交代码", "提交改动", "代码提交", "改动提交", "commit", "git commit", "git add")
    github_phrases = ("发起pr", "pull request", "创建pr", "提交pr", "merge request")
    python_phrases = ("python", ".py", "脚本", "运行脚本")
    shell_phrases = ("执行命令", "终端", "shell", "bash", "zsh")

    has_implicit_git = any(p in lowered for p in git_phrases) or bool(
        re.search(r"(?:代码|改动).{0,4}提交|提交.{0,4}(?:代码|改动)", text)
    )
    if has_implicit_git:
        intent.capabilities.add("git")
        intent.actions.add("git:commit")
        intent.notes.append("detected implicit git intent")
    if any(p in lowered for p in github_phrases):
        intent.capabilities.add("github")
        intent.actions.add("github:write")
        intent.notes.append("detected implicit github intent")
    if any(p in lowered for p in python_phrases):
        intent.capabilities.add("python")
    if any(p in lowered for p in shell_phrases):
        intent.capabilities.add("shell")

    for match in re.findall(r"(?:输出到|保存到|写入|to)\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+)", text):
        normalized = match.strip().lstrip("./")
        if normalized:
            intent.output_paths.add(normalized)
    for match in re.findall(r"(?:^|\s)(?:>>|>)\s*([^\s|;&]+)", text):
        normalized = match.strip().strip("'\"").lstrip("./")
        if normalized:
            intent.output_paths.add(normalized)

    signal_count = len(intent.capabilities) + len(intent.actions) + len(intent.output_paths)
    if signal_count >= 4:
        intent.confidence = 0.82
    elif signal_count >= 2:
        intent.confidence = 0.62
    elif signal_count == 1:
        intent.confidence = 0.48

    return intent


def _merge_intents(primary: PreflightIntent, secondary: PreflightIntent) -> PreflightIntent:
    merged = PreflightIntent()
    merged.capabilities = set(primary.capabilities).union(secondary.capabilities)
    merged.actions = set(primary.actions).union(secondary.actions)
    merged.output_paths = set(primary.output_paths).union(secondary.output_paths)
    merged.notes = [*primary.notes, *secondary.notes]
    merged.confidence = max(float(primary.confidence), float(secondary.confidence))
    return merged


def _intent_from_obj(obj: dict[str, Any]) -> PreflightIntent:
    intent = PreflightIntent()
    caps = obj.get("capabilities", [])
    actions = obj.get("actions", [])
    outputs = obj.get("output_paths", [])
    notes = obj.get("notes", [])
    confidence = obj.get("confidence", 0.35)
    if isinstance(caps, list):
        intent.capabilities = {str(x).strip() for x in caps if str(x).strip()}
    if isinstance(actions, list):
        intent.actions = {str(x).strip() for x in actions if str(x).strip()}
    if isinstance(outputs, list):
        intent.output_paths = {str(x).strip().lstrip("./") for x in outputs if str(x).strip()}
    if isinstance(notes, list):
        intent.notes = [str(x) for x in notes]
    try:
        intent.confidence = float(confidence)
    except Exception:
        intent.confidence = 0.35
    return intent


async def infer_preflight_intent_with_llm(text: str, llm_client: Any | None = None) -> PreflightIntent:
    rule_intent = infer_preflight_intent(text)
    if llm_client is None:
        return rule_intent
    prompt = (
        "请从以下用户输入中提取最小预判意图，仅用于并发冲突和权限预估。\n"
        "输出严格JSON对象，不要markdown，不要解释。\n"
        "schema: {\"capabilities\":[str],\"actions\":[str],\"output_paths\":[str],\"confidence\":float,\"notes\":[str]}\n"
        "capabilities候选: filesystem,shell,python,git,github,web,mcp\n"
        "actions示例: git:commit,git:push,github:write\n"
        "output_paths仅保留可能写入目标路径。\n"
        f"输入: {text}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        output = await llm_client.infer(messages)
    except Exception:
        return rule_intent
    llm_obj: dict[str, Any] | None = None
    if isinstance(output, dict):
        if {"capabilities", "actions", "output_paths"}.intersection(set(output.keys())):
            llm_obj = output
        elif isinstance(output.get("content"), str):
            content = str(output.get("content", "")).strip()
            if content:
                try:
                    llm_obj = json.loads(content)
                except Exception:
                    llm_obj = None
    if not isinstance(llm_obj, dict):
        return rule_intent
    llm_intent = _intent_from_obj(llm_obj)
    return _merge_intents(rule_intent, llm_intent)
