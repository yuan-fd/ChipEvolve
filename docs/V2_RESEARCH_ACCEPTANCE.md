# v2.0 可信闭环与论文验收

状态：v2 主链实现与一轮真实实验已完成；统计泛化结论仍受下述样本边界约束。

## 1. P1：独立 RTL 验证

`自然语言 -> SpecIR -> 独立 Verification Agent -> 冻结 testbench -> RTLScout 候选 -> lint/synthesis -> simulation -> mutation evidence -> ORFS`。

Verification Agent 与 RTLScout 使用分离的角色、提示和写权限：前者生成并冻结 testbench，后者只能写候选 RTL，不能修改判题环境。这里的“审核人”字段是机器生产者身份和审计归属，不表示默认需要人工编写或点击批准。候选进入 ORFS 前必须通过 Runtime 执行的 mutation-quality gate。该 gate 只说明测试对一组受限、可执行 mutant 的检错能力；它不替代 formal proof。两种角色目前由同一平台托管模型 `gpt-5.6-terra` 承担，不能称为“两个独立模型”。

## 2. P2：EDAIR v1

EDAIR 保留原始 Runtime artifact 为权威证据，并提供 Run / Design / Timing / Physical 的版本化投影。每个投影需带 artifact SHA-256、parser 与 parser version；截断必须显式标记。Agent 获得的是带回链的受限 evidence view，不是无来源的日志摘要。

## 3. P3：学习与反思

反思只创建不可执行 hypothesis。每个 hypothesis 必须有来源 Run、机制描述、预注册干预和可反驳条件。当前实现要求同一上下文、同一 RTL 指纹、两个参数各两个水平且每个组合至少重复两次，再用 difference-in-differences 计算局部交互；跨设计 held-out 复测决定该经验是 replicated 还是 refuted。即便 replicated，知识卡仍只有提案权，没有直接执行权。一次 holdout 不能建立普适因果规律。

## 4. P4：论文实验

先执行 `scripts/register_v2_research_protocols.py --database <state.db>`，固定并保存协议 hash。三项主张各自有 protocol：

- RTL：direct / RTLScout / independent verifier；报告功能通过率、mutation score、PPA。
- 参数探索：default / seeded random / BO / evidence-guided Agent；等预算、同硬约束。
- 学习迁移：no-memory / RAG / observed / causal；leave-one-design-out。

每个 arm 要报告所有 terminal runs、失败率、重复运行中位数、范围和 bootstrap median interval。只有达到预注册的实用提升阈值且失败率不恶化，才可使用“改进”表述；本框架不自动声称统计显著。

## 5. 2026-08-25 真实研究证据

以下结论均来自真实 OpenROAD 完整 finish 运行；路径中的 aggregate/report 是结论权威，报告不得越过其中的 `claim_boundary`：

- RTL 固定题库：`artifacts/v2-real-rtl-suite-20260825/aggregate.json`。gcd、FIFO、UART TX、ibex_alu 四题各一个生成 seed，均由自然语言开始，经独立自动 testbench、RTLScout、lint、仿真、mutation 和 ORFS 产出非空 GDS。它证明固定题库单 seed 可行性，不证明任意规格泛化；`ibex_alu` 不是完整 Ibex 核。
- 自主闭环：`artifacts/v2-multidesign-closed-loop-20260825/aggregate.json`。4 个设计各 baseline + 3 个候选，每个向量重复 3 次，共 48 次完整 flow；3/4 设计达到预注册 0.5% 实用阈值。它是描述性闭环证据，不是算法优越性证明。
- BO 对随机：`artifacts/v2-parameter-ablation-multiseed-20260825/aggregate.json`。4 设计 × 3 seed，BO 144 runs、等预算 seeded random 144 runs；达到 0.5% 阈值的 design-seed 单元为 7/12 对 4/12，但设计级中位数胜负为 2:2。只能声称 BO 在该预算下提高了阈值命中频次，不能声称普遍优越或统计显著。
- 因果学习门：`artifacts/v2-learning-ablation-20260825/aggregate.json` 与 `artifacts/v2-real-causal-gcd-fifo-20260825/causal-audit.json`。24/24 完整 flow 成功；GCD 局部交互为 +1.862 µm²，FIFO held-out 反向为 -1.596 µm²，系统将迁移假设标为 `refuted` 且 `action_eligible=false`。它只证明拦截了一次已观察到的错误迁移，不是总体迁移率估计。
- EDA-to-AI：`artifacts/v2-edair-ablation-20260825/aggregate.json`。四设计导出 18 条 timing path、2,236 条逻辑 net、1,885 个逻辑 instance、4,704 个物理 instance、84 个带 SHA 的原始 artifact 和 190 条受限 Agent facts。它证明比 12-KPI 摘要保留更多可查询结构与来源，不证明诊断准确率或 QoR 提升。
- Agent 编排：`artifacts/v2-agent-architecture-20260825/aggregate.json`。四设计均出现 `map -> semantic -> experiment -> hypothesis -> implement -> validate -> review -> memory` 八阶段，checkpoint、幂等恢复和权限边界另有故障注入测试。它证明编排可靠性，不证明多 Agent 本身提升 QoR。

## 6. 尚不可声称的结论

- 尚未完成 RTL direct / RTLScout / independent-verifier 三臂、每题 5 seed 的完整预注册比较，因此不能报告“任意自然语言 RTL 成功率”。
- 12 个 design-seed 单元不足以给出 BO 普遍优于随机或统计显著的结论。
- 单个 GCD→FIFO holdout 不等于深层因果识别或普适跨设计迁移。
- EDAIR 的结构保真与 Agent 八阶段完整，均不能直接换算成 QoR 收益。

## 7. 历史固定 fixture 证据

`scripts/run_v2_frontend_suite.py` 现在会对固定 v2 suite（gcd、fifo、uart_tx、ibex_alu）逐设计、逐重复运行：

- Icarus 编译并执行冻结 Testbench，要求出现 `PASS`；
- Verilator lint；
- 输出 golden RTL/Testbench SHA、工具日志、耗时和每次重复的状态；
- 明确标记这些是平台基线 fixture，不冒充 LLM 生成结果。

历史轮次实际执行 4 个设计 × 3 次重复，共 12 次，compile、simulation、lint 均通过。它使用 golden fixture，只证明回归题库可复放，不能替代第 5 节的真实自然语言生成证据。

`export_netlist_to_circuitops()` 新增低失真数字网表导出：完整保留 CircuitOps 关系表 schema，记录源网表 SHA；没有 library/physical 数据时保留空表而不填假值。它补上了“只有读取外部表、不能从平台产出表”的接口缺口，但 ODB/DEF 的物理属性导出仍需独立 parser。
