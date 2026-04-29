from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


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
