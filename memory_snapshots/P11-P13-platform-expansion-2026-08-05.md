# P11-P13 长期记忆快照

date: 2026-08-05
baseline_before_work: `90c2e13`

P11：EDACraft 官方 commit `739eee0...` 的 ImplCraft 已作为 script-generation-only 插件接入。Runtime run `6b93bf...` 成功，8 artifacts、3 Tcl；215 passed + 5 固定已知失败。没有商业 EDA binary/license，不得称为商业 GDS/signoff。

可视化：电路图使用 Graphviz；2D GDS 使用 KLayout `pya.LayoutView`；3D 使用 KLayout 提取真实多边形后由 Matplotlib `Poly3DCollection` 做层序图，Z 不是工艺厚度。TaiWei 与 ORFS 成功产物会登记 layout view，Web 可读取。

P12：`SpecConversationStore` 的 turns 追加保存；Terra/Sol 只做结构化提案。真实 Terra and2 验收先在 `PDN-0185` 失败，LimitedReAct 新增 `increase_floorplan_area`，子 run `2f6d473...` 成功完成 GDS，18 artifacts、12 stage events。失败 run `1f65b712...` 保留。API 提交幂等。

P13：Runtime 接收 ORFS 内部六阶段事件。`StageAwareCampaignManager` 提供参数网格、并发、阶段预算剪枝、修复子 run、Top-K。真实两候选 synth Campaign `campaign-519779...` 通过；规则剪枝/修复/排名由单测覆盖。

恢复入口：先读 `docs/evidence/P11_IMPLCRAFT_ACCEPTANCE.md`、`P12_SPEC_TO_GDS_ACCEPTANCE.md`、`P13_STAGE_AWARE_ACCEPTANCE.md`，再运行 `python -m pytest -q`。后续优先是论文驱动的 P14 自演化（知识库/RL/贝叶斯/GP）和用户提供的代码优化 Agent 项目迁移；不得把 P13 规则策略称为学习算法。
