# 新服务器接手审计

日期：2026-09-18
仓库：`yuan-fd/ChipEvolve`
基线：当前 main 分支的本地验收提交

## 结论先行

新服务器具备继续开发和单机运行 Agentic EDA Execution Foundation 的条件。当前服务器的 ORFS/OpenROAD Nangate45 GCD、Cadence Innovus、Synopsys ICC2、Synopsys PrimeTime 和 Cadence Genus 均已通过 Foundation 的最小 runtime/script 验收。ICC2 使用官方 wrapper，PrimeTime 由 Toolkit 注入 `SYNOPSYS_LC_ROOT=$LC_HOME` 修复 PT-063；Genus 的 Rocky 8 unsupported-OS warning 仍保留在原始日志中，不被误报为供应商支持声明。

“执行成功”“工具成功”和“设计质量达标”在本审计中分开记录。GCD 本次只确认 ORFS 产物和流程结束，没有宣称 QoR/signoff 达标。

## 已确认存在与实测证据

| 类别 | 结果 |
| --- | --- |
| 服务器 | Rocky 8.10、128 logical CPUs、314 GiB RAM、22 TiB `/home` 挂载 |
| 项目安装 | `.venv` editable 安装成功；安装前需先提供 `setuptools/wheel` |
| 测试 | 基线平台测试和服务器 Toolkit smoke 均已通过；完整结果以当前提交后的复跑为准 |
| Guardrails | `40 passed` |
| mypy | 正式源码 `RC 0` |
| Ruff/Black | 仓库全量未通过；见 baseline 报告，未自动格式化故意违规 fixture |
| ORFS/OpenROAD | GCD finish 成功；`6_final.gds/odb/def/v` 和报告存在，产物 SHA-256 已计算；进程已退出 |
| Innovus Toolkit | Foundation `WorkflowRuntime -> ProcessGuardian -> cadence-innovus adapter -> Innovus` 成功；run status `succeeded`，artifact kinds 为 `runtime_input_manifest/toolchain/tool_log/script_receipt`，metric `tool_version` 可追溯 |
| 商业工具 | 真实 Foundation run 均记录 toolchain/tool_log/script receipt；运行日志保留在 attempt workspace，toolchain metadata 不保存部署 license 值 |

## Toolkit/Adapter 边界核对

1. Agent 可以在 `TaskSpec.plugin_id`（兼容字段）和可选 `plugin_version` 中选择 Toolkit；registry 负责解析和 admission。
2. `inputs`、`parameters` 是 opaque mapping，底座不解释领域参数。
3. `staged_inputs` 支持 Agent 生成的 TCL/Python/shell 文件；平台在 workspace 中复制、hash 并记录 input manifest。
4. patch/build/benchmark 可以作为普通 Task 的 staged input；底座不执行 patch 语义。
5. kernel/runtime 只处理生命周期、资源、进程和 evidence，不解释或修改 placement/CTS/route 参数。
6. plan executor 按 Agent 提交的有序 task 列表执行，不生成 EDA 流程。
7. kernel 中没有 OpenROAD/ORFS/Innovus/PrimeTime 具体策略；Guardrails G1/G2/G16 通过。
8. 不同 plugin 使用同一 runtime 的 workspace/process/artifact/metric/evidence 生命周期。
9. adapter failure 在 `PluginResult.failure.category` 中记录；平台的 timeout/cancel/protocol/resource failure 单独记录。
10. Toolkit manifest 的 `toolkit` 元数据、adapter provenance 和 `toolchain` artifact 记录工具版本与环境键；具体路径/许可证由部署提供，不写入仓库。

## 当前实际 Toolkit

- `example-reporter`：仓库内参考 Toolkit，默认测试全覆盖。
- `cadence-innovus@1.0.0`：支持 `preflight` 和 Agent-staged `script`，运行入口由 Cadence module 提供。
- 用户目录 `~/toolkits/chipevolve-orfs`：提供 `preflight`、`finish`、`script`，当前服务器 GCD acceptance 已通过。
- 用户目录 `~/toolkits/synopsys-icc2`、`synopsys-primetime`、`cadence-genus`：均支持 `preflight` 和 `script`，环境 wrapper 由 Toolkit 自己负责。

## 限制与后续

- ICC2 动态库入口和 PrimeTime PT-063 已在用户目录 Toolkit 内解决；Genus 的供应商支持警告仍需后续版本治理。
- SQLite 测试兼容性应在后续选择“升级运行时 SQLite”或“改写迁移夹具”之一；本阶段未改变系统库。
- 商业 Toolkit 当前完成最小 runtime/script 能力，不包含真实 PDK/NDM/Milkyway physical flow、固定 flow 策略或 QoR 判定。
- 本阶段没有实现多节点、对象存储、强隔离、license manager 或通用 adapter 抽象。
