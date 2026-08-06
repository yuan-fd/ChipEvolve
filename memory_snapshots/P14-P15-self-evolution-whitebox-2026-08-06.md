# P14-P15 长期记忆快照

date: 2026-08-06
baseline_before_work: `36c2155c57748fc42719833459ddbbb1893c22c1`

## P14：证据驱动自演化闭环 v1

- 新增学习契约：`LearningContext`、observed-only `LearningObservation`、predicted-only `Prediction`、`OptimizationStudy`、`OptimizerProposal`、`TrajectoryStep`、`ShadowPolicyProposal`、`MechanismEvidence`。
- Runtime evidence exporter 只读取 Runtime 已登记的 numeric metric；ORFS QoR 额外只允许固定 `orfs/implementation/analysis/report.json`，并校验 attempt workspace 边界、size 和 SHA-256。
- Evidence RAG v2 在文本评分前硬过滤 PDK、工具链、RTL 指纹、stage、parser version；只有 verified observed_fact/validated_rule 可用于提案。
- NumPy RBF GP 与 scalarized EI 使用固定 seed；输出预测均值/标准差，但没有进程执行能力。
- `OptimizationCampaignBridge` 只把验证后的 ExperimentPlan 交给 StageAwareCampaign/Runtime，白名单参数、幂等 campaign id，predicted 不写入 Runtime canonical metrics。
- 离线 BC 与 linear-Q 只输出 `execution_allowed=false` shadow proposal；轨迹奖励显式拆分 QoR gain、runtime penalty 与 failure penalty。
- API/Web 新增 optimization study 列表、详情、observed/Pareto 与 predicted uncertainty 展示。

真实验收位于 `artifacts/p14-real-20260806/`。复用 3 个历史真实 run，新增 7 个 Runtime run，最大并发 2，低于 24 个批准预算。adder 的 BO/random/grid 三条均完成 GDS；mux 两个初始 run 在 floorplan 报 `PDN-0185`，分别生成 `increase_floorplan_area` 子 run 后完成 GDS。最终 5 个成功 GDS、2 个失败前像，失败未覆盖。

同口径结果：BO wirelength 265 µm，random 278 µm，grid/rule 254 µm；grid/rule 同时有更高 setup WNS 和更低 power。本轮不宣称 BO 优于对照。10 条 observation 全部为 observed；adder train、mux held-out；RAG 错工具链返回 0。

## P15：受保护白盒 DPLEvolve 迁移 v1

- DPLEvolve 固定 commit `96d8c613d62bf3431083bb5e52c7df8853d5a622`，BSD-3-Clause，365 文件稳定清单 SHA `4680820b...`。
- `dplevolve@1.0.0` 只做 Runtime 管理的 read-only release audit；真实 run `9c848e7d...` 成功，源码前后不变，EDA 未执行，promotion 未应用。
- 白盒 evaluator 限制候选修改面为 `tools/OpenROAD/src/dpl_evolve/`；保护 baseline/scripts/flow/经典 dpl/rsz/gpl/evaluator；metrics、正 liveness counter、legality、full-flow 全部是硬门。
- OpenROAD 固定锚点 `d14d526...`；ORFS `dcded683...`；OpenSTA/ABC 子模块固定。
- 私有构建目录 `build/framework-make4` 的 `dpl_evolve` 目标成功；三份静态库 SHA 已写入 P15 evidence。
- 固定 clean archive 重放：base、framework-after-base、diamond from-clean、negotiation from-clean 通过；两份 framework delta 前像失配，失败被保留。
- P15 完成的是安全迁移，不是 evolved candidate 效果验证；尚未做真实 full-flow/liveness/QoR，也没有源码晋级。

## 回归与安全

- 全量：`149 passed in 23.39s`。
- 定向 P14/P15/API/Web：`55 passed`。
- `node --check apps/web/assets/app.js` 通过。
- `python scripts/check_tracked_secrets.py`：220 个 tracked files，0 credential findings，0 suspicious filenames。
- `git diff --check` 通过。
- DPLEvolve 当前内容清单复核仍为 SHA `4680820b...`、365 文件。
- 未 push、未部署、未修改共享 ORFS/OpenROAD/PDK。

## 恢复入口

依次读取：

1. `docs/evidence/P14_SELF_EVOLUTION_ACCEPTANCE.md`
2. `docs/evidence/P15_DPLEVOLVE_ACCEPTANCE.md`
3. `tasks/phase-14.md` 与 `tasks/phase-15.md`
4. `docs/progress/NEXT_ACTION.md`

下一阶段尚未授权。若启动 P16，必须把“扩大跨设计学习实验”和“DPLEvolve evolved candidate 真实 full-flow”分别设预算；不得把 P15 迁移成功等同于候选算法成功。
