# P14 证据驱动自演化闭环 v1 验收

status: accepted
date: 2026-08-06

P14 已把 Runtime 真实证据、上下文隔离数据集、Evidence RAG、NumPy RBF GP、多目标 BO、受控 ExperimentPlan/Campaign、真实 ORFS 回灌、Pareto 与离线 shadow policy 串成闭环。优化器和两种离线策略均只产生数据对象，`execution_allowed=false`；所有 EDA 进程和终态仍由 Runtime 唯一拥有。

本轮复用 P5 的两个 adder 和 P2 的一个 mux 真实 run 作为 warm-start，并新增 7 个 Runtime run，低于 24 个批准预算，最大并发为 2。其中 5 个成功并登记真实 GDS；mux 的两个初始候选在 floorplan 遇到 `PDN-0185`，Stage-aware ReAct 分别创建 `increase_floorplan_area` 子 run 后成功。两条失败 run、attempt、日志和 failure 均保留，没有被成功结果覆盖。

主设计在相同 RTL、工具链、约束和单候选预算下比较 BO/GP、固定种子 random、grid/rule。结果如下：

| 方法 | wirelength (µm) | setup WNS (ns) | power (W) | DRC |
| --- | ---: | ---: | ---: | ---: |
| BO/GP | 265 | 5.6559 | 8.08437e-6 | 0 |
| random | 278 | 5.6553 | 8.11644e-6 | 0 |
| grid/rule | 254 | 5.6565 | 8.07794e-6 | 0 |

这次 grid/rule 候选优于 BO 候选，平台不把 BO 包装成必然改进。BO 固定 seed 重放得到相同提案；预测均值/标准差保存于 proposal，真实值只从 Runtime 已登记且 SHA-256 匹配的 `analysis/report.json` 导出。报告路径逃逸、未登记报告、大小或哈希篡改均被拒绝。

学习层现有 10 条 observed observation，按 PDK、工具链、RTL 指纹、stage 和 parser version 隔离。RAG 对错误工具链返回 0 条；行为克隆与 linear-Q 使用 adder 轨迹训练、mux 轨迹 held-out，设计集合无交集，二者均无线上执行路径。该结果只证明离线研究基线和数据契约成立，不宣称小样本策略已获得跨设计泛化能力。

机器可读摘要见 `docs/evidence/P14_SELF_EVOLUTION_ACCEPTANCE.json`；完整原始证据位于 `artifacts/p14-real-20260806/`，摘要 SHA-256 为 `4f5e61affba32923b6f4c437af699b5153b3a04ded405085e5bdf02e8076ee71`。
