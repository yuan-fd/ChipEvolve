# 平台数据模型

status: P1 implementation baseline
schema target: v1

## 1. 公共契约

所有 JSON envelope 必须包含整数 `schema_version`；未知主版本必须拒绝。

### TaskSpec

最低字段：

- `schema_version`
- `task_id`
- `project_id`、`design_id`
- `workflow_id` 或 `plugin_id`
- `inputs`：只包含值或受控 Artifact 引用
- `parameters`
- `resources`
- `timeout_policy`
- `retry_policy`
- `expected_artifacts`
- `labels`

### PluginManifest

- `schema_version`
- `plugin_id`、`plugin_version`
- `adapter_entry`
- `environment`
- `capabilities`
- `supported_arch`
- `input_schema`、`output_schema`
- `required_tools`
- `default_timeouts`
- `artifact_rules`

### PluginResult

- `schema_version`
- `status`
- `exit_code`
- `started_at`、`ended_at`
- `metrics`
- `artifacts`
- `failure`
- `provenance`

### ActionProposal

- `schema_version`
- `proposal_id`
- `producer`
- `target_run_id`、`target_stage_key`
- `action_type`
- `parameters`
- `evidence_refs`
- `risk`
- `budget`

### Event

- `schema_version`
- `event_id`
- `run_id`、可选 `stage_run_id`、`attempt_id`
- `event_type`
- `occurred_at`
- `producer`
- `payload`

## 2. 核心实体

### Project 与 Design

`Project` 聚合任务和访问范围。`Design` 表示逻辑设计身份；RTL、网表、约束和派生版图均通过 Artifact 与 Design 关联，不能把文件路径当作 Design 主键。

### ToolchainSnapshot

保存 toolchain id、架构、OS、ORFS/OpenROAD/Yosys/PDK 标识、关键配置哈希、受控环境摘要和创建时间。运行开始后不可修改。

### WorkflowDefinition

版本化 DAG/阶段序列，保存阶段 key、plugin capability、输入输出映射和依赖。P1 只需支持有序阶段，不提前实现通用分布式 DAG 引擎。

### Run

一次经接受的 TaskSpec 实例。字段至少包括 `run_id`、`task_id`、workflow version、当前状态、创建/开始/结束时间、parent campaign、配置快照引用和终止原因。

### StageRun

Run 中一个逻辑阶段。字段包括 `stage_run_id`、`run_id`、稳定 `stage_key`、ordinal、plugin id/version、当前状态、输入/输出映射。StageRun 可以拥有多个 Attempt。

### ExecutionAttempt

一次真实进程尝试。字段包括 `attempt_id`、`stage_run_id`、递增 `attempt_number`、workspace、worker/lease、pid、command digest、开始/结束/heartbeat、exit code、timeout/cancel 标记和 failure id。

核心不变量：

- `(stage_run_id, attempt_number)` 唯一；
- retry 只能插入新 Attempt；
- terminal Attempt 不可回到 running；
- StageRun 成功必须引用唯一成功 Attempt；
- Run 状态只能由 Runtime 根据 StageRun 投影更新。

### Artifact

字段包括 `artifact_id`、owner Attempt、Design、kind、相对 store key、size、sha256、mime、created_at、source artifact、parser version、retention。路径必须位于 Artifact Store，禁止任意绝对路径作为下载目标。

### Metric

字段包括 name、typed value、unit、stage/attempt、source artifact、parser id/version、context JSON、created_at。缺失单位或来源的指标不能进入跨实验比较。

### Event

append-only 事实流，使用全局 event id 和实体外键。Event 不作为绕过状态事务的后门；状态转换和对应事件必须同事务提交。

### AgentAction、Failure 与 RepairAttempt

`AgentAction` 保存 proposal、证据、风险、policy verdict 和是否执行。`Failure` 保存分类、摘要、根因指纹和日志引用。`RepairAttempt` 关联前一 Failure、新假设、措施和结果；同根因默认最多三次。

### KnowledgeEntry

类型限定为 `observed_fact`、`validated_rule`、`hypothesis`、`failed_attempt`。必须带工具链、设计/PDK适用范围和 evidence refs。

## 3. 状态模型

Run/StageRun 通用状态：

```text
queued → preparing → running → succeeded
                         ├── failed
                         ├── cancel_requested → cancelled
                         └── timed_out
```

`retry_wait` 是 StageRun 的调度状态，不是旧 Attempt 的状态。恢复失联 worker 时，旧 Attempt 以 `lost` 终止，再依据 retry policy 创建新 Attempt。

## 4. Campaign

Campaign 是一组 child Run 的计划与预算容器，不直接执行 EDA。字段包括 strategy plugin、baseline run、budget、seed、并发上限、停止条件和 child run 关系。Doomed/GWTW/BO 只能产生 proposal；Runtime 创建、暂停或取消 child Run。

## 5. 存储边界

- DB：实体、关系、状态、摘要、哈希和相对 store key。
- Artifact Store：RTL、GDS、DEF、ODB、报告、日志、模型输出和可视化。
- Memory/KB：经过分类的摘要和索引，不复制原始证据。

## 6. 兼容现有数据

当前 `jobs` 表保留为 legacy source。P1 必须提供新建 v1 schema 和显式 importer/migration 测试；不得原地破坏 `var/platform.db`。历史 `request_json/result_json` 可映射为 Run 和单次/多阶段 Attempt，但缺失 provenance 的字段必须标为 unknown，不得推断补齐。
