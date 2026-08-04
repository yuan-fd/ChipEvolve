# P5 任务：AgenticPD 黑箱优化提案

status: completed
phase: P5
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 0892d57

## 白名单

- `packages/contracts/`：ExperimentPlan/Candidate 版本化契约。
- `packages/execution/`、`integrations/agenticpd/`：固定源码黑箱 Adapter。
- `scripts/run_p5_acceptance.py`、`tests/test_agenticpd_plugin.py`。
- P5 任务、证据、进度、知识和只追加 memory snapshot。
- 生成物：`runs/p5-acceptance-*/`；live SQLite：`/tmp/openroad-platform-p5-runtime/`。

## 禁止范围

- 不修改、复制或再分发无 LICENSE 的 AgenticPD 上游源码。
- AgenticPD 不创建或管理 ORFS child process，不写 Runtime 终态。
- mock QoR 不得称为真实 QoR；未消费参数不得称为已生效。
- 不读取或持久化模型凭据，不修改共享 ORFS/OpenROAD/Yosys/PDK。

## 验收门

- 固定 commit 上游 mock 生成 proposal，转换为严格 ExperimentPlan。
- 同一 RTL、PDK、工具链、时钟和布局密度下执行 38% 基线与 35% 候选。
- 两个 child run 均由 Runtime 执行到真实 GDS，参数在生成 config 中可核验。
- QoR 只来自各自 ORFS `analysis/report.json`；保存失败尝试和 SQLite 快照。
- 全量 pytest、上游 clean、diff 和越界审计通过。

## 资源与停止条件

- child run 预算 2、并发 1、单 run 超时 7200 秒、同根因最多 3 次。
- 真实 LLM 仅在显式注入凭据和预算后运行；否则记录外部阻塞。
