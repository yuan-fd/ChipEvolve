# ADR-0001：Workflow Runtime 是唯一执行与状态权威

- status: Accepted
- accepted_by: user
- accepted_at: 2026-08-04

## 背景

架构草图包含 Flow Agent、Batch Exploration、AgenticPD、Doomed/GWTW、Evolve 和 Coding Agent。多个组件都可能在语义上“调度”实验；若它们各自启动进程和写最终状态，将产生重复执行、取消竞争和证据覆盖。

## 决策

Flow Agent 和各优化 Agent 保留智能调度职责：生成实验计划、参数、分支、预算和继续/暂停/终止建议。Workflow Runtime 独占以下权限：进程启动与终止、资源租约、超时、重试、恢复、Attempt/StageRun/Run 状态事务和最终成功判定。

Agent 输出版本化 ActionProposal/ExperimentPlan，经 Runtime 策略校验后才可执行。插件内部状态作为证据保存，不覆盖平台状态。

## 影响

- 智能策略可替换且不会破坏执行事实。
- AgenticPD/TaiWei 黑箱复现需要适配状态映射。
- Runtime 必须实现 lease、attempt 和幂等恢复。
- Web/LLM 不得直接写 terminal 状态。

## 被否决方案

1. Flow Agent 直接管理 subprocess 和数据库：智能逻辑与故障恢复耦合。
2. 每个插件自带 scheduler 并与平台并列：取消、重试和状态主键冲突。
3. 只使用 shell 脚本串联：缺少可恢复状态与结构化证据。
