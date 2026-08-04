# OpenROAD Platform — 工作记忆

project_id: openroad-platform
phase: P3：已完成；目标线程继续 P4-P10
current_subgoal: P4 RTLScout 标准插件与 RTL→ORFS 组合工作流
progress: 30%
last_updated: 2026-08-04

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

## 风险与后置门槛

- RTLScout 要求 Python >=3.10，ARM 源码兼容性在 P3 验证。
- AgenticPD 官方仓库未声明许可证；澄清前禁止复制或再分发源码。
- TaiWei 绑定独立 ORFS-Research/OpenROAD commit，不能覆盖内部 2D 工具链。
- ToolchainConfig 已在 P2 接入 ORFS 主链；其余高级 evidence 模块仍需按后续阶段逐项接入，禁止恢复隐式路径假设。

## 活跃约束

- 所有平台产出在 `~/openroad-platform/`；第三方源码缓存不进入 Git。
- 凭据只通过环境或项目外私有配置注入。
- 不修改共享 ORFS/OpenROAD/PDK，不删除 `var/` 原始证据。
- 不 push、不部署；真实 EDA 与 LLM 必须由阶段任务明确授权和预算。

## handoff_anchor

P0 commit `afdca1ef...`；P1 commit `e750370...`；P2 commit `aa7cf0a...`/封存 `6d9d93c`。读取 P2 快照与 `docs/evidence/P3_PLUGIN_ADMISSION.md` 恢复；当前目标已授权连续执行 P3-P10，下一步 P4。
