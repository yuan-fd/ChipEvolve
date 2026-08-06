# 当前任务：P16 开放知识、BYOK 与人控自演化 v1

status: planned_waiting_for_execution_approval
authorization: planning_only
planned_at: 2026-08-06
base_commit: 55c0bde67ecbd9332a7f44d0d5c11ac2a119e1c8

## 目标

把公开知识/benchmark、持续数据积累、用户自带模型、RL 建议与有限自动资格、Craft→OpenROAD 后端纳入同一个可审计阶段。

## 已冻结边界

- 本轮规划不实施 P16 代码，不下载大数据集，不调用付费模型，不运行新的真实 EDA。
- P16 执行预算建议为最多 8 个新增 ORFS run、并发 2；DPLEvolve full-flow 为 0。
- API key 推荐只在会话内存保存，默认 TTL 8 小时；用户数据默认私有。
- T1 用户建议为默认；T2 有限自动默认关闭，数据不足时必须返回 not eligible。
- IC Craft 使用 backend-neutral FlowPlan 和现有 ORFS Runtime，不伪造商业 Tcl/signoff。

完整施工范围、验收和审批项见 `tasks/phase-16.md`。用户明确批准后才能进入实现。
