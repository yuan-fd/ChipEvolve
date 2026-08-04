# P1 任务：通用核心契约与 Workflow Runtime

status: completed
depends_on: P0 accepted
implementation_commit: e750370d0a95c708cb5f9a0ee297dcb0de609db6

## 目标

在不破坏现有 ORFS demo 和历史 `var/` 数据的前提下，实现版本化公共契约、Plugin Registry、通用 Workflow Runtime、Run/StageRun/ExecutionAttempt 持久化、Artifact/Event 登记和基础查询，为 P2 ORFS 插件迁移提供稳定内核。

## 范围

- `packages/contracts/**`：TaskSpec、PluginManifest、PluginResult、ActionProposal、Event 及状态枚举。
- `packages/scheduler/**`：v1 schema、事务状态机、lease/heartbeat、cancel、timeout、retry、lost recovery。
- `packages/execution/**`：通用 adapter process envelope；复用 ProcessGuardian。
- `integrations/**`：manifest schema、示例 echo/test adapter、conformance fixtures。
- `apps/api/**`：只增加必要的 Run/StageRun/Attempt 查询边界，不重做 Web UI。
- `tests/**`：单元、状态机、进程、恢复、迁移和 contract tests。
- `docs/**`、`project_state.md`、`tasks/**`、`memory_snapshots/**`：同步实现事实。

## 非目标

- 不把现有 ORFSRunner 完整迁入新插件协议；该工作属于 P2。
- 不接入 RTLScout、AgenticPD、TaiWei。
- 不运行真实 EDA/LLM。
- 不引入 FastAPI、PostgreSQL、Redis、Celery、Kubernetes 或网络微服务。
- 不修改现有 `var/platform.db`；测试使用临时 DB，新 schema 使用独立开发 DB/显式初始化。
- 不重做 Web UI、RAG、BO、ReAct、Coding/Evolve Agent。

## 实现顺序

1. 增加 v1 contracts 与严格 round-trip/validation tests。
2. 定义 Registry 和 manifest loader，拒绝未知 schema/架构/能力。
3. 建立 v1 SQLite schema 和显式状态转换。
4. 实现 StageRun/Attempt、lease、heartbeat、cancel、timeout、retry/lost recovery。
5. 实现通用 subprocess adapter envelope 和 echo fixture。
6. 接入 Artifact/Event 最小登记及查询。
7. 增加 legacy jobs 只读映射/迁移设计测试，不触碰真实 DB。
8. 审查文档、diff、范围和全部测试。

## 验收标准

1. 所有公共 envelope 带 `schema_version=1`，未知版本拒绝。
2. Runtime 是唯一终态写入者；非法状态转换失败且不产生部分写入。
3. retry 创建新 Attempt；旧 Attempt 和事件保持不变。
4. worker lease 过期后旧 Attempt 记为 lost，并按 policy 有界恢复。
5. cancel/timeout 终止进程组并生成结构化 Failure/Event。
6. Adapter 使用受控 cwd/环境，只有 workspace 内声明产物可登记；绝对路径、越界路径和未声明种类被拒绝。P1 不把普通子进程宣称为恶意代码 OS 沙箱。
7. echo/test adapter 完成一次全链：TaskSpec → Run → StageRun → Attempt → Artifact/Event → terminal status。
8. 原 22 项测试和 P1 新测试全部通过。
9. `var/`、共享工具链和第三方源码没有修改。

## 权限与停止条件

- 用户已批准 P1 在 P0 无重大问题后自动开始。
- 允许项目内代码、测试和文档修改；允许本地 commit，不 push。
- 若必须改变 ADR-0001/0002、核心 ID/Attempt 不可变语义或安全边界，暂停汇报。
- 同一根因最多三次尝试，第三次失败记录证据并标记阻塞。

## 完成证据

- `python3 -m pytest -q`：49 passed。
- contract/registry/runtime/adapter/legacy projection 均有自动测试。
- echo TaskSpec→Run→StageRun→Attempt→Artifact/Metric/Event→terminal 全链通过。
- timeout/cancel 终止进程组并保留结构化 Failure/Event。
- `var/`、共享工具链和固定第三方源码未修改。
