# OpenROAD 自演化平台项目章程

status: accepted
approved_by: user
approved_at: 2026-08-04

## 使命

在固定、可追溯的 OpenROAD/ORFS 与 PDK 基线上，建设一个面向接口、插件化、可恢复、可复现的 EDA 实验与智能优化平台。平台逐步支持自然语言任务、RTL 生成与验证、2D/3D RTL→GDSII、Flow 优化、Agent/BO 实验、证据知识积累和受控白盒演化。

## 核心原则

1. 架构草图定义产品和 Agent 协作蓝图；外部项目真实源码定义各插件内部行为。
2. Flow Agent/AgenticPD 负责智能意义上的实验规划和调度；Workflow Runtime 是进程、资源和持久状态的唯一权威。
3. 任何成功结论必须有真实命令、退出码、原始产物、哈希、指标来源和数据库状态。
4. LLM 文本、mock、dry-run、命令生成和派生可视化不构成真实 EDA 成功。
5. 外部插件使用独立环境和版本化协议；核心不 import 插件私有依赖。
6. 数据库保存关系、状态和摘要，大文件进入 Artifact Store。
7. 重试创建新 ExecutionAttempt，禁止覆盖失败证据。
8. 共享工具链和原始证据只读；自演化候选在隔离 worktree/环境验证。

## 首期范围

- 通用契约、Plugin Registry、Workflow Runtime 和可恢复状态模型。
- 现有 OpenROAD/ORFS 执行能力迁移和真实 2D 闭环。
- RTLScout、AgenticPD、TaiWei-Pin-3D 三个平台适配。
- Campaign/批量探索、有限并发、查询 API 和基础 Web。
- 自然语言生成受校验 TaskSpec，白名单式有限 ReAct。
- 带版本和证据的知识复用。

## 非目标

- 首期不上 Kubernetes、分布式微服务或多租户生产部署。
- 不重写 RTLScout、AgenticPD 或 TaiWei 的内部算法。
- 不让 Agent 绕过 Runtime 直接改最终状态或执行任意 shell。
- 不在没有基线、预算和对照实验时声称算法提升。
- 不在许可证不明确时复制或分发第三方源码。
- 不在 P1 提前实现 RAG、Coding Agent、Evolve Agent 或华丽 Web。

## 成功定义

每个阶段必须同时满足范围合规、自动化测试、必要真实验证、证据完整、文档一致、diff 审查和限制记录。阶段状态只能是完成、部分完成或阻塞，不使用“理论可用”替代证据。
