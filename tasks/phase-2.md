# P2 任务：ORFS 标准插件迁移与真实 RTL→GDS 验收

status: in_progress
approved_at: 2026-08-04
started_at: 2026-08-04
depends_on: P1 complete
base_commit: 5e65fffb300b12f4dffefc046ccffeef8b396e1b

## 目标

将现有 ORFSRunner 包装为 v1 标准插件，通过 P1 WorkflowRuntime 执行；固定输入 RTL、工具链、生成配置和平台配置 provenance，保持旧 ORFSRunner/JobStore/Web API 回归，并在新 Runtime 下完成真实 Nangate45 RTL→GDS。

## 允许范围

- `packages/contracts/**`、`packages/execution/**`、`packages/scheduler/**`
- `integrations/**`、`tests/**`
- `docs/**`、`tasks/**`、`memory_snapshots/**`、`project_state.md`
- 项目内独立 P2 临时/证据 workspace；最终证据可保存于项目内新的 P2 evidence 目录。
- 只读调用 `~/OpenROAD-flow-scripts`、`~/bin/openroad`、`~/bin/yosys`、`~/bin/klayout`。

## 禁止范围

- 不修改共享 ORFS/OpenROAD/Yosys/PDK 或其既有 dirty 状态。
- 不修改、迁移或复用真实 `var/platform.db` 作为 v1 Runtime DB。
- 不删除 `var/` 历史证据，不把 P2 结果伪装为旧 JobStore 结果。
- 不接入 RTLScout、AgenticPD、TaiWei，不运行 LLM。
- 不 push、不部署，不引入微服务或容器编排。

## 实现步骤

1. 接通 ToolchainConfig 与 ORFSRunner 的受控环境，同时保持旧构造参数兼容。
2. 定义 `orfs@1.0.0` manifest、TaskSpec builder 和显式 RTL artifact 引用。
3. Adapter 校验 source size/SHA-256、复制 RTL 到 Attempt workspace，再调用 ORFSRunner。
4. 生成 ToolchainSnapshot：ORFS commit/status、OpenROAD/Yosys 版本与二进制哈希、平台/生成配置/RTL 哈希。
5. 将 RunResult 转为 PluginResult，登记 GDS/DEF/netlist/ODB/report/log/config/snapshot 和指标。
6. 用 fake ORFS 做 contract、越界/篡改、全链和旧 API 回归。
7. 使用独立 v1 DB/workspace 做真实 Nangate45 finish 运行并核验 GDS、状态、哈希与 provenance。
8. 对比共享工具链前后指纹，更新长期记忆并本地提交。

## 验收标准

- 新 Runtime 的 TaskSpec→Plugin→Attempt→Artifact/Metric/Event→terminal 全链成功。
- 输入 RTL 在启动前做 size/SHA-256 校验并复制到 workspace；adapter 不直接实现任意 source path 运行。
- RuntimeStore 中至少登记非空且哈希匹配的 GDS、DEF、netlist、ODB、配置与 ToolchainSnapshot。
- snapshot 包含足够重放的工具版本、commit、文件哈希和参数，不保存凭据或完整宿主环境。
- retry/cancel/timeout 与旧 49 项测试不回归；旧 ORFSRunner、JobStore、Web API 继续通过。
- 真实 Nangate45 `finish` 在新 Runtime 下 `succeeded` 且 `implementation_valid=true`、`gds_complete=true`。
- `var/platform.db`、共享工具链和三个固定第三方源码无 P2 修改。

## 停止条件

若真实运行必须修改共享工具链、需要改变 P1 Runtime 权威/Attempt 语义、输入证据无法可信固定，或连续三次同根因仍无法绕开，则暂停并提交证据与方案。
