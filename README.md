# Paragents

并发、隔离的 Agent Runtime System（Python + asyncio）。

## 使用 uv 管理环境

1. 安装依赖

```bash
uv sync
```

2. 启动 CLI（首次会进入配置向导）

```bash
uv run python main.py
```

3. CLI 中可随时重配

```text
setup
show-config
```

## 说明

- 使用 OpenAI-compatible `chat/completions` 接口
- 支持并发限制、超时、429/5xx 重试、provider fallback
- 配置持久化到 `runtime_config.json`
