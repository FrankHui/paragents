from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Any

import httpx

from config import (
    ProviderConfig,
    RuntimeSettings,
    build_provider_configs,
    get_default_provider_name,
    is_provider_configured,
)


class RateLimitError(Exception):
    pass


class TransientError(Exception):
    pass


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMClient:
    def __init__(
        self,
        max_concurrency: int = 8,
        timeout_s: float = 30,
        max_retries: int = 3,
        provider_name: str | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._providers = build_provider_configs(runtime_settings)
        self._default_provider_name = provider_name or get_default_provider_name(runtime_settings)
        self._last_successful_provider: str | None = None

    async def infer(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        last_error: Exception | None = None
        for provider in self._iter_fallback_providers():
            try:
                response = await self._infer_with_provider(provider, messages)
                self._last_successful_provider = provider.name
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("No LLM providers available. Please configure API keys.")

    async def _infer_with_provider(
        self,
        provider: ProviderConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                async with self._sem:
                    return await asyncio.wait_for(
                        self._chat_completions(provider, messages),
                        timeout=self._timeout_s,
                    )
            except (TimeoutError, RateLimitError, TransientError):
                if attempt >= self._max_retries:
                    raise
                backoff = (2**attempt) + random.random()
                await asyncio.sleep(backoff)

    async def _chat_completions(
        self,
        provider: ProviderConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        endpoint = self._build_chat_completions_url(provider.base_url)
        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(endpoint, headers=headers, json=payload)

        if response.status_code == 429:
            raise RateLimitError(response.text)
        if response.status_code >= 500:
            raise TransientError(response.text)
        if response.status_code >= 400:
            raise RuntimeError(f"LLM request failed ({response.status_code}): {response.text}")

        data = response.json()
        content = self._extract_content(data)
        usage = self._extract_usage(data)
        action = self._parse_agent_action(content)
        action["provider"] = provider.name
        action["model"] = provider.model
        action["usage"] = {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
        return action

    def _iter_fallback_providers(self) -> list[ProviderConfig]:
        configured = [p for p in self._providers.values() if is_provider_configured(p)]
        if not configured:
            return []

        chosen_name = self._last_successful_provider or self._default_provider_name
        configured.sort(key=lambda p: 0 if p.name == chosen_name else 1)
        return configured

    @staticmethod
    def _build_chat_completions_url(base_url: str) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM response has no choices")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            # 兼容某些 provider 把 content 拆成数组段
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> LLMUsage:
        usage = data.get("usage", {}) or {}
        return LLMUsage(
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
        )

    @staticmethod
    def _parse_agent_action(content: str) -> dict[str, Any]:
        # 约定 LLM 输出 JSON：
        # {"type":"final","content":"..."}
        # {"type":"tool","tool_name":"calculator","args":{"expression":"1+1"}}
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "type" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        return {"type": "final", "content": content}
