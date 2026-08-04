# P7 里程碑：自然语言入口与有限 ReAct

captured_at: 2026-08-04
status: completed

- NL compiler 仅支持 ORFS/RTLScout 白名单意图，ORFS 只用登记设计路径，拒绝任意 shell/平台/插件。
- API `/api/tasks/compile` 只返回 TaskSpec preview，明确 `execution_started=false`。
- RepairAction 只有 retry/increase_timeout/lower_core_utilization/stop，精确参数模板且必须引用证据。
- 默认 repair budget=2、同类失败=2、attempts≤3；stop 不能创建下一 TaskSpec。
- 未调用真实 LLM；P7 验收的是确定性安全边界。证据 `docs/evidence/P7_NL_REACT_ACCEPTANCE.{md,json}`，全量 75 passed。
