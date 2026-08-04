# 分阶段路线

status: accepted
updated_at: 2026-08-04

| 阶段 | 目标 | 真实性门槛 |
| --- | --- | --- |
| P0 | 安全基线、事实审计、版本锁定、架构/数据模型/ADR | Git 基线、22 tests、45 artifacts 校验、三仓库固定 |
| P1 | 版本化契约、Registry、Runtime、Run/StageRun/Attempt、Artifact/Event 查询 | crash/cancel/timeout/retry/recovery 集成测试 |
| P2 | 现有 ORFSRunner 迁为标准插件 | 新 Runtime 下真实 Nangate45 RTL→GDS |
| P3 | 三平台环境与源码兼容性准入 | 固定 commit 的 license/ARM/入口/smoke 结论 |
| P4 | RTLScout 黑箱插件与 RTL→ORFS 组合工作流 | fake smoke；有预算时真实 LLM，否则明确阻塞 |
| P5 | AgenticPD 智能优化接入 | 同基线、预算、工具链的真实候选对比 |
| P6 | Campaign、有限并发、恢复、查询 API/Web | 重启无重复、workspace 隔离、资源与取消证据 |
| P7 | NL→TaskSpec 与白名单 ReAct | 无任意 shell，修复预算和停止条件生效 |
| P8 | TaiWei-Pin-3D 黑箱插件 | 固定 3D toolchain 下 gcd 真实 GDS/指标/视图 |
| P9 | 证据知识库与跨实验复用 | 版本隔离、证据引用和回放测试 |
| P10 | Coding/Evolve Agent | 隔离 worktree、回归、人工/策略闸门 |

EDACraft/IC Craft 在取得源码与独立审计后作为 P11，不进入前三个平台主线。

## 阶段执行规则

每阶段开始前更新 `tasks/current_task.md`，明确白名单、验收命令、资源和停止条件。阶段结束必须更新 CURRENT_STATUS、NEXT_ACTION、project_state 和只追加 memory snapshot，并审查 diff 与越界文件。

重大 ADR 包括：调度权威、插件协议、数据主键/语义、工具链基线和安全边界。其它实现细节可在已批准阶段内自动推进。
