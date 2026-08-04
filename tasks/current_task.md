# 当前任务：P2 ORFS 标准插件迁移与真实 RTL→GDS 验收

status: in_progress
phase: P2
approved_at: 2026-08-04
started_at: 2026-08-04
base_commit: 5e65fffb300b12f4dffefc046ccffeef8b396e1b

## 1. 用户目标

完成 P2：将现有 ORFSRunner 非破坏性迁移为 v1 标准插件，并在新 Runtime 下完成真实 Nangate45 RTL→GDS 硬验收。

## 2. 执行规范

完整范围、步骤、验收标准和停止条件见 `tasks/phase-2.md`，该文件是本轮执行权威。

## 3. 关键不变量

- WorkflowRuntime 继续独占 v1 状态终态。
- 旧 ORFSRunner/JobStore/API 保持兼容；真实 legacy DB 只读保护。
- RTL、工具链、平台配置和生成配置必须有可复核哈希。
- 共享 ORFS/OpenROAD/Yosys/PDK 只读；既有 dirty 状态前后必须一致。
- 不 push、不部署、不运行 LLM。

## 4. 当前工具链事实

- ORFS commit：`51ad1231a231ee85234c06db807688d029b85c35`。
- OpenROAD：`26Q1-1961-g63ed2e0fe5`；Yosys：`0.63`。
- 主机：aarch64 / Python 3.9.9；Nangate45 platform 可见。
- 共享 ORFS 在 P2 开始前已有 dirty/untracked 状态，必须只读保持，不做清理。

## 5. 恢复锚点

- 基线 commit：`5e65fffb300b12f4dffefc046ccffeef8b396e1b`。
- P1 快照：`memory_snapshots/P1-runtime-core-2026-08-04.md`。
- P2 任务：`tasks/phase-2.md`。
