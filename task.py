from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from id_refs import default_short_ref

TaskStatus = Literal[
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class Task:
    task_id: str
    input: str
    task_ref: str = ""
    parent_task_id: str | None = None
    lineage_root_id: str | None = None
    run_dir: str = ""
    status: TaskStatus = "pending"
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
        if not self.task_ref:
            self.task_ref = default_short_ref(self.task_id, length=6)
        if self.lineage_root_id is None:
            self.lineage_root_id = self.parent_task_id or self.task_id
