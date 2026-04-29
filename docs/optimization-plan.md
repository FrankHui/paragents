# Paragents 优化计划（v1）

本文档汇总当前阶段可执行的优化项，按优先级分层，并给出建议顺序与验收标准。

## P0：IM 接入与多 Session 可用性（最高优先）

### 0) 支持通过 Telegram 等 IM 远程调用本地 Agent
- **目标**：像其他参考仓一样，支持在 IM 中触发本机 Paragents 能力。
- **关键问题**：
  - 如何在一个 Telegram Bot 内并行管理多个 session；
  - 如何保证消息可读性与可操作性（尤其在多 session 同时输出时）。
- **总体方案（最小可用）**：
  - 增加 IM 网关层（先做 Telegram），作为 `Scheduler` 的上游输入与输出分发器；
  - 采用 `chat_id + session_ref` 作为会话路由主键；
  - 通过命令协议显式切换/创建会话（避免隐式混乱）；
  - 输出统一带 `session_ref` 前缀，降低串线成本。
- **验收**：
  - 单个 bot 下可同时驱动 3~5 个 session；
  - 在群聊/私聊场景中可稳定路由到正确 session；
  - 用户能看懂“哪条回复属于哪个 session”。

## P0：稳定性与可恢复性（先做）

### 1) Session 状态持久化原子写
- **问题**：`JsonlSessionStateStore` 当前整文件重写，异常中断可能损坏文件。
- **改造**：
  - 采用临时文件写入（`*.tmp`）后原子替换（rename）。
  - 失败时保留旧文件并记录错误日志。
- **验收**：
  - 模拟写入中断后，重启可正常加载旧状态。
  - 不出现空文件/半截 JSONL 导致启动失败。

### 2) Session 状态写入并发保护
- **问题**：多 worker 写状态时可能互相覆盖。
- **改造**：
  - `SessionStateStore` 增加进程内锁（至少 asyncio 级别）。
  - flush 时串行执行。
- **验收**：
  - 并发提交 3+ session 压测后，JSONL 内容无丢失、无错乱。

### 3) 状态 schema/version 与容错迁移
- **问题**：后续字段演进可能导致历史状态不可读。
- **改造**：
  - 每条 session 记录增加 `schema_version`。
  - 加入向后兼容解析逻辑（缺失字段补默认值）。
- **验收**：
  - 低版本状态文件可被新代码读取并自动补齐。

### 4) Checkpoint 生命周期清晰化
- **问题**：turn/session checkpoint 边界不够清晰，排障成本高。
- **改造**：
  - 区分 `turn_checkpoint` 与 `session_checkpoint`。
  - debug 输出明确当前恢复来源。
- **验收**：
  - 恢复日志可清晰定位“恢复了哪一层状态”。

---

## P1：上下文压缩与 Prompt 质量（核心体验）

### 5) Compaction 策略化触发
- **问题**：当前压缩触发主要依赖 recent_turns 数量。
- **改造**：
  - `should_compact()` 支持多维阈值（turn 数、字符量、估算 token、tool 输出膨胀）。
  - 阈值配置化（可通过配置文件调整）。
- **验收**：
  - 长对话、少工具与短对话、重工具两种场景均触发合理。

### 6) Compact 内容结构化
- **问题**：压缩摘要仍偏拼接，语义保真度有限。
- **改造**：
  - 结构化摘要模板：`已完成` / `未完成` / `关键约束` / `下一步`。
  - 限制摘要长度，避免反向污染 prompt。
- **验收**：
  - 多轮后 `compact_notes` 可读且对后续回答有帮助。

### 7) Prompt 分层开关化（Hermes/Nanobot 最小策略增强）
- **当前已做**：runtime metadata + memory summary + compact notes + recent turns + latest user input。
- **下一步**：
  - 增加开关：`runtime_block_enabled`、`memory_summary_enabled`、`compact_notes_enabled`。
  - 允许 session 级别覆盖默认策略（A/B 测试）。
- **验收**：
  - 能对同一会话切换 prompt profile 并观察差异。

### 8) 历史污染控制
- **问题**：tool 原始输出容易淹没有效上下文。
- **改造**：
  - `recent_turns` 中大 tool 输出只保留摘要或指针。
  - 关键 observation 保留，低价值日志裁剪。
- **验收**：
  - 大输出场景下模型响应稳定，无明显“丢问题主线”。

---

## P1：权限与冲突预判体系

### 9) 统一策略层（ask/deny 语义收敛）
- **现状确认**：
  - 已有 `permissions.json` + `blocked/needs_approval/auto_approved`。
  - 但策略分散在 capability/shell/python/git/github 各块，不是统一规则引擎。
- **改造**：
  - 增加统一 policy 层（工具级 + capability 级 + path 级）。
  - 规则优先级固定：`deny > ask > allow`。
- **验收**：
  - 一条规则可统一控制同类工具，行为可预测。

### 10) 输出冲突判定增强
- **当前原则**：冲突依据聚焦 `out:*`（保留）。
- **增强方向**：
  - 增加目录级冲突（如同目录固定产物模式）。
  - 增加临时产物冲突模式（`tmp/*.json`）。
- **验收**：
  - 冲突提示更准确，误报率可控。

### 11) 冲突决策 UX 增强
- **改造**：
  - `/override` `/cancel` 提示中明确“将覆盖路径 + 先占用 session_ref”。
  - 决策后立即清理冲突高亮。
- **验收**：
  - 用户能在一次提示内完成判断，不需回查日志。

---

## P1：会话语义与调度模型

### 12) 单 session 单 agent 不变式监控
- **改造**：
  - 增加断言与监控指标（session->agent 映射唯一性）。
- **验收**：
  - 压测下不出现“同 session 多 agent”。

### 13) `turn_done` 语义彻底收敛
- **改造**：
  - 持续清理 `final` 兼容逻辑（分阶段移除）。
  - 测试桩与文档统一为 `turn_done`。
- **验收**：
  - 代码、测试、文档无 `final` 主路径依赖。

### 14) session worker 健康探针
- **改造**：
  - 增加 worker 心跳、队列长度、阻塞时长指标。
- **验收**：
  - “thinking 卡住”可通过指标快速定位。

---

## P2：可观测性、测试与 TUI 打磨

### 15) 结构化调试事件
- **改造**：
  - 将 `compact_notes/memory_summary/recent_turns` 转为结构化 debug 事件输出。
- **验收**：
  - 可按字段检索，支持自动化分析。

### 16) 关键链路 trace_id
- **改造**：
  - preflight -> approval -> run -> turn_done 全链路挂同一 trace_id。
- **验收**：
  - 一次 prompt 的全链路日志可一键串联。

### 17) 回归测试场景库
- **改造**：
  - 固化高频问题场景：冲突、审批、暂停恢复、连续多轮上下文。
- **验收**：
  - 回归套件可稳定复现历史问题并防回退。

### 18) 命令补全与冲突态 UI 统一状态机
- **改造**：
  - `/switch` `/override` `/cancel` 候选规则统一抽象。
  - 冲突高亮生命周期由状态机统一管理。
- **验收**：
  - 新增命令无需复制粘贴逻辑，UI 行为一致。

---

## 建议执行顺序（两周）

### 第 1 周（稳定性）
1. P0 的 IM 接入最小闭环（TODO-00 ~ TODO-00C）
2. P0 的稳定性（TODO-01 ~ TODO-04）
3. P1 的 5（触发策略基础版）

### 第 2 周（体验与治理）
1. P1 的 6~8（compact + prompt）
2. P1 的 9~11（权限与冲突）
3. P2 的 15~17（可观测 + 回归）

---

## 当前状态备注

- 已完成：
  - `CompactionEngine` 拆分为 `should_compact()/compact()`
  - `SessionStateStore` JSONL 持久化实现
  - Prompt 分层最小策略接入
- 待推进：
  - 原子写与并发锁
  - 统一策略层（ask/deny）抽象
  - 可观测与回归体系

---

## 可执行 TODO（按文件粒度）

> 说明：以下任务按建议执行顺序排列；每项都可单独提交 commit。

### TODO-00（P0）Telegram IM 网关最小接入
- **目标文件**：`main.py`（启动参数/入口）、`scheduler.py`（提交接口复用）、`docs/*`（命令说明）
- **改动点**：
  - 新增 Telegram polling/webhook 入口（先 polling，降低复杂度）。
  - 复用现有 `create_session_prompt/continue_session_prompt` 作为核心执行链路。
  - 加入基础命令：`/new`、`/prompt`、`/switch`、`/list`（IM 侧最小集合）。
- **验收**：
  - Telegram 私聊可创建 session、继续 prompt、查看列表。

### TODO-00A（P0）IM 会话路由键设计（chat_id + session_ref）
- **目标文件**：`scheduler.py`、`main.py`（或新增 `im_gateway.py`）
- **改动点**：
  - 建立 `im_chat_session_map`（chat_id -> active_session_ref）。
  - session 创建时返回可读短 ref，并支持按 ref 显式切换。
  - 支持同 chat 下多 session 并存，不互相覆盖。
- **验收**：
  - 同一 chat 并发 3+ session 时，路由不串线。

### TODO-00B（P0）IM 可读性协议（多 Session 输出）
- **目标文件**：`scheduler.py`、`tui_app.py`（如复用格式）、`docs/*`
- **改动点**：
  - 所有 IM 输出统一前缀：`[S:<session_ref>][P:<prompt_ref>][status] ...`。
  - 长输出分段并保留前缀；冲突/审批消息必须包含 `session_ref`。
  - `/list` 在 IM 里输出精简卡片：`session_ref + session_seed_content + latest_status`。
- **验收**：
  - 多 session 同时回流时，用户可肉眼区分来源。

### TODO-00C（P0）IM 多 Session 命令体验
- **目标文件**：`main.py`（命令解析抽象）、`docs/*`
- **改动点**：
  - 明确 IM 命令语法：`/new <text>`、`/switch <session_ref>`、`/prompt <text>`。
  - 默认行为：无显式 `/switch` 时，作用于当前 chat 的 active session。
  - 在歧义场景提示可选 `session_ref`，而不是静默失败。
- **验收**：
  - 新用户可在 1 分钟内掌握多 session 操作。

### TODO-01（P0）SessionStateStore 原子写
- **目标文件**：`session_runtime.py`
- **改动点**：
  - `JsonlSessionStateStore._flush_all()` 改为：写 `session_runtime_state.jsonl.tmp` -> `fsync` -> 原子替换。
  - 写失败保留旧文件并抛可观测错误。
- **验收**：
  - 注入异常后重启仍能读取旧状态。

### TODO-02（P0）SessionStateStore 并发锁
- **目标文件**：`session_runtime.py`
- **改动点**：
  - 为 `save/merge_turn_delta/save_checkpoint/clear_checkpoint` 增加串行写保护。
  - 若采用 asyncio 锁，明确 sync/async 边界（必要时改为线程锁）。
- **验收**：
  - 多 session 并发写，JSONL 无覆盖丢失。

### TODO-03（P0）状态 schema/version 与迁移
- **目标文件**：`session_runtime.py`
- **改动点**：
  - `SessionRuntimeState.to_state()` 增加 `schema_version`。
  - `_ensure_loaded()` 增加版本分支与缺省字段补全。
- **验收**：
  - 老状态文件可自动迁移并继续运行。

### TODO-04（P0）checkpoint 分层与恢复日志
- **目标文件**：`session_runtime.py`、`scheduler.py`、`agent_instance.py`
- **改动点**：
  - 区分 turn/session checkpoint 结构。
  - 恢复时输出来源标识（turn/session）。
- **验收**：
  - debug 日志可直接看出恢复链路来源。

### TODO-05（P1）Compaction 多维触发策略
- **目标文件**：`session_runtime.py`、`memory.py`
- **改动点**：
  - `should_compact()` 增加字符量/token 估算等维度。
  - 阈值参数化并给默认值。
- **验收**：
  - 长文本/多工具两类场景触发合理。

### TODO-06（P1）Compact 结构化摘要模板
- **目标文件**：`session_runtime.py`
- **改动点**：
  - `compact()` 产物从“拼接字符串”升级为结构化摘要文本块。
  - 控制摘要长度与条目上限。
- **验收**：
  - `compact_notes` 在多轮场景中可读且稳定。

### TODO-07（P1）Prompt 分层开关化
- **目标文件**：`session_runtime.py`、`scheduler.py`
- **改动点**：
  - `DefaultPromptAssembler` 支持开关项：runtime/memory/compact/recent_turns。
  - scheduler 支持 session 级 profile 覆盖。
- **验收**：
  - 同 session 可切换 profile 并观察行为差异。

### TODO-08（P1）历史污染控制（tool 大输出裁剪）
- **目标文件**：`agent_instance.py`、`memory.py`
- **改动点**：
  - 大 observation 入上下文前先摘要化。
  - `recent_turns` 保留关键事实而非原始大段输出。
- **验收**：
  - 大输出后回答质量不显著下降。

### TODO-09（P1）统一策略层（ask/deny）抽象
- **目标文件**：`permissions.py`（新增策略层结构）、`tools.py`（接入）
- **改动点**：
  - 抽象统一规则：`deny > ask > allow`。
  - 兼容现有 `PermissionsConfig` 并逐步迁移。
- **验收**：
  - 一个规则可控制多类工具行为且优先级稳定。

### TODO-10（P1）输出冲突判定增强
- **目标文件**：`scheduler.py`、`preflight_intent.py`
- **改动点**：
  - 在 `out:*` 基础上增加目录级/临时产物冲突辅助判定。
  - 保持“文件写冲突优先”原则不变。
- **验收**：
  - 冲突提示更准确，误报可控。

### TODO-11（P1）冲突决策 UX 强化
- **目标文件**：`tui_app.py`、`main.py`
- **改动点**：
  - `/override` `/cancel` 提示中展示覆盖目标与前置 session_ref。
  - 决策后冲突高亮立即清除（统一状态机触发）。
- **验收**：
  - 用户在一次交互内可完成决策。

### TODO-12（P1）单 session 单 agent 监控
- **目标文件**：`scheduler.py`、`agent_instance.py`
- **改动点**：
  - 增加不变式断言与 debug 计数指标。
  - 发现异常时快速告警（日志）。
- **验收**：
  - 压测下无“同 session 多 agent”。

### TODO-13（P1）`turn_done` 完全收敛
- **目标文件**：`agent_instance.py`、`llm_client.py`、`tests/*`
- **改动点**：
  - 移除 `final` 兼容路径（阶段性开关后再彻底删除）。
  - 统一文档与测试样例。
- **验收**：
  - 全仓只保留 `turn_done` 主语义。

### TODO-14（P1）session worker 健康探针
- **目标文件**：`scheduler.py`
- **改动点**：
  - 记录队列长度、等待时长、worker 心跳。
  - 长时阻塞日志化。
- **验收**：
  - “thinking 卡住”可通过指标快速定位。

### TODO-15（P2）结构化调试事件与 trace_id
- **目标文件**：`scheduler.py`、`agent_instance.py`
- **改动点**：
  - debug 改为结构化 JSON 字段输出。
  - preflight->approval->run->turn_done 贯穿 trace_id。
- **验收**：
  - 单 prompt 全链路可检索串联。

### TODO-16（P2）回归测试场景库补全
- **目标文件**：`tests/test_session_runtime.py`、`tests/test_session_context_continuity.py`、`tests/test_scheduler_output_conflicts.py`、`tests/test_approval_pause_flow.py`
- **改动点**：
  - 为历史高频问题建立固定回归样例。
- **验收**：
  - 历史 bug 均有对应自动化回归测试。

### TODO-17（P2）命令补全与冲突态状态机统一
- **目标文件**：`tui_app.py`、`main.py`
- **改动点**：
  - 命令候选生成逻辑抽象复用。
  - 冲突高亮生命周期由单一状态机控制。
- **验收**：
  - 新命令接入成本下降，UI 行为一致。
