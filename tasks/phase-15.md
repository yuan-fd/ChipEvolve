# P15：受保护白盒 Tool-Evolve / DPLEvolve 迁移

status: accepted_migration_v1
completed_at: 2026-08-06
base_commit: 36c2155c57748fc42719833459ddbbb1893c22c1

## 完成范围

- 固定 DPLEvolve commit、许可证、365 文件内容清单和历史 ORFS/OpenROAD/OpenSTA/ABC 锚点。
- 注册 read-only source audit 插件，并通过 Runtime 真实执行 release gate；源码前后未改变。
- 增加 `WhiteBoxPolicy`、隔离 evaluator 和人工 promotion gate。
- 候选修改面限定在 `tools/OpenROAD/src/dpl_evolve/`，保护生产基线与评价器。
- 在私有工具目录完成 framework/base 构建，`dpl_evolve` 目标成功。
- 从固定 clean OpenROAD 归档重放 patch check；完整候选通过，两份 framework delta 前像失配被保留。
- 单元测试覆盖路径越权、指标/liveness/legality/full-flow 门和永不自动晋级。

## 明确边界

本阶段只验收安全迁移、固定构建和候选评价边界。尚未执行 evolved candidate 的真实 full-flow/liveness/QoR 对照，也未晋级源码。任何后续候选必须在 Runtime 下隔离运行并经人工批准。

详细证据：`docs/evidence/P15_DPLEVOLVE_ACCEPTANCE.md`。
