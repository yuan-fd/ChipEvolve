# 当前状态

updated_at: 2026-08-06
phase: P0-P15 completed; P14/P15 pending user review

- Runtime 仍是唯一进程、Attempt、Artifact、Event 和终态权威；LLM、BO/GP、离线 RL、DPLEvolve 均无旁路执行权。
- P0-P13 与 P8-Real 的插件平台、真实 2D/3D、自然语言闭环、stage-aware 批量实验和自动纠错能力保持不变。
- P14 已形成证据驱动自演化闭环 v1：严格上下文 Evidence RAG、observed-only 学习库、多目标 GP/BO、ExperimentPlan→Campaign→Runtime→真实回灌、Pareto 与离线 shadow policy。
- P14 复用 3 个历史真实 run，新增 7 个 Runtime run；5 个成功登记 GDS，2 个 `PDN-0185` 失败保留后由独立修复子 run 成功。预算 7/24，并发上限 2。
- 同口径真实比较中，grid/rule 候选优于本轮 BO 候选；没有声称 BO 必然提升 QoR。
- P14 当前有 10 条 observed observation；adder 训练、mux held-out 无设计泄漏。BC 与 linear-Q 均为 `execution_allowed=false`。
- P15 已固定 DPLEvolve commit `96d8c613...`、BSD-3-Clause、365 文件清单，并通过 Runtime 只读 release gate。
- 固定 OpenROAD `d14d526...` 上 `dpl_evolve` 构建成功；base/framework 与两份完整 from-clean 候选 patch 通过。
- 两份 framework delta 前像不一致被保留为失败证据。尚未做 evolved candidate 真实 full-flow/liveness/QoR，不得称为算法效果验收。
- P15 候选只能修改 `tools/OpenROAD/src/dpl_evolve/`；生产基线、flow、经典 dpl、rsz、gpl 和评价器受保护；晋级必须人工批准。
- 当前全量回归 149 passed；定向 P14/P15/API/Web 55 passed；Node 语法、凭据扫描和 diff 检查通过。
- 不 push、不部署、不读取或提交凭据；共享 ORFS/OpenROAD/PDK 未修改。
