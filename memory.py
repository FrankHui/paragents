from __future__ import annotations

from typing import Any


class LocalMemory:
    def __init__(self) -> None:
        self._items: list[Any] = []

    async def append(self, item: Any) -> None:
        self._items.append(item)

    async def snapshot(self) -> list[Any]:
        return list(self._items)


class SharedReadonlyMemory:
    def __init__(self, docs: list[str] | None = None) -> None:
        self._docs = docs or []

    async def query(self, keyword: str) -> list[str]:
        keyword_lower = keyword.lower()
        return [d for d in self._docs if keyword_lower in d.lower()]
