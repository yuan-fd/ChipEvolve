# 外部插件事实清单

审计日期：2026-08-04

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

## 共通准入门

每个插件在实现前必须具备：固定 commit、许可证结论、独立环境、manifest、输入输出 Schema、超时与取消语义、最小 smoke、产物 allowlist、错误传播测试和不包含凭据的环境快照。
