from __future__ import annotations

import uuid


def default_short_ref(source_id: str, length: int = 6) -> str:
    normalized = source_id.replace("-", "")
    if len(normalized) >= length:
        return normalized[:length]
    return source_id[:length]


def generate_short_ref(existing_refs: set[str], length: int = 6) -> str:
    while True:
        candidate = uuid.uuid4().hex[:length]
        if candidate not in existing_refs:
            return candidate
