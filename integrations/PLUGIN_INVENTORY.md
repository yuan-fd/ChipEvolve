# 外部插件事实清单

审计日期：2026-08-06

本文件记录官方源码与项目补充材料的交叉核验结果。固定版本的机器可读真相源是 `plugins.lock.json`；第三方源码位于被 Git 忽略的 `.external-src/`，平台仓库不得复制第三方私有依赖。

## RTLScout

- 官方仓库：`huawei-csl/rtlscout`
- 固定 commit：`87a00edf6b9208f657dd9ffdda170004024c08ae`
- 许可证：BSD-3-Clause-Clear。
- 主入口：`run_benchmark.py`；离线 smoke 使用 `simple_adder` 和 fake model。
- 输出真相源：每次运行的 `result.json` 与 `best_design/`。
- Python：`pyproject.toml` 要求 `>=3.10`，高于主机系统 Python 3.9.9。
- 依赖：LLM SDK、Amaranth，以及 `tech_eval`、`spire-hdl` 等项目依赖；`spire-hdl` 是固定 commit 的 Git submodule。
- 已有验证：补充报告记录 x86_64 容器 fake smoke 成功，官方预构建镜像在 ARM64 失败。
- P3 增量核验：建立独立 Python 环境，初始化固定 submodule，验证源码级 ARM 安装；不得把镜像架构失败等同于源码不兼容。

接入结论：首版采用黑箱 CLI Adapter，保留其内部 ReAct 和 correctness/cost gate。平台 Runtime 负责外层进程、超时、取消、状态与产物登记。

## AgenticPD

- 官方仓库：`Cheatnut/AgenticPD`
- 固定 commit：`4322a25c1d57bc88d576fd2ce6898a52d30d92c7`
- 当前官方 commit 已包含 `multi_agent_gwtw.py`、Doomed predictor、GWTW scheduler 与实验 YAML，与用户提供报告一致。
- 服务器旧副本停留在 `073ed6d...`，不能作为本轮适配基线。
- 依赖：OpenAI-compatible SDK、Matplotlib、PyYAML；报告基线为 Python 3.10。
- 证据模型：Trial、StageResult、CheckpointRef、ExecutionResolution、decision trace、optimization tree。
- 风险：仓库当前没有 LICENSE/COPYING 声明。许可证澄清前只做内部审计和适配验证，不复制、修改后再分发其源码。

接入结论：保持 Judge/StageAgent/Doomed/GWTW 的智能调度含义。平台生产边界使用 `ExperimentPlan`/`ActionProposal`；进程和最终状态仍由 Runtime 权威管理。黑箱复现只作为兼容性基线，不成为第二平台 scheduler。

## TaiWei-Pin-3D

- 官方仓库：`CODA-Team/TaiWei-Pin-3D`
- 固定 commit：`db20136711ed8c0cdfed67a6123d059875764abd`
- 许可证：BSD-3-Clause；设计与 PDK 子目录还包含各自许可证，打包产物时必须逐项保留。
- 主入口：`run_experiments.py`，支持 ORD/CDS、多任务、状态文件、监控和 kill/retry。
- 官方 README 声明的测试基线：ORFS-Research `568eb04...`、OpenROAD `305d3ba...`。
- 当前平台 2D 基线为 ORFS `51ad123...`、OpenROAD `63ed2e0...`，两者不能假设兼容。
- 输出：GDS、DEF/ODB/Verilog handoff、`openroad_eval.json`、`final_summary.txt`、3D views 与阶段日志。

接入结论：首版整体黑箱适配，使用独立工具链 profile 和工作区；内部阶段只作为带来源的子阶段事件，不进入平台状态主键。真实验证从 `gcd` ORD 流程开始。

P8 状态：`taiwei-pin-3d@1.0.0` 协议接入完成；固定 3D 工具链因 GitHub 连接超时且本机可见版本不匹配而 fail closed。详见 `docs/evidence/P8_TAIWEI_ACCEPTANCE.md`，不得将 fixture 视为真实 gcd。

## 共通准入门

每个插件在实现前必须具备：固定 commit、许可证结论、独立环境、manifest、输入输出 Schema、超时与取消语义、最小 smoke、产物 allowlist、错误传播测试和不包含凭据的环境快照。

## DPLEvolve

- 私有仓库：`CODA-Team/DPLEvolve`；固定 commit `96d8c613d62bf3431083bb5e52c7df8853d5a622`。
- 许可证：BSD-3-Clause；365 个文件的稳定内容清单 SHA-256 为 `4680820b...`。
- 历史锚点：ORFS `dcded683...`、OpenROAD `d14d526...`；OpenSTA/ABC 子模块另行固定。
- `dplevolve@1.0.0` 当前只执行 read-only release-readiness/source-lock audit，不运行 EDA、不修改源码。
- Tool-Evolve 候选只能修改 `tools/OpenROAD/src/dpl_evolve/`，且必须通过 metrics、liveness、legality、full-flow 与人工晋级门。
- P15 已完成固定构建和 patch 前像检查；两份完整 from-clean 候选可应用，两份 framework delta 失配。
- 尚未执行 evolved candidate 的真实 full-flow/QoR 对照，不得宣称代码优化效果已验证。

## EDACraft Extension Pack

- 官方仓库：`ephonic/EDACraft`；固定 commit `739eee0f3ced8fc3cbb6f01b6cc89414758fd898`。
- 根许可证是 MIT-like 加 Non-Commercial 限制；仅允许本机私有非商业验收。
- 平台按六个独立插件接入，而不是把整个 monorepo 包装为一个含糊的 “IC Craft”：
  - `edacraft-rtlcraft`：前端白盒 Python DSL、SystemVerilog 生成和验证表面；
  - `edacraft-edacode`：模拟/混合信号 Agent 与 VS Code 表面，当前禁止暴露上游任意 shell/file-write；
  - `edacraft-tcadcraft`：器件级 3D TCAD，当前执行真实几何 smoke，不声称完整求解；
  - `edacraft-momcraft`：互连电磁与 S 参数，当前执行真实 Touchstone I/O smoke，不声称全波求解；
  - `edacraft-cktcraft`：SPICE/RF 求解器表面，固定版 v0.2 使用 Verilog-A→C++ 静态模型，当前为源码准入；
  - `edacraft-implcraft`：保留原 P11 数字后端 dry-run 脚本生成接入。
- P11 上游回归 220 项：215 passed、5 个固定已知失败。
- 本机无商业 EDA binary/license；`edacraft-implcraft@1.0.0` 只声明 `eda.implcraft.scriptgen` 和 `eda.backend.plan`，禁止宣称商业 GDS/signoff。
- P17 六组件均经 Workflow Runtime 产生独立 run 和哈希证据；能力等级详见 `docs/evidence/P17_EDACRAFT_WEB_ACCEPTANCE.md`。
