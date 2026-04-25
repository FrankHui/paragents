from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    model: str
    enabled: bool


@dataclass
class RuntimeSettings:
    default_provider: str = "openai"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)


def get_runtime_config_path() -> Path:
    # 与项目放在一起，方便本地开发与版本管理控制
    return Path.cwd() / "runtime_config.json"


def _env_provider_defaults() -> dict[str, ProviderConfig]:
    return {
        "openai": ProviderConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            enabled=os.getenv("OPENAI_ENABLED", "true").lower() == "true",
        ),
        "deepseek": ProviderConfig(
            name="deepseek",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            enabled=os.getenv("DEEPSEEK_ENABLED", "true").lower() == "true",
        ),
        "grok": ProviderConfig(
            name="grok",
            api_key=os.getenv("GROK_API_KEY", ""),
            base_url=os.getenv("GROK_BASE_URL", "https://api.x.ai/v1"),
            model=os.getenv("GROK_MODEL", "grok-4"),
            enabled=os.getenv("GROK_ENABLED", "true").lower() == "true",
        ),
    }


def load_runtime_settings() -> RuntimeSettings | None:
    path = get_runtime_config_path()
    if not path.exists():
        return None

    raw = json.loads(path.read_text(encoding="utf-8"))
    providers_raw = raw.get("providers", {})
    providers: dict[str, ProviderConfig] = {}
    for name, value in providers_raw.items():
        providers[name] = ProviderConfig(
            name=name,
            api_key=str(value.get("api_key", "")),
            base_url=str(value.get("base_url", "")),
            model=str(value.get("model", "")),
            enabled=bool(value.get("enabled", True)),
        )

    return RuntimeSettings(
        default_provider=str(raw.get("default_provider", "openai")),
        providers=providers,
    )


def save_runtime_settings(settings: RuntimeSettings) -> None:
    path = get_runtime_config_path()
    payload = {
        "default_provider": settings.default_provider,
        "providers": {name: asdict(cfg) for name, cfg in settings.providers.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_provider_configs(settings: RuntimeSettings | None = None) -> dict[str, ProviderConfig]:
    if settings is not None:
        return dict(settings.providers)
    loaded = load_runtime_settings()
    if loaded is not None and loaded.providers:
        return dict(loaded.providers)
    return _env_provider_defaults()


def get_default_provider_name(settings: RuntimeSettings | None = None) -> str:
    if settings is not None:
        return settings.default_provider
    loaded = load_runtime_settings()
    if loaded is not None:
        return loaded.default_provider
    return os.getenv("DEFAULT_PROVIDER", "openai")


def is_provider_configured(provider: ProviderConfig) -> bool:
    return provider.enabled and len(provider.api_key.strip()) > 0


def build_interactive_settings(existing: RuntimeSettings | None = None) -> RuntimeSettings:
    current = existing or RuntimeSettings(default_provider="openai", providers=_env_provider_defaults())
    providers = dict(current.providers) if current.providers else _env_provider_defaults()

    print("\n=== Runtime 配置向导 ===")
    print("支持 provider: openai / deepseek / grok")
    default_provider = input(f"默认 provider [{current.default_provider}]: ").strip() or current.default_provider
    if default_provider not in providers:
        print(f"未知 provider '{default_provider}'，回退为 openai")
        default_provider = "openai"

    target = providers[default_provider]
    print(f"\n配置 provider: {default_provider}")
    api_key = input(f"API Key [{_mask_key(target.api_key)}]: ").strip() or target.api_key
    base_url = input(f"Base URL [{target.base_url}]: ").strip() or target.base_url
    model = input(f"Model [{target.model}]: ").strip() or target.model
    enabled_raw = input(f"Enabled (true/false) [{'true' if target.enabled else 'false'}]: ").strip().lower()
    enabled = target.enabled if enabled_raw == "" else enabled_raw == "true"

    providers[default_provider] = ProviderConfig(
        name=default_provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        enabled=enabled,
    )
    return RuntimeSettings(default_provider=default_provider, providers=providers)


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
