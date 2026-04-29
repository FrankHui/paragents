# 5 仓特性映射清单（Paragents + 4 参考仓）

> 标记说明：
> - `代码验证`：已在对应仓代码中看到实现或接口。
> - `文档/变更信号`：主要来自 README/CHANGELOG/配置示例，核心实现未完全开源。

## 1. 权限与能力治理

| 维度 | Paragents | claude-code | mercury-agent | hermes-agent-main | nanobot |
|---|---|---|---|---|---|
| 能力开关 | `PermissionsConfig.capabilities`（代码验证） | settings 中按工具权限治理（文档/变更信号） | `permissions.yaml` + capability registry（代码验证） | toolset/gateway 组合治理（代码验证） | `ToolsConfig` 级开关（代码验证） |
| ask/deny 语义 | `needs_approval/blocked/auto_approved`（代码验证） | 显式 `ask/deny`（代码验证） | 命令模式匹配审批（代码验证） | 审批更偏运行链路（代码验证） | 以 enable/sandbox/restrict 为主（代码验证） |
| 文件范围控制 | `fs_scopes`（代码验证） | 通过工具权限和策略组合（文档/变更信号） | file scopes（代码验证） | 多在 tool runtime（代码验证） | `restrict_to_workspace`（代码验证） |
| 沙箱/网络策略 | 当前较轻（代码验证） | `sandbox.network.*`（代码验证） | shell cwd 等基础约束（代码验证） | 更偏网关与运行治理（代码验证） | `exec.sandbox` + SSRF 白名单（代码验证） |

## 2. 上下文、压缩、恢复

| 维度 | Paragents | claude-code | mercury-agent | hermes-agent-main | nanobot |
|---|---|---|---|---|---|
| Session 连续性 | session worker + 单 session 复用 agent（代码验证） | `--resume/--continue` 强语义（文档/变更信号） | conversationId 级短期记忆（代码验证） | session + contextvars 隔离（代码验证） | `SessionManager` 持久化（代码验证） |
| Prompt 构建 | `PromptAssembler`（代码验证） | 内核未完全公开（文档/变更信号） | `system + relevantFacts + recentMemory + user`（代码验证） | ContextEngine 统一治理（代码验证） | ContextBuilder 分层组装（代码验证） |
| Compact 策略 | `should_compact()/compact()`（代码验证） | auto-compact + pre-compact hook（文档/变更信号） | 主要 recent N 控制（代码验证） | ContextEngine + Compressor（代码验证） | 在线 consolidate + idle auto-compact（代码验证） |
| 中断/恢复 | `CheckpointRecovery + SessionStateStore`（代码验证） | 长会话恢复持续修复（文档/变更信号） | 记忆持久化继续执行（代码验证） | checkpoint manager（代码验证） | runtime checkpoint + stop 保留上下文（代码验证） |

## 3. Paragents 当前状态结论

- 权限侧“策略配置文件 + ask/deny 语义”**已部分具备**：`permissions.json` + `blocked/needs_approval/auto_approved`。
- 与 claude-code 的差异主要在：
  - ask/deny 目前按 capability/policy 分散，不是统一规则层；
  - 缺少更完整的策略层级（managed/user/project）与工具级统一规则解释。

