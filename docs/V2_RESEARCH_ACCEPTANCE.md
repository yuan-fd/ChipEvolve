# v2.0 可信闭环与论文验收

状态：实现完成；论文结论尚待按本协议运行真实实验。

## 1. P1：独立 RTL 验证

`SpecIR -> RTLScout -> frozen oracle -> lint/synthesis -> simulation -> mutation evidence -> ORFS`。

自动 testbench 可以由独立的 Verification Agent 起草，但它必须声明来源、审核人并冻结。若 oracle 的来源是 `approved_generated`，候选 RTL 在进入 ORFS 前还必须通过 Runtime 执行的 mutation-quality gate。该 gate 只说明测试对一组受限单点 mutant 的检错能力；它不替代 formal proof，也不把同一个 Agent 的自检称为独立验证。

## 2. P2：EDAIR v1

EDAIR 保留原始 Runtime artifact 为权威证据，并提供 Run / Design / Timing / Physical 的版本化投影。每个投影需带 artifact SHA-256、parser 与 parser version；截断必须显式标记。Agent 获得的是带回链的受限 evidence view，不是无来源的日志摘要。

## 3. P3：学习与反思

反思只创建不可执行 hypothesis。每个 hypothesis 必须有来源 Run、机制描述、预注册干预和可反驳条件。局部受控实验只能标为 supported/refuted；跨设计 held-out 验证后才可标为 validated，仍需第三个设计的人审确认才能扩展适用范围。

## 4. P4：论文实验

先执行 `scripts/register_v2_research_protocols.py --database <state.db>`，固定并保存协议 hash。三项主张各自有 protocol：

- RTL：direct / RTLScout / independent verifier；报告功能通过率、mutation score、PPA。
- 参数探索：default / seeded random / BO / evidence-guided Agent；等预算、同硬约束。
- 学习迁移：no-memory / RAG / observed / causal；leave-one-design-out。

每个 arm 要报告所有 terminal runs、失败率、重复运行中位数、范围和 bootstrap median interval。只有达到预注册的实用提升阈值且失败率不恶化，才可使用“改进”表述；本框架不自动声称统计显著。

## 5. 尚不可声称的结论

当前仓库的单元/集成测试证明接口、隔离和证据门可用；它们不等于真实论文实验。因此在完成真实 ORFS/RTLScout 多设计运行、收集 protocol 指向的 Runtime evidence 前，不能声称已证明 RTL 泛化、参数优化或自演化迁移效果。

## 6. 本轮新增可复放证据

`scripts/run_v2_frontend_suite.py` 现在会对固定 v2 suite（gcd、fifo、uart_tx、ibex_alu）逐设计、逐重复运行：

- Icarus 编译并执行冻结 Testbench，要求出现 `PASS`；
- Verilator lint；
- 输出 golden RTL/Testbench SHA、工具日志、耗时和每次重复的状态；
- 明确标记这些是平台基线 fixture，不冒充 LLM 生成结果。

本轮实际执行 4 个设计 × 3 次重复，共 12 次，compile、simulation、lint 均通过。该结果只证明固定前端回归题库可复放；RTLScout 生成质量、PPA 优化、自演化迁移仍需按第 4 节 protocol 运行。

`export_netlist_to_circuitops()` 新增低失真数字网表导出：完整保留 CircuitOps 关系表 schema，记录源网表 SHA；没有 library/physical 数据时保留空表而不填假值。它补上了“只有读取外部表、不能从平台产出表”的接口缺口，但 ODB/DEF 的物理属性导出仍需独立 parser。
