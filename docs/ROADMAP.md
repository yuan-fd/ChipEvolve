# 分阶段路线

status: accepted
updated_at: 2026-08-06

| 阶段 | 目标 | 真实性门槛 |
| --- | --- | --- |
| P0 | 安全基线、事实审计、版本锁定、架构/数据模型/ADR | Git 基线、22 tests、45 artifacts 校验、三仓库固定 |
| P1 | 版本化契约、Registry、Runtime、Run/StageRun/Attempt、Artifact/Event 查询 | crash/cancel/timeout/retry/recovery 集成测试 |
| P2 | 现有 ORFSRunner 迁为标准插件 | 新 Runtime 下真实 Nangate45 RTL→GDS |
| P3 | 三平台环境与源码兼容性准入 | 固定 commit 的 license/ARM/入口/smoke 结论 |
| P4 | RTLScout 黑箱插件与 RTL→ORFS 组合工作流 | fake smoke；有预算时真实 LLM，否则明确阻塞 |
| P5 | AgenticPD 智能优化接入 | 同基线、预算、工具链的真实候选对比 |
| P6 | Campaign、有限并发、恢复、查询 API/Web | 重启无重复、workspace 隔离、资源与取消证据 |
| P7 | NL→TaskSpec 与白名单 ReAct | 无任意 shell，修复预算和停止条件生效 |
| P8 | TaiWei-Pin-3D 黑箱插件 | 固定 3D toolchain 下 gcd 真实 GDS/指标/视图 |
| P9 | 证据知识库与跨实验复用 | 版本隔离、证据引用和回放测试 |
| P10 | Coding/Evolve Agent | 隔离 worktree、回归、人工/策略闸门 |
| P11 | EDACraft / ImplCraft 初始接入 | 固定源码、ImplCraft 脚本生成 Runtime；商业 live 边界明确 |
| P12 | 多轮自然语言 Spec→GDS | Terra/Sol proposal、确认/预算/幂等、真实 GDS 与修复链 |
| P13 | stage-aware Flow-Agent | 阶段事件、网格并发、剪枝、Top-K、修复子 run |
| P14 | 证据驱动自演化闭环 v1（已验收） | Evidence RAG、BO/GP 提案、真实回灌、RL 离线 shadow；预测与真实隔离；7 个新增 run |
| P15 | 受保护白盒 Tool-Evolve / DPLEvolve 迁移 v1（已验收） | 固定源码、路径保护、隔离构建、patch 前像、人工晋级门；实际候选 full-flow 留待后续 |
| P16 | 开放知识、BYOK 与人控自演化 v1（已验收） | 公开知识/benchmark、持续学习、用户自带模型、T1/T2 建议门、Craft→OpenROAD 真实 GDS |
| P17 | EDACraft 六子项目、五页网站与交付整理（已验收） | 六个独立插件、ImplCraft 兼容、五个英文 Tab、统一结果/学习投影、真实轻量 smoke 与目录审计 |
| P18 | EDACraft 有界真实能力晋级（已验收） | CktCraft `.op`、MoMCraft 最小数值求解、TCAD 真实物理校验、EDACode proposal-only；全部经 Runtime 留证 |
| P19 | 四篇论文方法接入（已验收） | DOI/方法/实现符号/成熟度/Runtime 边界可追溯，离线回放通过 |
| P20 | benchmark、校准、不确定性与 OOD（已验收） | 有界可重放采样、留一校准、区间覆盖、残差尺度和 OOD 报告；预测不冒充观测 |
| P21 | 人控建议到持续学习闭环（已验收） | 接受/修改/拒绝；二次确认后 Campaign→Runtime；终态证据校验后 observed 回灌 |
| P22 | 系统演示、恢复与 RC 收口（已验收） | 五页 Web、四条演示、9 库备份恢复、187 tests、凭据扫描、本地 RC |

P14-P22 证据分别见相应 `docs/evidence/*ACCEPTANCE.md`。P13 的规则剪枝不得包装成 RL/贝叶斯/GP；P14/P19 的离线 RL 也不得包装成已经获得在线控制权。P15 的迁移成功不得包装成 evolved candidate 已完成 full-flow 或 QoR 改善。P20 生成的 benchmark 计划和预测不是 observed 数据。P18 的小规模数值 smoke 不是 TCAD、EM 或 RF sign-off，TCADCraft 完整求解器仍有上游源码一致性阻塞。

## 阶段执行规则

每阶段开始前更新 `tasks/current_task.md`，明确白名单、验收命令、资源和停止条件。阶段结束必须更新 CURRENT_STATUS、NEXT_ACTION、project_state 和只追加 memory snapshot，并审查 diff 与越界文件。

重大 ADR 包括：调度权威、插件协议、数据主键/语义、工具链基线和安全边界。其它实现细节可在已批准阶段内自动推进。
