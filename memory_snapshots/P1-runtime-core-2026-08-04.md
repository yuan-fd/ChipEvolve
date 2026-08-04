# P1 Runtime Core 里程碑快照

snapshot_type: milestone
created_at: 2026-08-04
append_only: true

## 可恢复锚点

- P0 架构基线：`afdca1ef16f419843ef21009c7c4ff47274ee43b`。
- P1 实现：`e750370d0a95c708cb5f9a0ee297dcb0de609db6`。
- 验证：49 tests passed；JSON、compile、diff、secret 和保护范围检查通过。
- 入口：`TaskSpec` → `PluginRegistry` → `WorkflowRuntime` → `RuntimeStore`/`ProcessAdapter`。
- 查询：`WorkflowRuntime.describe(run_id)` 返回 Run/StageRun/Attempt/Artifact/Metric/Event。

## 已固定不变量

- Workflow Runtime 是进程生命周期和持久终态的唯一权威。
- `schema_version=1` 必须显式出现；未知公共 envelope/DB 版本拒绝。
- retry 创建新 Attempt，旧 Attempt 与 append-only Event 不覆盖。
- queued cancel 不创建 Attempt；live cancel 先 durable request，再由 guardian 终止进程组并确认。
- timeout/cancel/lost 保留结构化失败证据；artifact 只能从 workspace 内登记并校验 SHA-256。
- legacy `jobs` source 只读；所有缺失 provenance 保持 `unknown`。

## 安全与后续门槛

- P1 子进程协议适用于固定、审查过的 adapter，不是恶意代码 OS 沙箱。
- P2 首次真实 ORFS 接入前需确认运行预算、ToolchainSnapshot/配置哈希和共享工具链只读边界。
- AgenticPD 无 LICENSE、RTLScout ARM/Python 门槛、TaiWei 独立工具链约束仍然有效。
- 未经新阶段授权，不进入 P2、不运行真实 EDA、不修改 `var/` 或共享 ORFS/OpenROAD/PDK。
