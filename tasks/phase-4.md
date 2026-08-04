# P4 任务：RTLScout 黑箱插件与 RTL→ORFS 组合链

status: completed
phase: P4
base_commit: 9269040
completed_at: 2026-08-04

执行权威为 `tasks/current_task.md`。停止条件：同根因三次失败、需要用户提供凭据、需要改变共享工具链或出现插件契约架构分歧。其它独立环境和依赖问题在项目边界内自动绕开。

## 验收结果

- 5 项 RTLScout/组合新增测试通过，全量回归通过。
- 固定官方 fake/simple_adder：3/3 correctness、310 transistors。
- 生成 RTL 以 SHA-256 固定后进入 ORFS，真实 Nangate45 6/6 到 GDS。
- 真实 LLM 因没有用户提供的 provider 凭据/付费预算明确标记 external blocker。
- 证据：`docs/evidence/P4_RTLSCOUT_ACCEPTANCE.{md,json}`。
