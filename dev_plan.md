# Paragents 开发计划（权限模型优先版）

## 设计基线

- 主体采纳 `mercury-agent`：注册期 capability 开关 + 运行期审批流 + 文件 scope（临时/永久）
- 补充采纳 `nanobot`：
  - 主 agent 与 subagent 分层工具集（默认最小权限）
  - MCP server 级白名单与命名空间隔离
- 始终保证：多任务并发、任务隔离、watch/hide/show 观测能力不退化

## 权限架构目标（跨阶段）

- 注册期权限：工具是否注入由配置和上下文决定
- 运行期权限：每次工具调用经过统一 `PermissionManager` 判定
- 审批回路：`needs_approval` -> 用户确认 -> 临时或持久授权
- 分层权限：主 agent > subagent（严格子集）
- MCP 权限：仅主 agent 可见；每个 server 有 `enabled_tools` 白名单

## P0（权限骨架与可读工具）

### 目标

先落地“可控但可用”的最小权限框架，覆盖高频只读场景。

### 计划内容

- 实现 `PermissionManager`（mercury 风格）
  - capability 开关：`filesystem/shell/git/github/web/mcp`
  - 文件 scope：`path + mode(read/write)`，支持临时/持久
  - shell 策略：`blocked/auto_approved/needs_approval`
- 落地权限配置文件 `permissions.json`
  - 运行时加载 + 变更后热生效（新任务生效）
- 工具侧接入统一校验（先覆盖只读工具）
  - `read_file`, `list_dir`, `glob`, `grep`
- CLI 审批命令最小集
  - `approve scope ... [once|always]`
  - `approve command ... [once|always]`
  - `deny <request_id>`

### 验收标准

- 未授权路径读操作被拦截并给出可审批提示
- shell 命中 `needs_approval` 时不执行，等待用户确认
- 只读任务闭环稳定：定位 -> 读取 -> 总结

## P1（主/子 agent 分权与命令执行）

### 目标

引入 nanobot 风格主/子 agent 分层权限，并补齐代码修复最小链路。

### 计划内容

- 定义两套工具 profile
  - `main_agent_tools`：可包含 `message/spawn/schedule`（按开关）
  - `subagent_tools`：仅文件/搜索/可选 `exec`，禁止 `spawn/message/mcp`
- `spawn` 机制权限化
  - 子任务创建时绑定 `subagent` profile
  - 继承但不可扩大权限（no privilege escalation）
- `exec`（或 `run_command`）接入审批流
  - 高风险命令进入 `needs_approval`
  - 低风险命令可 auto-approved
- 文件写工具接入 scope 权限
  - `write_file`, `edit_file`, `create_file`, `delete_file`

### 验收标准

- subagent 无法调用主 agent 专属工具
- `exec` 审批策略对主/子 agent 一致生效
- 修复链路可用：检索 -> 编辑 -> 校验命令

## P2（MCP 权限模型与外部能力）

### 目标

接入 MCP 同时不破坏隔离与可控性。

### 计划内容

- MCP 动态注册（nanobot 风格）
  - 工具命名空间：`mcp_<server>_<tool>`
  - 资源/提示词同前缀隔离
- server 级白名单
  - `enabled_tools: ["*"] | [] | ["toolA", ...]`
  - 连接时过滤注册，调用时二次校验
- MCP 与 agent 分层绑定
  - 仅主 agent 默认可见
  - subagent 默认不可见（可配置开启，但默认关闭）
- web 工具权限并入统一模型
  - `web_fetch`, `web_search` 受 capability 开关与网络策略约束

### 验收标准

- 不在白名单的 MCP 工具无法注册/调用
- MCP 工具名不会与本地工具冲突
- 主子 agent 的 MCP 可见性符合配置

## P3（高风险工程能力与策略统一）

### 目标

补齐 git/github/skill/scheduler 工具，并统一高风险审批策略。

### 计划内容

- Git 工具接入统一审批中间层
  - `git_status/git_diff/git_log` 视为低风险
  - `git_add/git_commit/git_push` 进入写操作策略
- GitHub 工具接入统一审批中间层
  - GET 与写操作分级，写操作默认 `needs_approval`
- Skill 临时提权（mercury 风格）
  - 提权范围受 `allowed-tools` 和 capability 开关共同约束
  - 生命周期仅当前任务有效，完成后回收
- Scheduler 内部任务策略
  - 默认普通权限
  - 可配置 internal task auto-approve（默认关闭）

### 验收标准

- git/github 写操作全量经过统一审批策略
- skill 提权可审计、可回收、不可越级
- 定时任务不会绕过权限模型

## 实施顺序（文件级）

1. `Paragents/config.py`：权限配置结构、profile、MCP 白名单配置
2. `Paragents/`（新增）`permissions.py`：`PermissionManager` 与审批状态机
3. `Paragents/tools.py`：每个 tool 接入统一权限检查
4. `Paragents/agent_instance.py`：主/子 agent 工具 profile 绑定
5. `Paragents/scheduler.py`：审批事件、权限事件、审计日志
6. `Paragents/main.py`：审批与权限管理 CLI 命令
7. `Paragents/`（新增）`mcp_client.py`：MCP 连接与动态工具注册

## 里程碑

- M1（P0）：权限骨架与只读工具闭环
- M2（P1）：主/子 agent 分权与 exec/写文件审批
- M3（P2）：MCP 白名单与外部工具权限化
- M4（P3）：git/github/skill/scheduler 统一策略收口
