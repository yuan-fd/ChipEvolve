# 当前任务：P1 通用核心契约与 Workflow Runtime

status: in_progress
phase: P1
approved_at: 2026-08-04
started_at: 2026-08-04
base_commit: afdca1ef16f419843ef21009c7c4ff47274ee43b

## 1. 用户目标

P0 完成后，如无重大问题自动进入 P1。实现版本化公共契约、Plugin Registry、通用 Workflow Runtime、Run/StageRun/ExecutionAttempt 持久化、Artifact/Event 登记和基础查询，为 P2 ORFS 插件迁移提供稳定内核。

## 2. 允许修改范围

- `packages/contracts/**`
- `packages/scheduler/**`
- `packages/execution/**`
- `integrations/**`
- `apps/api/**`（仅必要查询边界）
- `tests/**`
- `docs/**`、`project_state.md`、`tasks/**`、`memory_snapshots/**`、`project_kb/**`

## 3. 禁止范围

- 不修改或迁移真实 `var/platform.db`，测试一律使用临时 DB。
- 不修改 `var/**`、共享 ORFS/OpenROAD/PDK、`.external-src/**`。
- 不接入三个真实插件，不运行真实 EDA/LLM。
- 不引入 FastAPI、PostgreSQL、Redis、Celery、Kubernetes 或网络微服务。
- 不重做 Web UI、RAG、BO、ReAct、Coding/Evolve Agent。
- 不 push、不部署。

## 4. 实现步骤

1. v1 TaskSpec、PluginManifest、PluginResult、ActionProposal、Event 及状态枚举。
2. Registry/manifest loader，拒绝未知 schema、架构和能力。
3. v1 SQLite schema 与显式事务状态机。
4. StageRun/Attempt、lease、heartbeat、cancel、timeout、retry/lost recovery。
5. 受控子进程 adapter envelope、echo fixture 和 artifact path 校验。
6. Artifact/Event 最小登记和 Python 查询边界。
7. legacy jobs 非破坏性迁移设计与测试。

## 5. 验收标准

- 公共 envelope 带 `schema_version=1`，未知主版本拒绝。
- Runtime 独占终态写入，非法转换不产生部分状态。
- retry 创建新 Attempt；旧 Attempt/Event 不变。
- lease 过期产生 lost Attempt，并按有界 policy 恢复或失败。
- cancel/timeout 形成结构化 Failure/Event。
- adapter 使用受控 cwd/环境；越界 artifact 被拒绝。
- echo 全链完成 TaskSpec→Run→StageRun→Attempt→Artifact/Event→terminal。
- 原 22 项和 P1 新测试全部通过。
- `var/`、共享工具链和外部源码没有修改。

## 6. 已批准决策

- ADR-0001：Workflow Runtime 是唯一执行与持久状态权威。
- ADR-0002：首版插件采用独立环境和版本化子进程协议。
- SQLite 为单机开发基线；P1 不提前分布式化。

## 7. 停止条件

若必须改变上述 ADR、核心 ID/Attempt 不可变语义、安全边界或项目外共享工具链，暂停汇报。普通实现问题在白名单内自动绕开；同一根因最多三次尝试。
