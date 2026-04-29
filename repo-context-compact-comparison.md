# 上下文与 Compact 设计对照（4 个仓库）

| Repo | Session 连续性模型 | Prompt 构建策略 | Compact 策略 | 中断/恢复机制 | 接口扩展设计 |
|---|---|---|---|---|---|
| `claude-code` | 从 changelog 可见有强 `resume/continue` 语义（大 session 场景持续修复） | 核心实现未在该仓库公开；可见能力信号包括 `/context`、`/recap`、long session 管理 | 明确有 `auto-compact`、`PreCompact hook`、上下文窗口修复 | 多次修复 `--resume`/`--continue` 丢上下文问题，说明有链路恢复逻辑 | Hook 与 settings 扩展强（Pre/Post/Stop 等）；compact 可被 hook 阻断 |
| `mercury-agent` | 以 `channelId/conversationId` 为短期会话键，短期记忆持久化到磁盘 | `systemPrompt + relevantFacts + recentMemory + currentUser` 直拼 | 无独立压缩引擎抽象；主要靠 `recent N` 控制窗口 | 无明显 checkpoint 回填层；偏简单持久化后继续 | 扩展点在 capabilities/tools/providers，context/compact 插件化较弱 |
| `hermes-agent-main` | session 概念完整，并发上下文隔离（`contextvars`） | 形成“上下文引擎”治理，支持按引擎策略装配 | `ContextEngine` 抽象 + 默认 `ContextCompressor`；支持 preflight、focus 压缩、模型切换适配 | 有 checkpoint/批处理恢复与大量异常分支测试 | 强接口化：`ContextEngine`、`MemoryProvider`、hook 生命周期、tool schema 注入 |
| `nanobot` | `SessionManager` 一等公民：`messages + metadata + last_consolidated` 持久化 | `ContextBuilder` 分层：system(identity/bootstrap/memory/skills/history) + runtime context + current input | 双路径：`maybe_consolidate_by_tokens`（在线）+ `AutoCompact`（idle TTL） | 具备 runtime checkpoint 恢复；`/stop` 后可 materialize 已完成上下文 | 结构化扩展较好：context builder、memory store、consolidator、auto-compact 独立模块 |

## 对 Paragents 的映射建议

- Session 连续 agent：优先借鉴 `nanobot` 的会话状态持久与恢复闭环。
- Compact 抽象化：优先借鉴 `hermes-agent-main` 的 `ContextEngine` 接口模式。
- 恢复与鲁棒性：组合 `nanobot` checkpoint 物化 + `hermes` 的错误分流思路。
- 策略治理：参考 `claude-code` 的 hook 阶段化与可配置策略。
