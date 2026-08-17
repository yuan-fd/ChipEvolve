# 🔬 AI for EDA 数据对照说明

> 参考 Si2（Silicon Integration Initiative）AI for EDA Ontology 标准体系 · 对照本平台的现状


## 一、Si2 是什么？它的 AI for EDA 标准是什么？

> **Si2（Silicon Integration Initiative，硅集成联盟）**是半导体行业的一个标准组织——行业里的公司（西门子 EDA、IBM、高通、NXP、Synopsys 等）和大学（亚利桑那州立、Drexel 等）凑在一起，定"共同语言"。

**AI for EDA Ontology（AI 驱动的 EDA 本体）：**2026 年 6 月公开发布的开放标准（Apache 2.0 开源），目的就是给"AI 用在芯片设计上"定一套**统一的概念和关系**，让不同的 AI/EDA 工具能互相理解、协同工作。

这套标准包含：

- **本体文件**（TTL/OWL 格式）：正式定义"网（net）、单元（cell）、时序路径（timing path）"等概念，以及它们之间的**关系**（"影响 affects"、"依赖 depends on"、"约束 constrains"）；
- **工作流语义**：工作流步骤、依赖关系、因果链、设计权衡、领域词汇；
- **验证过的用例**（亚利桑那州立大学、Drexel 大学做的）；
- **MCP 服务器**：让 AI 代理能"发现"和"推理"这些概念；
- **文档包**。

**一句话：**Si2 想给"AI 理解芯片设计"定一套"普通话"——大家别各说各话，都按这套概念体系来说，AI 才能跨工具协作。

参考来源：[Semicon Leaders Asia 报道](https://semiconleadersasia.com/news/41/1263/semiconductor-industry-collaboration-unveils-open-ontology-for-ai-powered-eda-workflows.html) · [Si2 官网](https://si2.org/si2-names-nvidia-synopsys-technologists-to-lead-new-llm-benchmarking-coalition/)


## 二、为什么要做"数据对照"？

本平台自己也做"AI for EDA"（自演化、知识库、AI 建议），那就值得问一个问题：

**本平台记录的数据，和 Si2 标准里定义的"AI 需要的数据"，对得上吗？**

对得上 → 将来可以对接行业生态（大家用同一套语言，平台的数据能被别的 AI 工具读、别的数据能进来）；

对不上 → 本平台的数据是"方言"，只能自己用，跨平台协作会困难。

所以下面做一张"对照表"，把 Si2 的核心概念和本平台的数据结构一一比照，标出：✅ 对上了 / ⚠️ 部分对上 / ❌ 还没有。


## 三、核心对照表：Si2 概念 ↔ 本平台

| Si2 标准概念 | 大白话 | 本平台的对应物 | 对照 |
| --- | --- | --- | --- |
| 网 Net | 连接芯片各点的导线 | netlist 文件（门级网表）、跨层网络报告（cross_tier_nets） | ✅ 有（netlist + 3D 跨层网络统计） |
| 单元 Cell | 芯片里的基本零件（标准单元） | 工艺库（LEF/LIB）+ 布局结果（DEF 里的实例） | ✅ 有 |
| 时序路径 Timing Path | 信号从哪到哪、多快走到 | openroad_eval.json 的 setup/hold 时序指标（WNS/TNS） | ✅ 有 |
| 设计意图 Design Intent | 用户想要什么（时钟、目标、约束） | TaskSpec（任务单）里的 clock/parameters + SDC 约束文件 | ✅ 有 |
| 工艺/库 PDK & Library | 用哪套"建材标准" | TaskSpec 的 tech 字段 + platform 参数 | ✅ 有 |
| 工作流步骤 Workflow Steps | 流程分几步、顺序如何 | Runtime 的 stage_runs（20 阶段 3D 流程 / 6 阶段 2D 流程） | ✅ 有（事件化记录） |
| 步骤依赖 Dependencies | 哪步必须在哪步之后 | stage 顺序固定 + 每阶段日志/产物 | ✅ 隐含在流程里 |
| 权衡关系 Tradeoffs | 面积↔速度↔功耗的取舍 | 优化模块（多目标 BO 同时考虑多指标） | ✅ 有（多目标优化） |
| 验证/签核 Verification/Signoff | 结果有没有达标、可不可信 | DRC 报告、时序报告、产物 SHA-256 校验 | ✅ 有（证据链） |
| 因果链 Cause-and-effect | "这个参数导致那个结果" | LearningObservation（参数+结果一起存）+ 轨迹（state→action→reward） | ✅ 部分（存了对应关系，但没显式声明"因果"） |
| 领域词汇/语义 Domain Vocabulary | 统一术语表 | 指标命名（finish__route__drc_errors 等）—— 平台自定义 | ⚠️ 有自己的命名，未对齐 Si2 术语 |
| 本体文件 TTL/OWL | 机器可读的概念定义 | —— 没有 | ❌ 没有 |
| MCP 服务器 | AI 代理查询概念的服务 | —— 没有（有 REST API 但无本体推理） | ❌ 没有 |
| 验证用例 | 标准测试场景 | tests/ + 7 个官方 case + 3 个真实跑通变体 | ✅ 有（自己的用例） |


## 四、逐项详细对照


### ① 数据层：Si2 概念 vs 平台数据

| Si2 概念 | 平台数据在哪 | 说明 |
| --- | --- | --- |
| Net / Cell / Timing Path | runtime 产物：netlist、DEF、openroad_eval.json | ✅ 平台有真实文件，但以"产物文件"形式存在，没有提炼成"结构化知识对象" |
| PDK / Library | task.parameters.platform + inputs.tech | ✅ 明确记录（还有 toolchain_snapshot 存工具版本） |
| Design Intent | TaskSpec（任务单） | ✅ 任务单就是"设计意图"的结构化表达 |


### ② 流程层：工作流 vs 平台运行

| Si2 概念 | 平台对应 | 说明 |
| --- | --- | --- |
| Workflow Steps + Dependencies | Runtime stage_runs + 阶段事件 | ✅ 每次运行都记录每阶段状态（events 表），可重放 |
| Design Tradeoffs | 多目标 BO（同时看面积/时序/功耗） | ✅ 优化模块显式处理权衡 |
| Cause-and-effect | LearningObservation（参数+结果）+ TrajectoryStep（state→action→reward） | ⚠️ 存了"相关关系"，但没有显式"因果模型"或"影响关系图" |


### ③ 语义层：本体 vs 平台词汇

| Si2 概念 | 平台现状 | 说明 |
| --- | --- | --- |
| Domain Vocabulary | 指标名自创（finish__route__drc_errors 等） | ⚠️ 语义清晰但与 Si2 术语未映射 |
| Ontology (TTL/OWL) | 无 | ❌ 没有机器可读本体 |
| MCP Server | 无（只有 REST API） | ❌ 没有供 AI 代理发现概念的接口 |


## 五、本平台符合哪些 / 还差哪些

> **✅ 已经符合的（数据层面基本齐全）：**设计意图、工艺库、网表/单元/时序、工作流步骤与依赖、验证证据、权衡优化、因果对应数据——这些 Si2 关注的核心数据，平台**都在记录**，而且比很多平台更严格（带指纹、带工具版本、带 SHA 校验）。

> **⚠️ 还差的三件事：**
 ① **统一术语映射**：平台自创的指标命名（finish__route__drc_errors）没有对照 Si2 词汇表——需要做一张"平台名 ↔ Si2 名"映射表；
     ② **机器可读本体**：没有 TTL/OWL 本体文件——概念都是散在代码和数据库里，没有正式定义；
     ③ **AI 发现接口**：没有 Si2 那样的 MCP 服务器——AI 代理不能"发现"平台有哪些概念、什么关系。

**一句话评价：**本平台的数据**含量足够**（该记的都记了），缺的是**包装**（没有按行业标准的"语言"组织起来）。像一个人知识很丰富，但还没学会用普通话讲课——内容是有的，形式要对齐。

> **未来展望：**如果按 Si2 标准补齐"术语映射、机器可读本体、AI 发现接口"三件事，平台就能和行业生态"说同一种话"，这也是走向真正开放式平台的关键一步。

