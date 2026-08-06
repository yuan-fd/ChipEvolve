# P15 受保护白盒 Tool-Evolve / DPLEvolve 迁移验收

status: accepted_migration_v1
date: 2026-08-06

DPLEvolve 已按固定私有源码归档接入平台。源码锁定在 commit `96d8c613...`，BSD-3-Clause，365 个文件，稳定内容清单 SHA-256 为 `4680820b...`。Runtime 的只读 release gate run `9c848e7d...` 成功，登记 release log、source lock 和 audit report；审计前后源码清单一致，没有执行 EDA，也没有应用或晋级补丁。

白盒候选沿用 P10 的隔离 coding-agent 骨架，但策略进一步收紧：候选只能修改 `tools/OpenROAD/src/dpl_evolve/`；baseline、scripts、flow、经典 dpl、rsz、gpl 和评价器不可修改；缺 required metrics、liveness counter 不为正、legality/full-flow 不通过时均拒绝。晋级门只生成 `applied=false` 收据，源码晋级必须人工批准。

固定历史锚点为 ORFS `dcded683...`、OpenROAD `d14d526...`，OpenSTA/ABC 子模块也已固定。私有构建目录已成功生成 `dpl_evolve_framework_lib`、`dpl_evolve_lib` 和 `dpl_evolve.a`，重新执行构建得到 `Built target dpl_evolve`。

Patch 前像检查从固定 OpenROAD 归档重新解压执行：base、base 后 framework、diamond 完整 from-clean 和 negotiation 完整 from-clean 均通过。两份 `framework_delta` 均无法应用，分别在 `StudentAlgorithm.cpp`，以及 `LegalmFullLegalization.cpp`/`StudentAlgorithm.cpp` 发生前像不一致。该失败被明确保留；迁移采用可从固定 clean anchor 应用的完整候选 patch，不把失配增量描述为成功。

P15 完成的是“受保护迁移 v1”，不是算法效果验收。尚未通过 Runtime 对 evolved candidate 做真实 full-flow/liveness/QoR 对比，没有候选源码被晋级。后续开展白盒研究时，仍须经过隔离构建、机制计数、合法性、完整流程和人工批准门。

机器可读证据见 `docs/evidence/P15_DPLEVOLVE_ACCEPTANCE.json`；Runtime 摘要位于 `artifacts/p15-real-20260806/acceptance_summary.json`，patch 检查位于同目录 `patch_checks.json`。
