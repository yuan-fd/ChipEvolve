# 新服务器接手审计

日期：2026-09-18
仓库：`yuan-fd/ChipEvolve`
基线：`main@5343acea0a916bd5b1542672bbd9b569566c9483`

## 结论先行

新服务器具备继续开发和单机运行 Agentic EDA Execution Foundation 的条件。开源 ORFS/OpenROAD 链路已在独立临时工作目录完成真实 Nangate45 GCD `finish`；Cadence Innovus 已通过新接入的 Toolkit Adapter，由底座托管一次 Agent-staged Tcl 脚本并成功收集版本、日志、script receipt 和指标。商业工具不是全部可用：ICC2 被动态库错误阻塞，PrimeTime 尚未完成最小启动，Genus 的旧 build expiry 需要管理员确认。

“执行成功”“工具成功”和“设计质量达标”在本审计中分开记录。GCD 本次只确认 ORFS 产物和流程结束，没有宣称 QoR/signoff 达标。

## 已确认存在与实测证据

| 类别 | 结果 |
| --- | --- |
| 服务器 | Rocky 8.10、128 logical CPUs、314 GiB RAM、22 TiB `/home` 挂载 |
| 项目安装 | `.venv` editable 安装成功；安装前需先提供 `setuptools/wheel` |
| 测试 | `466 passed, 1 skipped, 3 failed`；失败均为 SQLite 3.26 不支持测试使用的 `DROP COLUMN` |
| Guardrails | `40 passed` |
| mypy | 正式源码 `RC 0` |
| Ruff/Black | 仓库全量未通过；见 baseline 报告，未自动格式化故意违规 fixture |
| ORFS/OpenROAD | GCD finish 成功；`6_final.gds/odb/def/v` 和报告存在，产物 SHA-256 已计算；进程已退出 |
| Innovus Toolkit | Foundation `WorkflowRuntime -> ProcessGuardian -> cadence-innovus adapter -> Innovus` 成功；run status `succeeded`，artifact kinds 为 `runtime_input_manifest/toolchain/tool_log/script_receipt`，metric `tool_version` 可追溯 |
| Innovus license | 最小 preflight 日志显示 license checkout succeeded；许可证值已从报告和仓库中排除 |

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
- `cadence-innovus@1.0.0`：本阶段新增最小商业 Toolkit，支持 `preflight` 和 Agent-staged `script`；需要 `module:cadence`、OA 和 license。
- ORFS/OpenROAD：服务器工具链存在并已独立实测，但完整 ORFS adapter/manifest 不在本仓库；历史 GCD 文档中的旧路径不能当作本机证据。

## 限制与后续

- 需要管理员处理 ICC2 `libkrb5` 兼容性、Genus expiry 和 PrimeTime 启动验证。
- SQLite 测试兼容性应在后续选择“升级运行时 SQLite”或“改写迁移夹具”之一；本阶段未改变系统库。
- 商业 Toolkit 目前只做最小脚本能力，不包含 Innovus flow 策略、PDK 选择或 QoR 判定。
- 本阶段没有实现多节点、对象存储、强隔离、license manager 或通用 adapter 抽象。
