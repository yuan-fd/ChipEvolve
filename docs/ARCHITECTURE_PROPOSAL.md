# 目标架构

status: accepted baseline
approved_at: 2026-08-04

## 1. 总体拓扑

```text
Web / CLI / Natural Language
             │
      Versioned TaskSpec
             │
      Workflow Runtime
  (唯一进程与持久状态权威)
       ├─ Plugin Registry
       ├─ Resource / Permission Policy
       ├─ Event / Hook dispatcher
       ├─ Campaign / child-run manager
       └─ Adapter process protocol
              ├─ ORFS Adapter
              ├─ RTLScout Adapter
              ├─ AgenticPD Policy Adapter
              └─ TaiWei-Pin-3D Adapter
             │
   Metadata DB + Artifact Store
             │
       Query API / Web Views
```

## 2. Agent 与 Runtime 的双层调度语义

架构草图中的 Flow Agent 保持智能调度器定位：读取规格、报告和历史，选择参数、分支、实验、预算与下一步；AgenticPD 的 Judge/StageAgent/Doomed/GWTW 也保留内部策略含义。

Runtime 是工程执行调度器：校验 proposal、分配资源、启动/取消进程、实施超时和重试、写入 Run/StageRun/Attempt 状态、登记原始证据。两层关系是 proposal/approval，不是两个组件竞争同一状态主键。

## 3. 控制面

Workflow Runtime 必须提供：

- TaskSpec 校验和 WorkflowDefinition 展开；
- Plugin Registry 解析和能力/架构匹配；
- 状态转换、lease、heartbeat、cancel、timeout、retry、recovery；
- 单机有限并发、资源预算和工作区隔离；
- append-only Event；
- Artifact/Metric/Failure/ToolchainSnapshot 登记；
- Campaign 与 child run 关系；
- proposal 风险和权限策略。

Runtime 不解析特定插件私有业务文件；解析由插件 Adapter 返回版本化结果，Runtime 再验证公共 envelope。

## 4. 执行面

首版统一采用独立 Python/Conda 环境和子进程 JSON 协议。每个 Attempt：

1. Runtime 创建独立 workspace 和 attempt envelope；
2. 受控环境启动 adapter；
3. TaskSpec 或文件引用通过 stdin/任务文件传入；
4. adapter 在 stdout/结果文件返回 PluginResult；
5. stdout 只允许协议消息，原始工具输出进入独立日志；
6. Runtime 校验状态、退出码、产物 allowlist、路径边界和哈希；
7. Runtime 在单个事务中完成 Attempt 和 StageRun 状态变更。

插件不得依赖隐式 cwd，环境变量使用 allowlist；不得继承宿主完整 PATH/LD_LIBRARY_PATH。

## 5. 事实面

- SQLite 是 P1/P2 单机基线，Schema 保留迁移到 PostgreSQL 的边界。
- Artifact Store 首版是项目内受控目录，DB 保存相对路径、大小、哈希、类型、来源 Attempt 和保留策略。
- Metric 必须保存 unit、source_artifact、parser_id/version 和上下文。
- Event append-only；状态表是 Runtime 事务更新的当前投影。
- 原始日志和产物不可由 AI 重写；AI 只生成摘要和引用。

## 6. 插件边界

### ORFS

一个结构化 EDA Stage/Workflow 执行器。现有 runner 迁移后成为首个标准插件，不承担长期优化策略。

### RTLScout

黑箱 RTL 生成/验证/优化插件。内部 ReAct、Verilator、Yosys/CEC 与成本评估保留；输出 RTL 和验证证据，可由组合 Workflow 传给 ORFS。

### AgenticPD

智能 Flow 优化器。黑箱入口用于复现；生产集成以 ExperimentPlan/ActionProposal 驱动 Runtime child run。Trial/tree/checkpoint/decision trace 被登记为插件证据，不覆盖平台事实。

### TaiWei-Pin-3D

首版是独立 ToolchainProfile 下的黑箱 3D workflow。内部阶段映射为子阶段 Event/Artifact；外层 StageRun 表示一次完整插件调用。

## 7. Hook 与事件

Hook 必须声明顺序、超时、失败策略、幂等键和读写范围。P1 只实现必要生命周期事件，不建设任意动态 hook：

- `run.accepted`
- `stage.ready`
- `attempt.started`
- `attempt.heartbeat`
- `attempt.finished`
- `artifact.registered`
- `metric.recorded`
- `run.finished`

## 8. 演进顺序

Runtime/Schema → ORFS 真实闭环 → 外部源码兼容性 → RTLScout → AgenticPD → Campaign/Web → NL/ReAct → TaiWei → Knowledge/RAG → Coding/Evolve。

任何改变唯一调度权威、核心实体主键、Attempt 不可变语义、插件协议或安全边界的修改必须新增 ADR。
