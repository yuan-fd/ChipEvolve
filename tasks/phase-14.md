# P14：证据驱动自演化闭环 v1

status: accepted
approval: executed_under_user_approved_goal
planned_at: 2026-08-05
completed_at: 2026-08-06
base_commit: 36c2155c57748fc42719833459ddbbb1893c22c1

## 1. 阶段目标

把 P9 的证据知识库、P13 的 stage-aware Campaign 和 P10 的安全提案骨架，组合成第一条真实、可回放的学习闭环：

```text
Runtime/Campaign 原始证据
  -> 版本与设计上下文隔离的数据集
  -> Evidence RAG
  -> BO/GP 生成只读 OptimizerProposal
  -> Schema 校验后的 ExperimentPlan
  -> Stage-aware Campaign
  -> Runtime 执行真实 ORFS
  -> observed metrics / failure / cost 回灌
  -> 更新代理模型、Pareto 前沿和知识证据
```

强化学习在 P14 只进入离线 shadow 模式：建立轨迹、状态/动作/奖励契约和离线基线，不拥有在线调度权。P14 完成后，平台可以诚实声明“已具备证据 RAG + BO/GP 真实闭环和 RL 离线研究基础”，不能声明已经实现跨设计自主学习或专家级策略。

## 2. 研究材料映射

| 材料 | P14 用途 | 实施边界 |
| --- | --- | --- |
| PTPT | 多目标物理设计参数 BO | 借鉴问题建模和多目标评价，不复述论文结果为平台结果 |
| Customized RAG for EDA Tool Documentation QA | 文档与经验的混合检索、rerank | 增加 PDK/工具链/设计/阶段硬过滤和证据哈希 |
| DRiLLS | EDA 序列决策和奖励设计 | P14 只做离线轨迹与 shadow policy |
| GP surrogate | QoR 预测与不确定性 | prediction 与 observed metric 严格分表/分来源 |
| A2-ORFO / TaiWei-flow-Agent | LLM 先验与 BO 的组合方式 | 执行前固定用户给定 commit 并审计许可证、入口和依赖 |
| DAC 2026 专家布局学习 | 专家示范/偏好构造 | 作为后续策略学习参考，不在小数据上宣称专家级 |
| ReviewDSE / DPLEvolve | 受保护白盒源码机制探索 | 留到 P15；不与 P14 黑盒学习闭环混做 |

阶段启动时必须记录论文/源码的实际文件 SHA-256、commit、license 和可复现入口。当前 `DPLEvolve` 私有仓库无法匿名读取，不阻塞 P14，但会阻塞 P15 实际迁移。

## 3. 分阶段实施

### P14-A：材料审计与学习契约冻结

1. 审计论文、A2-ORFO 固定源码和现有 P9/P10/P13 接口。
2. 形成以下版本化契约：
   - `LearningObservation`：设计特征、参数、阶段、真实指标、失败、运行成本、证据引用；
   - `Prediction`：预测值、方差/置信信息、模型版本，固定 `source=predicted`；
   - `OptimizationStudy`：目标、约束、参数空间、预算、随机种子和数据切分；
   - `OptimizerProposal`：候选参数、采集函数值、证据、风险和剩余预算；
   - `Trajectory`：state、action、next_state、reward、terminal、run/attempt 引用。
3. 冻结设计特征与 QoR 单位，防止不同 PDK、工具版本和阶段的样本误混。
4. 若必须改变 Runtime DB 主键、终态或调度语义，先提交 ADR 并暂停，不在 P14 内暗改。

### P14-B：学习数据层与 Evidence RAG v2

1. 从 RuntimeStore、CampaignStore 和 Artifact Store 只读导出学习样本，不重写原始证据。
2. 每条样本强制带 design fingerprint、PDK、OpenROAD/ORFS commit、参数、stage、metric parser version、run/attempt、artifact ref/SHA。
3. 将知识类型扩展为 `observed_fact`、`validated_rule`、`hypothesis`、`failed_attempt`；只有前两类可参与自动提案。
4. 实现硬上下文过滤后的混合检索与 rerank；向量检索不可绕过版本门，LLM 生成文本不进入事实字段。
5. 建立至少 20 条带期望证据的检索/错配/篡改测试；版本错配、证据篡改和预测冒充真实指标必须 100% 拒绝。

### P14-C：多目标 BO 与 GP 代理闭环

1. 在项目私有隔离环境中固定科学计算依赖和锁文件，不修改系统、共享 Python 或共享工具链。
2. 第一版搜索空间限定为已真实接通的白名单连续参数，例如 core utilization、place density、clock period；不开放任意 Tcl 或 shell。
3. 以时序、功耗、面积、线长、DRC 和 runtime 构造约束多目标研究；目标方向、单位、缺失值和失败惩罚显式登记。
4. GP 输出均值与不确定性；BO 只生成 `OptimizerProposal/ExperimentPlan`，不得直接启动进程或写终态。
5. 由 P13 StageAwareCampaignManager 提交候选，Runtime 执行后回灌 observed 结果；重启必须能够从 study 状态幂等恢复，不重复提交。
6. 同预算比较 random、现有 grid/rule 和 BO/GP。无论是否改善 QoR，都保存全部候选、随机种子、失败和 Pareto/超体积结果，禁止只挑最佳样本。

### P14-D：强化学习离线 shadow 基线

1. 从真实 Campaign 导出不可变 trajectory dataset，并明确状态、动作、奖励和终止定义。
2. 奖励同时考虑 QoR、DRC、失败、运行时间和资源消耗；每项权重版本化，并保留原始分量。
3. 建立规则策略、模仿/行为克隆基线和至少一种离线价值或策略基线；使用设计级切分，防止同一设计运行泄漏到训练与测试两侧。
4. RL 结果只生成 `execution_allowed=false` 的 shadow proposal；通过离线回放和固定 held-out 设计报告收益、方差和失效案例。
5. 若样本量不足以支持有效训练，阶段结果应明确记录“数据不足”，但轨迹契约、导出器和评测基线仍可验收；不得用合成结果冒充真实轨迹。

### P14-E：真实系统验收与展示

1. 在同一固定 2D Nangate45 工具链上选择至少两个结构不同的设计。
2. 总预算上限 24 个新增 Runtime run，最大并发 2；先做阶段 smoke，再对每种方法晋级的 Top-1 做完整 RTL→GDSII 复验。
3. 验证链必须包含：提案、ExperimentPlan、Campaign、stage events、真实 metrics、GDS/报告、证据入库、模型更新和可回放查询。
4. API/Web 最小展示 study 预算、候选来源、Pareto 前沿、predicted/observed 对照、不确定性、工具链版本和证据引用；不新增复杂可视化框架。
5. 运行全量回归、安全/凭据扫描、数据库备份恢复和文档一致性审计，生成 P14 acceptance evidence 与只追加 memory snapshot。

## 4. 验收标准

- P13 的 103 项测试基线不回退，新功能具备单元、集成、恢复和安全测试。
- 所有训练/检索样本可追溯到真实 run/attempt/artifact，证据引用与 SHA-256 校验通过。
- `predicted`、`observed`、`hypothesis` 在 Schema、数据库和 API 中不可混淆。
- RAG 的错版本、错设计、篡改证据和无来源结论被确定性拒绝。
- BO/GP 可以在固定 seed 下重放候选序列；优化器不能绕过 Runtime。
- random、grid/rule、BO/GP 使用相同设计、工具链、约束和运行预算；完整报告全部结果。
- 至少两个设计完成真实候选评估，晋级候选有真实 GDS、QoR、日志、hash 和 Runtime 事件链。
- RL shadow policy 没有直接执行路径，线上控制权仍为零。
- Runtime 仍是唯一进程与状态权威；重启不重复提交，失败记录不被覆盖。
- 不修改共享 OpenROAD/ORFS/PDK，不提交凭据、模型大文件、第三方源码或原始运行缓存。

QoR 改善是研究结果而不是完成条件。若 BO 未优于对照，必须如实报告并分析原因，不能通过改变预算、工具链或过滤失败样本制造提升。

## 5. 允许与禁止范围

允许修改：

- `packages/analysis/`：数据集、RAG、GP/BO、离线评测；
- `packages/contracts/`：新增版本化学习契约；
- `packages/scheduler/`：只增加 study/proposal 到现有 Campaign 的受控桥接，不改变 Runtime 权威；
- `workflows/flow_optimization/`、`apps/api/`、`apps/web/`；
- `tests/`、`scripts/`、`docs/`、`tasks/`、`memory_snapshots/`；
- 项目私有 `.tools/`、`runs/`、`var/` 中事先登记的 P14 生成物。

禁止：

- 修改共享工具链、PDK 或第三方固定源码；
- 让 LLM、BO、GP、RL、A2-ORFO 成为第二调度器；
- 任意 shell/Tcl 生成与执行；
- 把预测值写成 canonical Metric；
- 自动 merge/apply/push 源码补丁；
- 在 P14 内迁移 DPLEvolve 或开展生产基线白盒修改；
- push、部署、写入或显示凭据。

## 6. 资源预算与暂停条件

- 新增真实 ORFS 运行不超过 24 次，最大并发 2，连续执行窗口不超过 8 小时；超出需重新批准。
- 同一根因最多 3 次尝试，每次必须改变假设或措施。
- 新依赖必须固定版本并记录许可证；ARM64 无可信构建或依赖冲突时先暂停该分支，不污染现有环境。
- 出现 Runtime 权威变化、DB 核心语义变化、工具链基线变化、评价口径争议或需扩大真实运行预算时，暂停并提交方案。
- DPLEvolve 认证缺失不阻塞 P14；P15 开始前需要用户提供完整源码包或仓库访问。优先完整源码与可重放环境，论文 artifact 用于交叉验收。

## 7. P14 完成后的下一阶段

P15 原定为“受保护白盒 Tool-Evolve 与 DPLEvolve 迁移”：审计用户组内完整源码，固定 commit/许可证/环境，把其代码级候选接入 P10 隔离 evaluator，结合 ReviewDSE 的机制级证据、liveness、完整流程和人工晋级门进行真实验证。该迁移 v1 已在同一用户授权目标中完成，见 `tasks/phase-15.md`。

## 8. 实际验收结果

P14 已在固定 Nangate45/ORFS 工具链完成。复用 3 个历史真实 run，新增 7 个 Runtime run（5 个成功 GDS、2 个保留失败），最大并发 2。主设计完成 BO/GP、random、grid/rule 同口径比较；第二设计的两个 `PDN-0185` 失败由 Stage-aware ReAct 创建独立修复子 run 后成功。observed/predicted、设计级训练/held-out、RAG 上下文门和非可执行 shadow policy 均通过验收。

本次 grid/rule 的真实 QoR 优于 BO，未宣称 BO 必然改善。完整证据见 `docs/evidence/P14_SELF_EVOLUTION_ACCEPTANCE.md`。
