# P7 任务：NL→TaskSpec 与有限 ReAct

status: completed
phase: P7
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: 025dc72

## 白名单

- `packages/contracts/`：RepairAction 版本化契约。
- `packages/scheduler/`：受限自然语言编译器、失败分类与修复策略。
- `apps/api/`：只生成 TaskSpec 预览的自然语言入口。
- `tests/test_nl_react.py`、P7 文档、证据与 memory snapshot。

## 禁止范围

- 不执行或拼接任意 shell；模型文字不能直接改变状态、指标或产物。
- 不从自然语言接受任意本机路径、插件 ID、凭据或无界预算。
- RepairAction 不直接执行，仍需 Runtime/策略批准。

## 验收门

- 中文/英文意图稳定转换为 schema 校验通过的 ORFS/RTLScout TaskSpec。
- 路径来自已登记设计上下文，不从文本提取；平台、stage、模型和数值均白名单/限界。
- 结构化 failure 必须引用证据；修复动作类型和参数模板严格白名单。
- 修复预算、同类失败计数和停止条件生效；恶意 shell 文本被拒绝。
- 全量 pytest、diff 和越界审计通过。

## 预算与停止条件

- 默认最多 2 个 repair actions，同类失败最多 2 次，Task 最大 3 attempts。
- P7 不调用外部 LLM；真实模型入口待显式凭据和费用预算后插入同一严格输出契约。
