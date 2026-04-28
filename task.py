from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from id_refs import default_short_ref

PromptStatus = Literal[
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class Prompt:
    prompt_id: str
    input: str
    prompt_ref: str = ""
    session_id: str | None = None
    run_dir: str = ""
    status: PromptStatus = "pending"
    result: Optional[Any] = None
    error: Optional[str] = None
    local_state: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    max_retries: int = 2
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def __post_init__(self) -> None:
        if not self.prompt_ref:
            self.prompt_ref = default_short_ref(self.prompt_id, length=6)
        if self.session_id is None:
            self.session_id = self.prompt_id
