from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

ToolFunc = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class MCPRegistry:
    def __init__(self) -> None:
        self._servers: dict[str, dict[str, ToolFunc]] = {}

    def register_server(self, server_name: str, tools: dict[str, ToolFunc]) -> None:
        self._servers[server_name] = dict(tools)

    def build_wrapped_tools(self, enabled_tools: dict[str, list[str]] | None = None) -> dict[str, ToolFunc]:
        wrapped: dict[str, ToolFunc] = {}
        for server, tools in self._servers.items():
            allow = None if enabled_tools is None else enabled_tools.get(server, [])
            for name, tool_func in tools.items():
                if allow is not None and allow != ["*"] and name not in allow:
                    continue

                async def _wrapped(args: dict[str, Any], fn=tool_func) -> dict[str, Any]:
                    return await fn(args)

                wrapped[f"mcp_{server}_{name}"] = _wrapped
        return wrapped
