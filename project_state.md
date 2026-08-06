# OpenROAD Platform — 工作记忆

project_id: openroad-platform
phase: P0-P16 已完成
current_subgoal: 等待 P17 公开服务化、论文扩展与数据规模化规划
progress: P0-P16 + P8-Real 已验收
last_updated: 2026-08-06

## 已确认事实

- 初始安全基线：`e788a362e27318ee6950db1793bd47040e577d49`。
- 主机：ARM64/openEuler 22.03；Python 3.9.9。
- 2D 工具链：ORFS `51ad1231...`、OpenROAD `63ed2e0...`、Yosys `d3e297f...`。
- 平台已有独立 worker、SQLite queue、ProcessGuardian、六阶段 ORFS runner、分析与 Web demo。
- 测试基线：22 tests passed。
- 历史数据库：6 jobs；45 个登记产物大小与 SHA-256 全部匹配。
- 三个官方插件源码已按 `integrations/plugins.lock.json` 固定到 detached commit。
- P1 实现提交：`e750370d0a95c708cb5f9a0ee297dcb0de609db6`；全量 49 tests passed。
- P2 实现提交：`aa7cf0a8b3b2feaa1e16f2a1bad45e612b89beef`；全量 53 tests passed。
- `orfs@1.0.0` 已由新 Runtime 完成真实 Nangate45 RTL→GDS；17 artifacts、7 metrics、29 events。
- P2 GDS：19,572 bytes，SHA-256 `d20ee44ef216af20a896b4a48794d2ee3fdd8de70b7fe8280fb8ae13a59ad1e6`。
- P3 三平台均完成固定 commit 准入：AgenticPD 271 项检查通过，TaiWei CLI 入口通过，RTLScout 系统 Python smoke 明确受 Python/依赖环境阻塞。
- RTLScout 的 Python 3.11/aarch64 主要依赖 wheels 可获得；完整 fake/真实 LLM 尚未宣称成功。
- P4 `rtlscout@1.0.0` 已接入；固定源码 offline fake 真实通过 Verilator/Yosys，生成 RTL SHA `4b4fe1e2...`。
- P4 RTLScout→ORFS 真实组合链成功；Nangate45 GDS 164,296 bytes，SHA `64ea359e...`。
- RTLScout 固定 Spire 实际要求 Python >=3.12；隔离 Python 3.12.4/Verilator 5.040 在 `.tools/`，不进入 Git。
- P5 `agenticpd@1.0.0` 已接入为只生成 proposal 的黑箱；完整参数留证，首版只消费可核验的 CORE_UTILIZATION。
- 同一 RTL、Nangate45、固定工具链真实比较 38%/35%：两次 GDS 成功、DRC=0；候选并非全面更优。
- P6 Campaign member 与 Runtime run 一一映射；按 task_id 幂等恢复、并发上限和 lost→新 Attempt 已通过真实子进程测试。
- P7 NL 只生成验证后的 TaskSpec preview；RepairAction 只有四种数据模板，必须引用证据并受预算停止条件约束。
- P8 历史 blocker 证据保留；P8-Real 新增证据已用官方固定链真实跑通 gcd 3D，GDS/via/QoR/Runtime/API/Web 均通过。
- P9 知识记录强制 verified/ref/SHA/版本上下文；P5 真实经验完成 data-only 回放，错版本和篡改均拒绝。
- P10 Evolve→隔离 Coding→Promotion receipt 闭环完成；真实仓库候选回归 81 passed、基线不变、applied=false；总回归 84。
- P11 EDACraft/ImplCraft 固定官方 commit 接入完成；仅 script generation/backend plan，未执行商业 EDA。
- 电路图/2D GDS/3D GDS 分别使用 Graphviz、KLayout、KLayout+Matplotlib；不手绘版图几何。
- P12 Codex Terra 真实 Spec→RTL→ORFS→GDS 成功；首 run 的 `PDN-0185` 证据保留，最小面积修复子 run 成功。
- P13 ORFS stage 事件进入 Runtime；参数网格、并发、阶段预算剪枝、Top-K 与 LimitedReAct 修复子 run 已实现。
- P14 Evidence RAG v2、observed-only 数据库、NumPy GP/BO、Campaign bridge、真实回灌、Pareto 和离线 shadow policy 已实现。
- P14 真实验收复用 3 个历史 run，新增 7 个 run（5 成功 GDS、2 个失败前像）；两个 `PDN-0185` 均由新修复子 run 成功，失败证据保留。
- P14 同口径结果为 grid/rule 优于本轮 BO；预测与 observed 严格分离，不宣称 BO 必然提升。
- P15 DPLEvolve 固定 commit `96d8c613...`、BSD-3-Clause、365 文件清单；Runtime 只读审计成功，源码未变、未晋级。
- P15 固定 OpenROAD 构建 `dpl_evolve` 成功；完整 from-clean 候选可应用，两份 framework delta 前像失配如实保留。
- P15 尚未执行 evolved candidate 真实 full-flow/liveness/QoR；完成的是受保护迁移 v1。
- P15 轻量可运行性 smoke 已验证 33 条知识索引、三类 prompt、0 warning、四个 Tcl 命令和两个核心编译符号；共享 ORFS 未改变。
- P16 固定公开语料快照有 10 来源、7 benchmark definitions；许可未完成文件级核验的大数据集仅登记 metadata，外部结果不进入 observed。
- P16 LearningCollector 完成 quarantine/verify/admit/reject、幂等和 tenant/project 隔离；共享必须逐条 opt-in。
- P16 BYOK 支持 OpenAI-compatible profile，key 只存内存 8 小时；fake server 覆盖成功、401、429、超时、非法/超大响应、取消、撤销和 owner 隔离。
- P16 T1 支持接受/修改/拒绝，修改后重验参数；T2 默认关闭且真实 10 条 P14 数据为 not_eligible。
- P16 Craft backend-neutral FlowPlan 已映射 ORFS Runtime 与 ImplCraft scriptgen。官方 gcd real run `6111c2de...` 六阶段成功，GDS SHA `2d84c09a...`。
- 当前全量回归：167 passed。

## 架构定版

- 架构草图定义 Agent 产品拓扑。
- Flow Agent/AgenticPD 负责智能实验规划；Workflow Runtime 是进程、资源和持久状态唯一权威。
- 首版插件使用独立环境与版本化子进程 JSON 协议。
- DB 保存状态/关系/摘要；Artifact Store 保存原始大文件。
- retry 创建新 Attempt，不覆盖失败证据。
- Runtime DB schema 与 Event 均固定为 v1；未知版本和未版本化已有 DB 会拒绝打开。
- legacy `jobs` 只能只读投影；不得把 unknown provenance 猜测补齐。
- P1 adapter 面向固定且审查过的代码，不是恶意代码 OS 沙箱。
- ORFS Task 必须固定 RTL size/SHA-256 并先 stage 到 Attempt workspace。
- ORFSRunner 使用 ToolchainConfig 的受控环境；ToolchainSnapshot 是 P2 重放证据。
- SQLite WAL 不能放在当前 GlusterFS 项目盘；live Runtime DB 使用节点本地盘，checkpoint 后保存不可变快照和查询摘要。
- LLM 只产生结构化 proposal；显式确认、确定性校验和 Runtime 提交是执行门。Codex CLI 登录不是平台 API key。
- P13 doomed-run v1 是可解释阶段预算规则，不是学习预测器。
- P14 学习对象均带 PDK、工具链、RTL 指纹、stage、parser version；只有 SHA 校验通过的 Runtime 登记报告可进入 observed KPI。
- P14/P15 所有模型和代码候选只产生提案；Runtime 与人工 promotion gate 分别控制执行和源码晋级。

## 风险与后置门槛

- RTLScout 要求 Python >=3.10，ARM 源码兼容性在 P3 验证。
- AgenticPD 官方仓库未声明许可证；澄清前禁止复制或再分发源码。
- TaiWei 独立 ORFS-Research/OpenROAD 工具链已冻结在私有 `.tools`，不能覆盖内部 2D 工具链。
- ToolchainConfig 已在 P2 接入 ORFS 主链；其余高级 evidence 模块仍需按后续阶段逐项接入，禁止恢复隐式路径假设。

## 活跃约束

- 所有平台产出在 `~/openroad-platform/`；第三方源码缓存不进入 Git。
- 凭据只通过环境或项目外私有配置注入。
- 不修改共享 ORFS/OpenROAD/PDK，不删除 `var/` 原始证据。
- 不 push、不部署；真实 EDA 与 LLM 必须由阶段任务明确授权和预算。

## handoff_anchor

P0 `afdca1e`；P1 `e750370`；P2 `aa7cf0a`/`6d9d93c`；P3 `9269040`；P4 `0892d57`；P5 `38208ae`；P6 `025dc72`；P7 `339f34f`；P8 `00cf2f8`；P9 `c923f55`；P10 `6499d58`；P11-P13 `36c2155`；P14-P15 `55c0bde`；P16 规划 `3bc9829`，实现提交见最新 Git。读取 `memory_snapshots/P16-complete-2026-08-06.md` 恢复。
